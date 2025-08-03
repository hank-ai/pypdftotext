"""Microsoft Azure Document Intelligence API Handler"""

import io
import logging
import os
from dataclasses import dataclass, field

from azure.ai.documentintelligence import DocumentIntelligenceClient, AnalyzeDocumentLROPoller
from azure.ai.documentintelligence.models import AnalyzeResult
from azure.core.credentials import AzureKeyCredential
from tqdm import tqdm

from . import constants, layout

logger = logging.getLogger(__name__)


@dataclass
class AzureDocIntelIntegrator:
    """
    Extract text from pdf images via calls to Azure Document Intelligence OCR API.
    """

    timeout: int = 60
    preserve_vertical_whitespace: bool = False
    font_height_weight: float = 1.0
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)
    last_result: AnalyzeResult = field(default_factory=lambda: AnalyzeResult({}), init=False)
    pbar_position: int | None = field(default=None, init=False, repr=False)

    def create_client(self) -> bool:
        """
        Create an Azure DocumentIntelligenceClient based on current global
        constants and env var settings.

        The following may be set via env var prior to module import OR set via
        the corresponding constants.<ENV_VARIABLE_NAME> global constant after
        module import.

        Constants/Environment Variables:
            AZURE_DOCINTEL_ENDPOINT: Azure Document Intelligence Instance Endpoint URL.
            AZURE_DOCINTEL_SUBSCRIPTION_KEY: Azure Document Intelligence Subscription Key.

        Returns:
            bool: True if client was created successfully. False otherwise.
        """
        endpoint = os.getenv("AZURE_DOCINTEL_ENDPOINT") or constants.AZURE_DOCINTEL_ENDPOINT
        key = (
            os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY")
            or constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY
        )
        if endpoint and key:
            self.client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))
            logger.info("Azure OCR Client Created: endpoint='%s'", endpoint)
            return True
        logger.error("Failed to create Azure OCR Client at endpoint='%s'", endpoint)
        return False

    def reset(self):
        """Clear last_result and last_page_list from previous run."""
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
        if constants.AZURE_DOCINTEL_AUTO_CLIENT and self.client is None:
            self.create_client()
        if self.client is None:
            logger.error(
                "Azure OCR API not available. Did you create a client? Returning empty string."
            )
            return []
        assert self.client is not None
        logger.info("Sending pdf of %s bytes for OCR of %s pages.", len(pdf), len(pages))
        poller: AnalyzeDocumentLROPoller = self.client.begin_analyze_document(
            model_id="prebuilt-read",
            body=io.BytesIO(pdf),
            pages=",".join(str(pg + 1) for pg in pages),
        )
        self.last_result = poller.result(self.timeout)
        logger.info("%s pages OCR'd successfully. Creating fixed width pages.", len(pages))
        ocr_pbar = tqdm(
            self.last_result.pages,
            desc="Processing OCR results...",
            disable=constants.DISABLE_PROGRESS_BAR,
            position=self.pbar_position,
        )
        results: list[str] = [
            layout.fixed_width_page(
                doc_page, self.preserve_vertical_whitespace, self.font_height_weight
            )
            for doc_page in ocr_pbar
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
            handwritten_confidence_limit: the spans of handwritten styles with confidences
                less than this limit will not be considered. Defaults to
                constants.OCR_HANDWRITTEN_CONFIDENCE_LIMIT.

        Returns:
            float: 0.0 if the supplied page index was not OCR'd or of length 0.0. Otherwise
            the ratio of the sum of all handwritten spans on the page to the total page span.
        """
        handwritten_confidence_limit = (
            constants.OCR_HANDWRITTEN_CONFIDENCE_LIMIT
            if handwritten_confidence_limit is None
            else handwritten_confidence_limit
        )
        if any(
            # find the page at the supplied index. otherwise return 0.0 (final return below)
            (_selected_page := page).page_number == page_index + 1
            for page in self.last_result.pages
        ):
            # a page should only have one span, but we'll treat as if there could be more
            # just in case. Get the min offset from all spans as the start and the max
            # offset + length as the page end.
            page_start = min(span.offset for span in _selected_page.spans)
            page_end = max(span.offset + span.length for span in _selected_page.spans)
            if page_end - page_start <= 0:
                # whoops! something's wrong. We should probably throw an exception here, but
                # we'll fail open for now as it fits our use case.
                return 0.0
            # lets get the sum of span lengths for all is_handwritten styles with confidences
            # >= our threshold that also occur between page_start and page_end!
            handwritten_length = sum(
                (
                    (span.offset + min(span.length, page_end)) - span.offset
                    for style in (self.last_result.styles or [])
                    if style.is_handwritten and style.confidence >= handwritten_confidence_limit
                    for span in style.spans
                    if page_start <= span.offset < page_end
                ),
                start=0,
            )
            # Guess we'll cap our value at 1.0. We should probably throw and exception here
            # also, but again we'll fail open for now as it suites our use case.
            return min(handwritten_length / (page_end - page_start), 1.0)

        return 0.0


AZURE_READ = AzureDocIntelIntegrator()
