"""Per-OCR-call value type. Replaces session-scoped AzureDocIntelIntegrator.last_result."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from azure.ai.documentintelligence.models import AnalyzeResult, DocumentPage

from ._config import PyPdfToTextConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OCRResult:
    """Result of a single OCR call against Azure Document Intelligence.

    Carries the rendered fixed-width page strings, the underlying SDK
    ``AnalyzeResult`` (or ``None`` on hard failure), and an optional
    human-readable error string. Replaces the session-scoped
    ``AzureDocIntelIntegrator.last_result`` attribute, so two concurrent OCR
    calls cannot alias each other's per-page metadata.

    Attributes:
        pdf_name: Identifier for the source PDF; useful for log correlation.
        config: Snapshot of the PyPdfToTextConfig in effect when the OCR call
            was submitted. Methods like handwritten_ratio read thresholds from
            this snapshot rather than from any current global state.
        raw: The underlying AnalyzeResult returned by the Azure SDK; None when
            the operation failed before producing a result.
         pages: One rendered fixed-width text string per page in the order
             provided by the Azure SDK result (typically increasing
             ``page_number``). Empty when the OCR call failed.
        error: Human-readable failure description prefixed with ``OCR
            <verb>:`` (e.g. ``"OCR timeout: ..."``). None when the call
            succeeded.
    """

    pdf_name: str
    config: PyPdfToTextConfig
    raw: AnalyzeResult | None
    pages: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """True iff no error was recorded and the SDK returned a result with at
        least one page.

        Example:
            >>> from pypdftotext import PyPdfToTextConfig
            >>> from pypdftotext.ocr_result import OCRResult
            >>> OCRResult(pdf_name="x", config=PyPdfToTextConfig(), raw=None,
            ...           pages=[], error="OCR timeout").succeeded
            False
            >>> OCRResult(pdf_name="x", config=PyPdfToTextConfig(), raw=None,
            ...           pages=[]).succeeded
            False
        """
        return self.error is None and self.raw is not None and bool(self.raw.pages)

    def page_at_index(self, page_index: int) -> DocumentPage | None:
        """Return the DocumentPage at the given 0-based index, or None.

        Returns None when the OCR call failed OR when the index is out of
        range relative to ``self.raw.pages``.

        Example:
            >>> from pypdftotext import PyPdfToTextConfig
            >>> from pypdftotext.ocr_result import OCRResult
            >>> OCRResult(pdf_name="x", config=PyPdfToTextConfig(), raw=None,
            ...           pages=[]).page_at_index(0) is None
            True
        """
        if self.raw is None or not self.raw.pages:
            return None
        for page in self.raw.pages:
            if page.page_number == page_index + 1:
                return page
        return None

    def rotation_degrees(self, page_index: int) -> float:
        """Return Azure's reported rotation in degrees for the given page.

        Returns 0.0 when the OCR call failed, the page is missing from the
        result, or the reported angle is below
        ``self.config.MIN_OCR_ROTATION_DEGREES``.
        """
        page = self.page_at_index(page_index)
        if page is None:
            return 0.0
        angle = page.angle or 0.0
        if abs(angle) > self.config.MIN_OCR_ROTATION_DEGREES:
            return angle
        return 0.0

    def handwritten_ratio(self, page_index: int) -> float:
        """Return the ratio of handwritten characters to total characters on
        the given page (0.0 to 1.0).

        Uses ``self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT`` as the minimum
        confidence threshold for counting a span as handwritten. Returns 0.0
        when the OCR call failed or the page has no text.
        """
        if self.raw is None or not self.raw.content:
            return 0.0
        page = self.page_at_index(page_index)
        if page is None or not page.spans:
            return 0.0
        page_start = min(span.offset for span in page.spans)
        page_end = max(span.offset + span.length for span in page.spans)
        # Selection marks like :selected: and :unselected: are encoded in
        # span offsets but not rendered; subtract their lengths.
        length_reduction = sum(sel.span.length for sel in (page.selection_marks or []))
        # And subtract embedded newlines.
        length_reduction += self.raw.content[page_start:page_end].count("\n")
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
        threshold = self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT
        handwritten_length = sum(
            min(span.length, page_end - span.offset)
            for style in (self.raw.styles or [])
            if style.is_handwritten and style.confidence >= threshold
            for span in style.spans
            if page_start <= span.offset < page_end
        )
        ratio = handwritten_length / page_length
        return min(ratio, 1.0)
