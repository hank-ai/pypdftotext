"""Microsoft Azure Document Intelligence API Handler"""

import io
import logging
import os
import threading
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field

from azure.ai.documentintelligence import AnalyzeDocumentLROPoller, DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentPage
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, HttpResponseError
from azure.core.pipeline.transport import RequestsTransport

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


_BUDGET_GRACE_SECONDS: float = 5.0
"""How long await_all waits after signalling cancellation for late callbacks
to fire before synthesizing 'budget exceeded' results. Internal tuning
constant; not configurable."""


def await_all(
    pollers: "Mapping[str, AnalyzeDocumentLROPoller]",
    integrator: "AzureDocIntelIntegrator",
    timeout: float | None,
    *,
    config: PyPdfToTextConfig | None = None,
) -> dict[str, OCRResult]:
    """Collectively wait for many pollers and return one OCRResult per name.

    Registers a done-callback on each poller. The callback only records
    completion (sets an Event when all are done). The coordinating thread
    then harvests results via ``integrator.await_one`` — this must happen
    on the coordinator thread, NOT inside the callback, because Azure SDK
    callbacks fire on the poller's own daemon thread and calling
    ``poller.result()`` from that thread triggers
    ``RuntimeError: cannot join current thread``.

    On timeout: sets ``cancel_event`` on every poller whose result is still
    missing, waits up to ``_BUDGET_GRACE_SECONDS`` for late callbacks
    (lucky-race window), then synthesizes
    ``OCRResult(error="OCR batch budget exceeded ...")`` for any still
    missing.

    Args:
        pollers: dict mapping pdf_name to poller.
        integrator: the integrator whose await_one is used during the
            harvest phase. Its ``_thread_local`` is updated for the LAST
            harvest to complete (matches the "undefined in batch context"
            semantics for AZURE_READ.last_result).
        timeout: overall budget in seconds. None means wait indefinitely.
        config: optional per-batch config override for ``await_one``.

    Returns:
        dict mapping pdf_name to OCRResult. Never raises.
    """
    cfg = config or integrator.config
    results: dict[str, OCRResult] = {}
    done_event = threading.Event()
    lock = threading.Lock()
    completed: set[str] = set()
    total = len(pollers)
    if total == 0:
        return results

    def _on_done(name: str) -> None:
        # CRITICAL: do NOT call poller.result() / await_one() here. This
        # callback fires on the SDK's daemon polling thread, which would
        # deadlock or raise on Thread.join(self). Just signal completion;
        # the coordinator thread does the result extraction.
        with lock:
            if name not in completed:
                completed.add(name)
                if len(completed) == total:
                    done_event.set()

    for name, poller in pollers.items():
        # SDK passes the polling method to the callback; we ignore it via _pm.
        poller.add_done_callback(lambda _pm, n=name: _on_done(n))

    # Phase 1: wait for all callbacks to fire (or timeout).
    if not done_event.wait(timeout):
        # Budget elapsed. Cancel pending pollers and wait briefly for late
        # callbacks (lucky-race window).
        with lock:
            pending_names = [n for n in pollers if n not in completed]
        for n in pending_names:
            polling_method = getattr(pollers[n], "_polling_method", None)
            cancel_event = getattr(polling_method, "cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
        done_event.wait(_BUDGET_GRACE_SECONDS)

    # Phase 2: harvest results on the coordinating thread.
    # Safe to call poller.result() here because we are NOT the daemon
    # polling thread; the thread that just finished polling is the one
    # we're joining, not ourselves.
    with lock:
        completed_snapshot = set(completed)
    for name in pollers:
        if name in completed_snapshot:
            results[name] = integrator.await_one(
                pollers[name],
                pdf_name=name,
                config=cfg,
            )
        else:
            results[name] = OCRResult(
                pdf_name=name,
                config=cfg,
                raw=None,
                pages=[],
                error=(f"OCR batch budget exceeded after {timeout}s (pdf still pending)"),
            )
    return results


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
        """DEPRECATED. Clear the thread-local last_result.

        Replaced by allowing OCRResult instances to manage their own
        lifetime; no explicit reset is needed in the new API.
        """
        warnings.warn(
            "AzureDocIntelIntegrator.reset is deprecated; OCRResult instances "
            "manage their own lifetime. This call clears the thread-local "
            "back-compat slot.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._thread_local.last_result = AnalyzeResult({})
        self._thread_local.ocr_result = None

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
        """Create or retrieve the cached DocumentIntelligenceClient.

        Returns True if a client is available (newly cached or pre-existing),
        False otherwise. The actual client object is stored on
        ``self.client`` for back-compat with any callers that introspect it.
        """
        self.client = client_for(self.config)
        if self.client is None:
            endpoint = os.getenv("AZURE_DOCINTEL_ENDPOINT") or self.config.AZURE_DOCINTEL_ENDPOINT
            logger.error("Failed to obtain Azure OCR Client at endpoint='%s'", endpoint)
            return False
        return True

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
        # Prefer the cached/credential-based client; fall back to self.client so
        # callers that assign a pre-built (or mock) client directly still work.
        client = client_for(cfg) or self.client
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

    def ocr_pages(
        self,
        pdf: bytes,
        pages: list[int],
        pdf_name: str = "",
    ) -> list[str]:
        """Submit pages for OCR and wait for the result.

        Thin wrapper over ``submit`` + ``await_one``. Returns the rendered
        fixed-width page strings, or an empty list on failure (failure is
        also logged at ERROR; see ``await_one`` for details).
        """
        if self.config.AZURE_DOCINTEL_AUTO_CLIENT and self.client is None:
            # Eager create-and-cache via client_for so the legacy `self.client`
            # attribute is populated for any external introspection.
            self.client = client_for(self.config)
        poller = self.submit(pdf, pages, pdf_name=pdf_name)
        if poller is None:
            return []
        result = self.await_one(poller, pdf_name=pdf_name)
        return result.pages

    def handwritten_ratio(
        self,
        page_index: int,
        handwritten_confidence_limit: float | None = None,
    ) -> float:
        """DEPRECATED. Returns the handwritten ratio for the given page from
        this thread's most-recent OCR result.

        Replaced by ``OCRResult.handwritten_ratio(page_index)`` (or
        ``PdfExtract.ocr_result.handwritten_ratio(page_index)``).
        """
        warnings.warn(
            "AzureDocIntelIntegrator.handwritten_ratio is deprecated. Use "
            "OCRResult.handwritten_ratio (e.g. via PdfExtract.ocr_result) "
            "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if handwritten_confidence_limit is not None:
            logger.warning(
                "handwritten_confidence_limit arg is no longer supported; "
                "set config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT instead. "
                "(requested=%.2f, effective=%.2f)",
                handwritten_confidence_limit,
                self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT,
            )
        result = getattr(self._thread_local, "ocr_result", None)
        return result.handwritten_ratio(page_index) if result is not None else 0.0

    def rotation_degrees(self, page_index: int) -> float:
        """DEPRECATED. See OCRResult.rotation_degrees."""
        warnings.warn(
            "AzureDocIntelIntegrator.rotation_degrees is deprecated. Use "
            "OCRResult.rotation_degrees (e.g. via PdfExtract.ocr_result) "
            "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        result = getattr(self._thread_local, "ocr_result", None)
        return result.rotation_degrees(page_index) if result is not None else 0.0

    def page_at_index(self, page_index: int) -> DocumentPage | None:
        """DEPRECATED. See OCRResult.page_at_index."""
        warnings.warn(
            "AzureDocIntelIntegrator.page_at_index is deprecated. Use "
            "OCRResult.page_at_index (e.g. via PdfExtract.ocr_result) "
            "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        result = getattr(self._thread_local, "ocr_result", None)
        return result.page_at_index(page_index) if result is not None else None


AZURE_READ = AzureDocIntelIntegrator()
