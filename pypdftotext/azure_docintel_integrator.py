"""Microsoft Azure Document Intelligence API Handler"""

import io
import logging
import os
from dataclasses import dataclass, field

from azure.ai.documentintelligence import DocumentIntelligenceClient, AnalyzeDocumentLROPoller
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentPage
from azure.core.credentials import AzureKeyCredential
from tqdm import tqdm

from . import layout
from ._config import PyPdfToTextConfig


logger = logging.getLogger(__name__)


@dataclass
class AzureDocIntelIntegrator:
    """
    Extract text from pdf images via calls to Azure Document Intelligence OCR API.
    """

    config: PyPdfToTextConfig = field(default_factory=PyPdfToTextConfig)
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)
    last_result: AnalyzeResult = field(default_factory=lambda: AnalyzeResult({}), init=False)

    def create_client(self) -> bool:
        """
        Create an Azure DocumentIntelligenceClient based on current global
        constants and env var settings.

        The following may be set via env var prior to module import OR set via
        the corresponding self.config.<ENV_VARIABLE_NAME> global constant after
        module import.

        Constants/Environment Variables:
            AZURE_DOCINTEL_ENDPOINT: Azure Document Intelligence Instance Endpoint URL.
            AZURE_DOCINTEL_SUBSCRIPTION_KEY: Azure Document Intelligence Subscription Key.

        Returns:
            bool: True if client was created successfully. False otherwise.
        """
        endpoint = os.getenv("AZURE_DOCINTEL_ENDPOINT") or self.config.AZURE_DOCINTEL_ENDPOINT
        key = (
            os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY")
            or self.config.AZURE_DOCINTEL_SUBSCRIPTION_KEY
        )
        if endpoint and key:
            self.client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))
            logger.info("Azure OCR Client Created: endpoint='%s'", endpoint)
            return True
        logger.error("Failed to create Azure OCR Client at endpoint='%s'", endpoint)
        return False

    def reset(self):
        """Clear last_result from previous run."""
        self.last_result = AnalyzeResult({})

    def ocr_pages(self, pdf: bytes, pages: list[int]) -> list[str]:
        """
        Read the text from supplied pdf page indices.

        Args:
            pdf: bytes of a pdf file
            pages: list of pdf page indices to OCR

        Returns:
            list[str]: list of strings containing structured text extracted
                from each supplied page index.
        """
        if self.config.AZURE_DOCINTEL_AUTO_CLIENT and self.client is None:
            self.create_client()
        if self.client is None:
            logger.error(
                "Azure OCR API not available. Did you create a client? Returning empty list."
            )
            return []
        assert self.client is not None
        logger.info("Sending pdf of %s bytes for OCR of %s pages.", len(pdf), len(pages))
        poller: AnalyzeDocumentLROPoller = self.client.begin_analyze_document(
            model_id=self.config.AZURE_DOCINTEL_MODEL,
            body=io.BytesIO(pdf),
            pages=",".join(str(pg + 1) for pg in pages),
        )
        self.last_result = poller.result(self.config.AZURE_DOCINTEL_TIMEOUT)
        logger.info("%s pages OCR'd successfully. Creating fixed width pages.", len(pages))
        ocr_pbar = tqdm(
            self.last_result.pages,
            desc="Processing OCR results...",
            disable=self.config.DISABLE_PROGRESS_BAR,
            position=self.config.PROGRESS_BAR_POSITION,
            leave=None,
        )
        results: list[str] = [
            layout.fixed_width_page(doc_page, self.config) for doc_page in ocr_pbar
        ]
        return results

    def handwritten_ratio(
        self,
        page_index: int,
        handwritten_confidence_limit: float | None = None,
    ) -> float:
        """
        Given a page *index*, returns the ratio of handwritten to total characters on the page.

        Args:
            page_index: the 0-based index of the page to analyze
            handwritten_confidence_limit: deprecated. use config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT

        Returns:
            float: 0.0 if the supplied page index was not OCR'd or has no text. Otherwise
            the ratio of the sum of all handwritten spans on the page to the total page span.
        """
        if handwritten_confidence_limit is not None:
            logger.warning(
                "Arg 'handwritten_confidence_limit' is no longer supported."
                " Supply the desired value via `self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT`."
                "\nrequested limit: %.2f (from arg)"
                "\neffective limit: %.2f (from self.config)",
                handwritten_confidence_limit,
                self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT,
            )

        if _selected_page := self.page_at_index(page_index):
            # a page should only have one span, but we'll treat as if there could be more
            # just in case. Get the min offset from all spans as the start and the max
            # offset + length as the page end.
            page_start = min(span.offset for span in _selected_page.spans)
            page_end = max(span.offset + span.length for span in _selected_page.spans)
            page_length = page_end - page_start
            if page_length <= 0:
                # whoops! something's wrong. We should probably throw an exception here, but
                # we'll fail open for now as it fits our use case.
                logger.warning(
                    "Error calculating handwritten ratio for page at index %s:"
                    " page span start (%s) >= end (%s)",
                    page_index,
                    page_start,
                    page_end,
                )
                return 0.0
            # lets get the sum of span lengths for all is_handwritten styles with confidences
            # >= our threshold that also occur between page_start and page_end!
            handwritten_length = sum(
                (
                    (span.offset + min(span.length, page_end)) - span.offset
                    for style in (self.last_result.styles or [])
                    if style.is_handwritten
                    and style.confidence >= self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT
                    for span in style.spans
                    if page_start <= span.offset < page_end
                ),
                start=0,
            )
            # Now we'll account for selection marks since prebuilt-layout output replaces
            # checkboxes and the like with ':selected:' or ':unselected:' and includes this
            # unrendered text in span offsets (like an asshole).
            page_length_reduction = sum(
                (
                    10  # 10 chars in ':selected:'
                    if sel.state == "selected"
                    else 12  # 12 chars in ':unselected:'
                )
                for sel in _selected_page.selection_marks or []
            )
            # finally, we'll ignore newline chars that occur in the page span
            page_length_reduction += self.last_result.content[page_start:page_end].count("\n")
            # Guess we'll cap our value at 1.0. We should probably throw and exception here
            # also, but again we'll fail open for now as it suits our use case.
            ratio = handwritten_length / (page_end - page_start - page_length_reduction)
            if ratio > 1.0:
                logger.warning("Handwritten ratio of page index at %s capped at 1.0", page_index)
                return 1.0
            return ratio
        # page was not OCR'd return 0.0 default.
        return 0.0

    def rotation_degrees(self, page_index: int) -> float:
        """
        Given a page *index*, returns the degrees of rotation of the page reported by Azure.

        Args:
            page_index: the 0-based index of the page to analyze

        Returns:
            float: 0.0 if the supplied page index was not OCR'd. Otherwise
                the page's reported rotation in degrees.
        """
        if _selected_page := self.page_at_index(page_index):
            angle = _selected_page.angle or 0.0
            if abs(angle) > self.config.MIN_OCR_ROTATION_DEGREES:
                logger.debug("Page at index %s is rotated %.2f degrees", page_index, angle)
                return angle
        return 0.0

    def page_at_index(self, page_index: int) -> DocumentPage | None:
        """
        Returns the DocumentPage instance having the given page *index* or None.

        Args:
            page_index: the 0-based index of the page to analyze

        Returns:
            DocumentPage | None: None if the supplied page index was not OCR'd.
        """
        if any(
            # find the page at the supplied index and report its angle. otherwise return 0.0.
            (_selected_page := page).page_number == page_index + 1
            for page in self.last_result.pages
        ):
            return _selected_page
        # page was not OCR'd. Return None.
        return None


AZURE_READ = AzureDocIntelIntegrator()
