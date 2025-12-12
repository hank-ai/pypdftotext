"""Batch processing module for efficient parallel OCR of multiple PDFs."""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from pathlib import Path

from azure.core.exceptions import AzureError
from pypdf import PdfReader
from tqdm import tqdm

from ._config import PyPdfToTextConfig
from .azure_docintel_integrator import AzureDocIntelIntegrator
from .header_footer_detection import assign_headers_and_footers
from .pdf_extract import PdfExtract


logger = logging.getLogger(__name__)


class PdfExtractBatch:
    """
    Processes multiple PDFs efficiently with sequential embedded text extraction
    and parallel OCR processing.

    This class maintains full backward compatibility while enabling efficient
    batch processing of multiple PDFs. Embedded text extraction occurs sequentially
    for all PDFs, then all pages needing OCR are submitted in a single batch
    for parallel processing.

    Args:
        pdfs: List or mapping of PDF inputs (str | Path | bytes | io.BytesIO | PdfReader)
        config: Configuration to use for all PDFs (defaults to PyPdfToTextConfig())
        **kwargs: Additional arguments passed to PdfExtract instances

    Usage:
        pdfs = ["file1.pdf", "file2.pdf", Path("file3.pdf")]
        batch = PdfExtractBatch(pdfs)
        pdf_extracts = batch.extract_all()
        # pdf_extracts is a list of PdfExtract objects with text already extracted
    """

    def __init__(
        self,
        pdfs: (
            Sequence[str | Path | bytes | io.BytesIO | PdfReader]
            | Mapping[str, str | Path | bytes | io.BytesIO | PdfReader]
        ),
        config: PyPdfToTextConfig | None = None,
        **kwargs,
    ) -> None:
        if not isinstance(pdfs, (list, dict)):
            raise TypeError(
                f"PdfExtractBatch input should be list or dict, received {type(pdfs)=!r}"
            )
        self.pdfs = (
            pdfs if isinstance(pdfs, dict) else {f"PDF[{i}]": pdf for i, pdf in enumerate(pdfs)}
        )
        self.config = config or PyPdfToTextConfig()
        self.kwargs = kwargs
        logger.info("Starting batch extraction for %s PDFs", len(self.pdfs))
        # Create the pdf extract objects but don't extract text until 'process' is called.
        self.pdf_extracts, self.s3_errors = self._pull_s3_parallel()

    def _pull_s3_parallel(self) -> tuple[dict[str, PdfExtract], dict[str, str]]:
        """Parallelize calls to s3 if present. Returns a dict of extracts that were created
        successfully and a dict of pdf name: s3 uri for failures"""
        s3_uris = {
            k: v for k, v in self.pdfs.items() if isinstance(v, str) and v.startswith("s3://")
        }
        pdf_extracts: dict[str, PdfExtract] = {
            pdf_name: PdfExtract(
                pdf=pdf, config=self.config, **{**self.kwargs, "_batch_mode": True}
            )
            for pdf_name, pdf in self.pdfs.items()
            if pdf_name not in s3_uris or len(s3_uris) == 1
        }
        s3_errors = {}
        if len(s3_uris) <= 1:
            return pdf_extracts, {}
        with ThreadPoolExecutor(
            max_workers=min(len(s3_uris), self.config.MAX_WORKERS)
        ) as executor:

            futures: list[Future[tuple[str, PdfExtract | Exception]]] = []
            for pdf_name, s3_uri in s3_uris.items():
                futures.append(executor.submit(self._extract_from_s3_uri, (pdf_name, s3_uri)))

            # Process results as they complete
            pbar = tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Downloading Objects from S3",
                disable=self.config.DISABLE_PROGRESS_BAR,
                position=self.config.PROGRESS_BAR_POSITION,
                leave=None,
            )

            for i, future in enumerate(pbar):
                pdf_name, extract_or_error = future.result()
                logger.debug("S3 Download Complete: %r (%s/%s)", pdf_name, i, len(s3_uris))
                if isinstance(extract_or_error, Exception):
                    s3_errors[pdf_name] = extract_or_error
                else:
                    pdf_extracts[pdf_name] = extract_or_error
        return pdf_extracts, s3_errors

    def _extract_from_s3_uri(
        self, s3_uri_tuple: tuple[str, str]
    ) -> tuple[str, PdfExtract | Exception]:
        pdf_name, s3_uri = s3_uri_tuple
        try:
            extract = PdfExtract(
                pdf=s3_uri, config=self.config, **{**self.kwargs, "_batch_mode": True}
            )
            return pdf_name, extract
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "S3 Download Error: %r failed, %s",
                pdf_name,
                e,
                exc_info=logger.getEffectiveLevel() == logging.DEBUG,
            )
            return pdf_name, e

    def extract_all(self) -> dict[str, PdfExtract]:
        """Extract embedded text serially, then perform OCR operations in parallel."""
        # Step 1: Perform embedded text extraction
        self.pdf_extracts = self._extract_embedded_text()

        try:
            # Step 2: Perform batch OCR if needed
            self._perform_batch_ocr()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "PdfExtractBatch OCR Error: %e",
                e,
                exc_info=logger.getEffectiveLevel() == logging.DEBUG,
            )
        for extract in self.pdf_extracts.values():
            assign_headers_and_footers(extract.extracted_pages, self.config)
        logger.info("Batch extraction complete for %s PDFs", len(self.pdf_extracts))
        return self.pdf_extracts

    def _extract_embedded_text(self) -> dict[str, PdfExtract]:
        """Create PdfExtract instances with embedded text extraction only."""
        pbar = tqdm(
            self.pdf_extracts.items(),
            desc="Extracting embedded text",
            disable=self.config.DISABLE_PROGRESS_BAR,
            position=self.config.PROGRESS_BAR_POSITION,
            leave=None,
        )

        for i, (pdf_name, pdf) in enumerate(pbar):
            logger.debug("Extracting text from %s (%s/%s)", pdf_name, i, len(self.pdfs))
            # Create PdfExtract with batch mode flag to prevent individual OCR
            pbar.set_postfix_str(pdf_name)
            _ = pdf.extracted_pages

        return self.pdf_extracts

    def _perform_batch_ocr(self) -> dict[str, PdfExtract]:
        """Perform OCR for all collected pages in parallel."""

        ocr_pdfs = {  # get pdfs that need OCR
            pdf_name: extract
            for pdf_name, extract in self.pdf_extracts.items()
            if (
                len(extract.ocr_page_idxs) / len(extract.extracted_pages)
                >= self.config.TRIGGER_OCR_PAGE_RATIO
            )
        }
        if not ocr_pdfs:
            logger.debug(
                "No PDFs met OCR criteria (MIN_LINES_OCR_TRIGGER=%s, TRIGGER_OCR_PAGE_RATIO=%s)",
                self.config.MIN_LINES_OCR_TRIGGER,
                self.config.TRIGGER_OCR_PAGE_RATIO,
            )
            return self.pdf_extracts
        total_pages = sum(len(ext.ocr_page_idxs) for ext in ocr_pdfs.values())
        logger.info("Submitting %s pages across %s PDFs for batch OCR", total_pages, len(ocr_pdfs))

        # Process PDFs in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(
            max_workers=min(len(ocr_pdfs), self.config.MAX_WORKERS)
        ) as executor:

            futures: list[Future[tuple[str, PdfExtract]]] = []
            for pdf_name, pdf_ext in ocr_pdfs.items():
                futures.append(executor.submit(self._ocr_single_pdf, (pdf_name, pdf_ext)))

            # Process results as they complete
            pbar = tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Processing OCR results",
                disable=self.config.DISABLE_PROGRESS_BAR,
                position=self.config.PROGRESS_BAR_POSITION,
                leave=None,
            )

            for i, future in enumerate(pbar):
                pdf_name, _ = future.result()
                logger.debug("OCR complete for %s (%s/%s)", pdf_name, i, len(ocr_pdfs))
        return self.pdf_extracts

    def _ocr_single_pdf(self, pdf_info: tuple[str, PdfExtract]) -> tuple[str, PdfExtract]:
        """OCR a single PDF's pages."""
        pdf_name, pdf_extract = pdf_info
        try:
            logger.debug("Begin OCR for %s", pdf_name)
            azure = AzureDocIntelIntegrator(self.config)
            if self.config.AZURE_DOCINTEL_AUTO_CLIENT and azure.client is None:
                azure.create_client()

            # Run OCR for this extract.
            pdf_extract.ocr(azure)
        except AzureError as azure_error:
            logger.error(
                "PdfExtractBatch Azure Error for %s: %s",
                pdf_name,
                azure_error,
                exc_info=logger.getEffectiveLevel() == logging.DEBUG,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "PdfExtractBatch Error for %s: %s",
                pdf_name,
                e,
                exc_info=logger.getEffectiveLevel() == logging.DEBUG,
            )

        return pdf_name, pdf_extract
