"""Structured text extraction extension for pypdf"""

__version__ = "0.0.1"
import io
from pathlib import Path

from pypdf import PdfReader

from .pdf_to_text import extract_structured_text


def _as_pdf_reader(pdf: PdfReader | io.BytesIO | bytes | Path) -> PdfReader:
    """Returns a PdfReader from bytes, io.BytesIO, or Path."""
    if isinstance(pdf, PdfReader):
        return pdf
    if isinstance(pdf, io.BytesIO):
        return PdfReader(pdf, False)
    if isinstance(pdf, Path):
        pdf = pdf.read_bytes()
    return PdfReader(io.BytesIO(pdf), False)


def pdf_text_pages(
    pdf_binary: PdfReader | io.BytesIO | bytes | Path, space_vertically=True, scale_weight=1.25
) -> list[str]:
    """list of multiline strings containing the text on each page of a PDF

    Args:
        pdf_binary: a PdfReader, io.BytesIO, bytes, or Path object
        space_vertically: whether to space text vertically based on y-position
        scale_weight: multiplier for string length when calculating weighted
            average character width.
    """
    pdf_rdr = _as_pdf_reader(pdf_binary)
    return [
        pg_txt
        for pg_txt in map(
            extract_structured_text,
            pdf_rdr.pages,
            [space_vertically] * len(pdf_rdr.pages),
            [scale_weight] * len(pdf_rdr.pages),
        )
    ]


def pdf_text(
    pdf_binary: PdfReader | io.BytesIO | bytes | Path, space_vertically=True, scale_weight=1.25
) -> str:
    """string containing the text from all pages of a PDF

    Args:
        pdf_binary: a PdfReader, io.BytesIO, bytes, or Path object
        space_vertically: whether to space text vertically based on y-position
        scale_weight: multiplier for string length when calculating weighted
            average character width.

    Returns:
        string containing the text from all pages of a PDF
    """
    return "\n".join(pdf_text_pages(pdf_binary, space_vertically, scale_weight))


__all__ = ["extract_structured_text", "pdf_text_pages", "pdf_text"]
