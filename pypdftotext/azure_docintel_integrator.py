"""Microsoft Azure Document Intelligence API Handler"""

import io
import logging
import os
import threading
import warnings
from dataclasses import dataclass, field

from azure.ai.documentintelligence import AnalyzeDocumentLROPoller, DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentPage
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, HttpResponseError
from azure.core.pipeline.transport import RequestsTransport
from tqdm import tqdm

from . import layout
from ._cancellable_polling import CancellablePolling
from ._config import PyPdfToTextConfig
from .ocr_result import OCRResult

logger = logging.getLogger(__name__)

_client_cache: dict[tuple[str, str], DocumentIntelligenceClient] = {}
"""Process-wide cache of DocumentIntelligenceClient instances keyed by
(endpoint, key). Allows credential rotation between calls without leaking
stale clients."""

_client_cache_lock: threading.Lock = threading.Lock()
"""Protects _client_cache against concurrent first-construction."""


def client_for(config: PyPdfToTextConfig) -> DocumentIntelligenceClient | None:
    """Return a DocumentIntelligenceClient for the given config's credentials.

    Clients are cached by (endpoint, key) tuple, so rotating credentials
    transparently produces a new client. The underlying urllib3 connection
    pool size is taken from ``config.AZURE_CLIENT_POOL_MAXSIZE``.

    Environment variables ``AZURE_DOCINTEL_ENDPOINT`` and
    ``AZURE_DOCINTEL_SUBSCRIPTION_KEY`` take precedence over config fields,
    matching the existing behavior of ``AzureDocIntelIntegrator.create_client``.

    Returns:
        A cached or newly-constructed client, or None if either endpoint or
        key is missing.
    """
    endpoint = os.getenv("AZURE_DOCINTEL_ENDPOINT") or config.AZURE_DOCINTEL_ENDPOINT
    key = os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY") or config.AZURE_DOCINTEL_SUBSCRIPTION_KEY
    if not endpoint or not key:
        return None
    cache_key = (endpoint, key)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            transport = RequestsTransport(
                connection_pool_maxsize=config.AZURE_CLIENT_POOL_MAXSIZE,
            )
            client = DocumentIntelligenceClient(
                endpoint,
                AzureKeyCredential(key),
                transport=transport,
            )
            _client_cache[cache_key] = client
            logger.info(
                "Cached new Azure OCR client: endpoint='%s', pool_maxsize=%s",
                endpoint,
                config.AZURE_CLIENT_POOL_MAXSIZE,
            )
        return client


@dataclass
class AzureDocIntelIntegrator:
    """
    Extract text from pdf images via calls to Azure Document Intelligence OCR API.
    """

    config: PyPdfToTextConfig = field(default_factory=PyPdfToTextConfig)
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)
    _thread_local: threading.local = field(
        default_factory=threading.local,
        init=False,
        repr=False,
    )

    def reset(self):
        """Clear last_result from previous run."""
        self._thread_local.last_result = AnalyzeResult({})

    @property
    def last_result(self) -> AnalyzeResult:
        """DEPRECATED. The raw AnalyzeResult from this thread's most recent
        await_one call, or AnalyzeResult({}) if no OCR has run on this thread.

        Replaced by ``PdfExtract.ocr_result.raw`` (or ``OCRResult.raw``).
        Will be removed in a future minor release.
        """
        warnings.warn(
            "AzureDocIntelIntegrator.last_result is deprecated and will be "
            "removed in a future release. Use PdfExtract.ocr_result.raw or "
            "OCRResult.raw instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(self._thread_local, "last_result", AnalyzeResult({}))

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

    def submit(
        self,
        pdf: bytes,
        pages: list[int],
        pdf_name: str = "",
        *,
        config: PyPdfToTextConfig | None = None,
    ) -> AnalyzeDocumentLROPoller | None:
        """Submit pages for OCR without blocking.

        Returns a poller that can be passed to ``await_one`` (or collected in
        a dict and passed to ``await_all``) for later result retrieval. Uses
        ``CancellablePolling`` internally so the eventual wait can be
        cancelled cleanly on timeout.

        Args:
            pdf: bytes of the PDF to OCR.
            pages: 0-based page indices to OCR. Converted to the SDK's
                1-based ``pages`` string parameter internally.
            pdf_name: optional identifier for log correlation.
            config: optional per-call override. Defaults to ``self.config``.

        Returns:
            A poller, or None when no client could be constructed (missing
            credentials).
        """
        cfg = config or self.config
        client = client_for(cfg)
        if client is None:
            logger.error(
                "[%s] Cannot submit OCR: no client available "
                "(check AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_SUBSCRIPTION_KEY)",
                pdf_name or "<unnamed>",
            )
            return None
        prefix = f"[{pdf_name}] " if pdf_name else ""
        logger.info(
            "%sSubmitting %d pages for OCR (pdf bytes=%d)",
            prefix,
            len(pages),
            len(pdf),
        )
        polling = CancellablePolling(client._config.polling_interval)
        poller = client.begin_analyze_document(
            model_id=cfg.AZURE_DOCINTEL_MODEL,
            body=io.BytesIO(pdf),
            pages=",".join(str(pg + 1) for pg in pages),
            polling=polling,
        )
        return poller

    def await_one(
        self,
        poller: AnalyzeDocumentLROPoller,
        pdf_name: str = "",
        *,
        config: PyPdfToTextConfig | None = None,
    ) -> OCRResult:
        """Wait for the given poller to complete and build an OCRResult.

        On timeout, the poller's CancellablePolling.cancel_event is set so
        the SDK's daemon poll thread terminates cleanly. The returned
        OCRResult carries error=str when the wait timed out, the SDK raised
        an AzureError, or the result had zero pages.

        Updates ``self._thread_local`` for deprecated callers of
        ``self.last_result`` / ``self.handwritten_ratio`` / etc.

        Args:
            poller: poller previously returned by ``self.submit``.
            pdf_name: identifier for log correlation; carried into the
                returned OCRResult.
            config: optional per-call override. Defaults to ``self.config``.

        Returns:
            An OCRResult. Never raises on Azure/timeout errors; check
            ``result.succeeded`` and ``result.error``.
        """
        cfg = config or self.config
        prefix = f"[{pdf_name}] " if pdf_name else ""
        raw: AnalyzeResult | None
        error: str | None = None
        try:
            raw = poller.result(cfg.AZURE_DOCINTEL_TIMEOUT)
        except HttpResponseError as e:
            raw = None
            error = f"OCR failed: HttpResponseError: {e}"
        except AzureError as e:
            raw = None
            error = f"OCR failed: {type(e).__name__}: {e}"
        if raw is None and error is None:
            # poller.result returned None — the timeout-without-exception case.
            error = (
                f"OCR timeout: poller returned no analyzeResult after "
                f"{cfg.AZURE_DOCINTEL_TIMEOUT}s"
            )
        if error is not None:
            # Signal the daemon poll thread to exit. Best-effort: not all
            # mocked pollers have a CancellablePolling, so guard the access.
            polling_method = getattr(poller, "_polling_method", None)
            cancel_event = getattr(polling_method, "cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
        pages: list[str] = []
        if raw is not None and not raw.pages:
            error = error or "OCR failed: empty result (analyzeResult.pages was empty)"
        elif raw is not None:
            pages = [layout.fixed_width_page(doc_page, cfg) for doc_page in raw.pages]
        result = OCRResult(
            pdf_name=pdf_name,
            config=cfg,
            raw=raw,
            pages=pages,
            error=error,
        )
        # Update thread-local for back-compat consumers.
        self._thread_local.last_result = raw if raw is not None else AnalyzeResult({})
        self._thread_local.ocr_result = result
        if result.succeeded:
            logger.info(
                "%sOCR completed: %d pages rendered.",
                prefix,
                len(result.pages),
            )
        else:
            logger.error("%sOCR did not complete: %s", prefix, result.error)
        return result

    def ocr_pages(self, pdf: bytes, pages: list[int], pdf_name: str = "") -> list[str]:
        """
        Read the text from supplied pdf page indices.

        Args:
            pdf: bytes of a pdf file
            pages: list of pdf page indices to OCR
            pdf_name: optional identifier included in log messages for parallel tracing

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
        prefix = f"[{pdf_name}] " if pdf_name else ""
        logger.info("%sSending pdf of %s bytes for OCR of %s pages.", prefix, len(pdf), len(pages))
        poller: AnalyzeDocumentLROPoller = self.client.begin_analyze_document(
            model_id=self.config.AZURE_DOCINTEL_MODEL,
            body=io.BytesIO(pdf),
            pages=",".join(str(pg + 1) for pg in pages),
        )
        # (Temporary: Task 11 swaps ocr_pages to a thin wrapper around submit+await_one.)
        self._thread_local.last_result = poller.result(self.config.AZURE_DOCINTEL_TIMEOUT)
        logger.info(
            "%s%s pages OCR'd successfully. Creating fixed width pages.", prefix, len(pages)
        )
        ocr_pbar = tqdm(
            self._thread_local.last_result.pages,
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
            # Now we'll account for selection marks since prebuilt-layout output replaces
            # checkboxes and the like with ':selected:' or ':unselected:' and includes this
            # unrendered text in span offsets (like an asshole).
            page_length_reduction = sum(
                sel.span.length for sel in _selected_page.selection_marks or []
            )
            # finally, we'll ignore newline chars that occur in the page span
            page_length_reduction += self._thread_local.last_result.content[
                page_start:page_end
            ].count("\n")
            page_length = page_end - page_start - page_length_reduction
            if page_length <= 0:
                # whoops! something's wrong. We should probably throw an exception here, but
                # we'll fail open for now as it fits our use case.
                logger.warning(
                    "Error calculating handwritten ratio for page at index %s:"
                    " page span length reduction (%s) + start (%s) >= end (%s)",
                    page_index,
                    page_length_reduction,
                    page_start,
                    page_end,
                )
                return 0.0
            # lets get the sum of span lengths for all is_handwritten styles with confidences
            # >= our threshold that also occur between page_start and page_end!
            handwritten_length = sum(
                (
                    (span.offset + min(span.length, page_end)) - span.offset
                    for style in (self._thread_local.last_result.styles or [])
                    if style.is_handwritten
                    and style.confidence >= self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT
                    for span in style.spans
                    if page_start <= span.offset < page_end
                ),
                start=0,
            )
            # Guess we'll cap our value at 1.0. We should probably throw and exception here
            # also, but again we'll fail open for now as it suits our use case.
            ratio = handwritten_length / page_length
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
            for page in self._thread_local.last_result.pages
        ):
            return _selected_page
        # page was not OCR'd. Return None.
        return None


AZURE_READ = AzureDocIntelIntegrator()
