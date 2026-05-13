"""Batch processing module for efficient parallel OCR of multiple PDFs."""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from pypdf import PdfReader
from tqdm import tqdm

from ._config import PyPdfToTextConfig, PyPdfToTextConfigOverrides
from .azure_docintel_integrator import AZURE_READ, await_all
from .header_footer_detection import assign_headers_and_footers
from .ocr_result import OCRResult
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
        config: a PyPdfToTextConfig instance, a dict of config-field overrides
            (e.g. ``{"DISABLE_OCR": True}``), or None. A dict is converted to
            ``PyPdfToTextConfig(overrides=config)`` automatically. Defaults to
            ``PyPdfToTextConfig()``.
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
        config: PyPdfToTextConfig | PyPdfToTextConfigOverrides | None = None,
        **kwargs,
    ) -> None:
        if not isinstance(pdfs, (list, dict)):
            raise TypeError(
                f"PdfExtractBatch input should be list or dict, received {type(pdfs)=!r}"
            )
        self.pdfs = (
            pdfs if isinstance(pdfs, dict) else {f"PDF[{i}]": pdf for i, pdf in enumerate(pdfs)}
        )
        if kwargs.pop("pdf_name", None) is not None:
            logger.warning(
                "pdf_name is not a valid kwarg for PdfExtractBatch, dumbass — "
                "each PDF's name is derived from its dict key or list index. Ignoring."
            )
        if isinstance(config, dict):
            config = PyPdfToTextConfig(overrides=config)
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
                pdf=pdf,
                config=self.config,
                pdf_name=pdf_name,
                **{**self.kwargs, "_batch_mode": True},
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
                pdf=s3_uri,
                config=self.config,
                pdf_name=pdf_name,
                **{**self.kwargs, "_batch_mode": True},
            )
            return pdf_name, extract
        except Exception as e:  # noqa: BLE001  # batch must survive per-item S3 failures; caller expects Exception in result
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
        except Exception as e:  # noqa: BLE001  # batch must survive OCR failures to return partial results
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
        """Submit all OCR-eligible PDFs, await collectively, apply results."""
        ocr_pdfs = {
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
        logger.info(
            "Submitting %s pages across %s PDFs for batch OCR",
            total_pages,
            len(ocr_pdfs),
        )
        # Phase 1: submit all
        pollers = {}
        for pdf_name, extract in ocr_pdfs.items():
            poller = AZURE_READ.submit(
                extract.body,
                extract.ocr_page_idxs,
                pdf_name=pdf_name,
                config=self.config,
            )
            if poller is not None:
                pollers[pdf_name] = poller
            else:
                # No client available; record failure inline.
                extract._apply_ocr_result(
                    OCRResult(
                        pdf_name=pdf_name,
                        config=self.config,
                        raw=None,
                        pages=[],
                        error="OCR failed: no client available "
                        "(check AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_SUBSCRIPTION_KEY)",
                    )
                )
        # Phase 2: collective wait
        results = await_all(
            pollers,
            AZURE_READ,
            timeout=self.config.AZURE_DOCINTEL_TIMEOUT,
            config=self.config,
        )
        # Phase 3: apply
        for pdf_name, extract in ocr_pdfs.items():
            if pdf_name in results:
                try:
                    extract._apply_ocr_result(results[pdf_name])
                except Exception as e:  # noqa: BLE001  # batch survives per-PDF apply failures
                    logger.error(
                        "PdfExtractBatch apply error for %s: %s",
                        pdf_name,
                        e,
                        exc_info=logger.getEffectiveLevel() == logging.DEBUG,
                    )
        return self.pdf_extracts
