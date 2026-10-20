"""Per-OCR-call transport type plus helpers that read an Azure AnalyzeResult."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from azure.ai.documentintelligence.models import AnalyzeResult, DocumentPage

from ._config import PyPdfToTextConfig

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """Result of a single OCR call against Azure Document Intelligence.

    A transport object from ``AzureDocIntelIntegrator.await_one`` to
    ``PdfExtract._apply_ocr_result``. Carries the rendered fixed-width page
    strings, an optional human-readable error string, and the underlying SDK
    ``AnalyzeResult``.

    Attributes:
        pdf_name: Identifier for the source PDF; useful for log correlation.
        pages: One rendered fixed-width text string per page in the order
            provided by the Azure SDK result (typically increasing
            ``page_number``). Empty when the OCR call failed.
        error: Human-readable failure description prefixed with ``OCR
            <verb>:`` (e.g. ``"OCR timeout: ..."``). None when the call
            succeeded.
        raw: The underlying AnalyzeResult returned by the Azure SDK; None when
            the operation failed before producing a result. Released (set to
            None) by ``PdfExtract._apply_ocr_result`` unless the owning
            ``PdfExtract`` has a ``debug_path``, because the parsed result is
            several times the size of the rendered text and nothing reads it
            after apply.
    """

    pdf_name: str
    pages: list[str] = field(default_factory=list)
    error: str | None = None
    raw: AnalyzeResult | None = None

    @property
    def succeeded(self) -> bool:
        """True iff no error was recorded and at least one page was rendered.

        Does not depend on ``raw``, which may have been released.

        Example:
            >>> from pypdftotext.ocr_result import OCRResult
            >>> OCRResult(pdf_name="x", pages=[], error="OCR timeout").succeeded
            False
            >>> OCRResult(pdf_name="x", pages=[]).succeeded
            False
            >>> OCRResult(pdf_name="x", pages=["text"]).succeeded
            True
        """
        return self.error is None and bool(self.pages)


def page_at_index(raw: AnalyzeResult, page_index: int) -> DocumentPage | None:
    """Return the DocumentPage for the given 0-based page index, or None.

    Matches on Azure's 1-based ``page_number``, so a result that covers only
    a subset of a PDF's pages still resolves by original page index.

    Example:
        >>> from azure.ai.documentintelligence.models import AnalyzeResult
        >>> from pypdftotext.ocr_result import page_at_index
        >>> raw = AnalyzeResult({"pages": [{"pageNumber": 3}]})
        >>> page_at_index(raw, 2).page_number
        3
        >>> page_at_index(raw, 0) is None
        True
    """
    if not raw.pages:
        return None
    for page in raw.pages:
        if page.page_number == page_index + 1:
            return page
    return None


def rotation_degrees(raw: AnalyzeResult, page_index: int, config: PyPdfToTextConfig) -> float:
    """Return Azure's reported rotation in degrees for the given page.

    Returns 0.0 when the page is missing from the result or the reported
    angle is at or below ``config.MIN_OCR_ROTATION_DEGREES``.
    """
    page = page_at_index(raw, page_index)
    if page is None:
        return 0.0
    angle = page.angle or 0.0
    if abs(angle) > config.MIN_OCR_ROTATION_DEGREES:
        return angle
    return 0.0


def handwritten_ratio(raw: AnalyzeResult, page_index: int, config: PyPdfToTextConfig) -> float:
    """Return the ratio of handwritten characters to total characters on
    the given page (0.0 to 1.0).

    Uses ``config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT`` as the minimum
    confidence threshold for counting a span as handwritten. Returns 0.0
    when the page is missing from the result or has no text.
    """
    if not raw.content:
        return 0.0
    page = page_at_index(raw, page_index)
    if page is None or not page.spans:
        return 0.0
    page_start = min(span.offset for span in page.spans)
    page_end = max(span.offset + span.length for span in page.spans)
    # Selection marks like :selected: and :unselected: are encoded in
    # span offsets but not rendered; subtract their lengths.
    length_reduction = sum(sel.span.length for sel in (page.selection_marks or []))
    # And subtract embedded newlines.
    length_reduction += raw.content[page_start:page_end].count("\n")
    page_length = page_end - page_start - length_reduction
    if page_length <= 0:
        logger.warning(
            "Cannot compute handwritten ratio for page index %s: "
            "length reduction (%s) >= span (%s, %s)",
            page_index,
            length_reduction,
            page_start,
            page_end,
        )
        return 0.0
    threshold = config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT
    handwritten_length = sum(
        min(span.length, page_end - span.offset)
        for style in (raw.styles or [])
        if style.is_handwritten and style.confidence >= threshold
        for span in style.spans
        if page_start <= span.offset < page_end
    )
    ratio = handwritten_length / page_length
    return min(ratio, 1.0)
