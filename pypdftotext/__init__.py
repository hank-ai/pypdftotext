"""Extract text from pdf pages from codebehind or Azure OCR as required"""

__version__ = "0.3.2"

import io
import logging
from pathlib import Path

from pypdf import PdfReader

from . import layout
from ._config import constants, PyPdfToTextConfig, PyPdfToTextConfigOverrides
from .azure_docintel_integrator import AZURE_READ
from .batch import PdfExtractBatch
from .extracted_page import ExtractedPage
from .pdf_extract import PdfExtract


logger = logging.getLogger(__name__)


def pdf_text_pages(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    **kwargs,  # prevent errors due to bad args in upstream config dicts
) -> list[str]:
    """
    Extract text from PDF pages and return as a list of multiline strings.

    Uses PDF code-behind by default. Triggers Azure OCR if the fraction
    of pages having fewer than `MIN_LINES_OCR_TRIGGER` lines is greater than
    or equal to `TRIGGER_OCR_PAGE_RATIO`.

    NOTE: kwargs will accept lowercase variants of all `PyPdfToTextConfig` members.

    NOTE: *NOT* thread safe. Use `pdf_extract/PdfExtract` for thread safe operations.

    Args:
        pdf_reader (PdfReader | io.BytesIO | bytes): Pdf with pages to extract
            as an already instantiated PdfReader or the raw pdf bytes or BytesIO.
        debug_path (Path | None, optional): Path to write pypdf debug files to.
            Defaults to None.
        page_indices (list[int] | None): if provided, only extract text from
            the listed page indices. Default is None (extract all pages).

    Returns:
        list[str]: a string of text extracted for each page
    """
    config = PyPdfToTextConfig(
        overrides=PyPdfToTextConfigOverrides(
            **{
                _upper: kwargs.pop(k)
                for k in list(kwargs.keys())
                if hasattr(constants, _upper := k.upper())
            }
        ),
    )
    pdf_extract = PdfExtract(
        pdf_reader,
        config,
        debug_path=debug_path,
        azure=AZURE_READ,
    )
    page_indices = page_indices or list(range(len(pdf_extract.extracted_pages)))
    return [pg.text for idx, pg in enumerate(pdf_extract.extracted_pages) if idx in page_indices]


def pdf_text_page_lines(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    **kwargs,  # prevent errors due to bad args in upstream config dicts
) -> list[list[str]]:
    """
    Extract text from PDF pages and return as a list of lines for each page.

    Uses PDF code-behind by default. Triggers Azure OCR if the fraction
    of pages having fewer than `MIN_LINES_OCR_TRIGGER` lines is greater than
    or equal to `TRIGGER_OCR_PAGE_RATIO`.

    NOTE: kwargs will accept lowercase variants of all `PyPdfToTextConfig` members.

    NOTE: *NOT* thread safe. Use `pdf_extract/PdfExtract` for thread safe operations.

    Args:
        pdf_reader (PdfReader | io.BytesIO | bytes): Pdf with pages to extract
            as an already instantiated PdfReader or the raw pdf bytes or BytesIO.
        debug_path (Path | None, optional): Path to write pypdf debug files to.
            Defaults to None.
        page_indices (list[int] | None): if provided, only extract text from
            the listed page indices. Default is None (extract all pages).

    Returns:
        list[list[str]]: a list of lines of text extracted for each page
    """
    return [
        pg.splitlines() for pg in pdf_text_pages(pdf_reader, debug_path, page_indices, **kwargs)
    ]


__all__ = [
    "constants",
    "layout",
    "AZURE_READ",
    "pdf_text_pages",
    "pdf_text_page_lines",
    "ExtractedPage",
    "PyPdfToTextConfig",
    "PyPdfToTextConfigOverrides",
    "PdfExtract",
    "PdfExtractBatch",
]
