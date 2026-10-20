"""Microsoft Azure Document Intelligence API Handler"""

import io
import logging
import os
import queue
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from azure.ai.documentintelligence import AnalyzeDocumentLROPoller, DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, HttpResponseError

from . import layout
from ._cancellable_polling import CancellablePolling
from ._config import PyPdfToTextConfig
from .ocr_result import OCRResult

logger = logging.getLogger(__name__)


class _Urllib3PoolFullFilter(logging.Filter):
    """Suppress urllib3's 'Connection pool is full' warnings.

    urllib3 logs this whenever a completed connection can't be returned to
    its idle pool (because the pool is at capacity) and gets closed instead.
    For pypdftotext's submit-and-await OCR pattern, every in-flight request
    succeeds regardless — the warning concerns connection-reuse efficiency,
    which is irrelevant for typical batch-once OCR usage. Suppress at module
    import to keep production logs clean.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "Connection pool is full" not in record.getMessage()


logging.getLogger("urllib3.connectionpool").addFilter(_Urllib3PoolFullFilter())


_client_cache: dict[tuple[str, str], DocumentIntelligenceClient] = {}
"""Process-wide cache of DocumentIntelligenceClient instances keyed by
(endpoint, key). Allows credential rotation between calls without leaking
stale clients."""

_client_cache_lock: threading.Lock = threading.Lock()
"""Protects _client_cache against concurrent first-construction."""


def client_for(config: PyPdfToTextConfig) -> DocumentIntelligenceClient | None:
    """Return a DocumentIntelligenceClient for the given config's credentials.

    Clients are cached by (endpoint, key) tuple, so rotating credentials
    transparently produces a new client.

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
            client = DocumentIntelligenceClient(
                endpoint,
                AzureKeyCredential(key),
            )
            _client_cache[cache_key] = client
            logger.info(
                "Cached new Azure OCR client: endpoint='%s'",
                endpoint,
            )
        return client


_CANCEL_GRACE_SECONDS: float = 5.0
"""How long a cancelled poller's thread is joined during harvest before its
result is treated as missing. Internal tuning constant; not configurable."""


def iter_results(
    pollers: dict[str, AnalyzeDocumentLROPoller],
    integrator: "AzureDocIntelIntegrator",
    *,
    config: PyPdfToTextConfig | None = None,
    submitted_at: Mapping[str, float] | None = None,
    batch_timeout: float | None = None,
) -> Iterator[tuple[str, OCRResult]]:
    """Yield ``(pdf_name, OCRResult)`` for each poller as it completes.

    Consumes ``pollers``: each entry is popped from the caller's dict before
    its result is yielded, so the poller (and the HTTP response body it holds)
    is released as soon as it is harvested. The dict is empty when iteration
    ends; callers must not keep a second reference to the pollers.

    Done-callbacks are registered eagerly, at call time, so no completion can
    be missed between this call and the first ``next()``. A callback only
    enqueues the poller's name: it fires on the SDK's daemon polling thread,
    where calling ``poller.result()`` would raise ``RuntimeError: cannot join
    current thread``. All harvesting happens on the thread that iterates.

    Deadlines:
        * Per PDF: ``submitted_at[name]`` (or call time when not provided)
          plus ``cfg.AZURE_DOCINTEL_TIMEOUT``. An expired poller is cancelled
          and harvested with a short grace join; if its final poll had already
          landed the real result is yielded, otherwise an ``OCRResult`` with
          ``error="OCR timeout: ..."``.
        * Batch: call time plus ``batch_timeout`` when not None. On expiry every
          pending poller is cancelled the same way and reported as
          ``"OCR batch budget exceeded ..."`` unless its result had landed.

    Results are yielded in completion order. Never raises for Azure or
    timeout causes (same contract as ``await_one``).

    Args:
        pollers: dict mapping pdf_name to poller. Mutated: emptied as results
            are yielded.
        integrator: the integrator whose ``await_one`` harvests each poller.
        config: optional per-batch config override for ``await_one`` and the
            per-PDF timeout. Defaults to ``integrator.config``.
        submitted_at: ``time.monotonic()`` readings taken right after each
            ``submit``; anchors the per-PDF deadlines.
        batch_timeout: optional wall-clock budget for the whole batch, in
            seconds, anchored at call time.
    """
    cfg = config or integrator.config
    start = time.monotonic()
    ready: queue.Queue[str] = queue.Queue()
    seen: set[str] = set()
    anchors = submitted_at or {}
    deadline = {n: anchors.get(n, start) + cfg.AZURE_DOCINTEL_TIMEOUT for n in pollers}
    batch_deadline = None if batch_timeout is None else start + batch_timeout

    for name, poller in list(pollers.items()):
        # CRITICAL: do NOT call poller.result() / await_one() in the callback.
        # It fires on the SDK's daemon polling thread, which would deadlock or
        # raise on Thread.join(self). Just enqueue; the iterating thread
        # harvests. The SDK passes the polling method to the callback (_pm).
        poller.add_done_callback(lambda _pm, n=name: ready.put(n))

    def _await(poller: AnalyzeDocumentLROPoller, name: str, timeout: float | None) -> OCRResult:
        # await_one never raises for Azure/timeout causes, but a bug in result
        # rendering must not kill the stream and strand every PDF still pending.
        try:
            return integrator.await_one(poller, pdf_name=name, config=cfg, timeout=timeout)
        except Exception as e:
            logger.error(
                "[%s] OCR harvest failed: %s: %s",
                name,
                type(e).__name__,
                e,
                exc_info=logger.getEffectiveLevel() == logging.DEBUG,
            )
            return OCRResult(pdf_name=name, error=f"OCR failed: {type(e).__name__}: {e}")

    def _cancel(name: str) -> None:
        polling_method = getattr(pollers[name], "_polling_method", None)
        cancel_event = getattr(polling_method, "cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()

    def _harvest_expired(name: str, batch_expired: bool, grace_deadline: float) -> OCRResult:
        seen.add(name)
        # Short join, sharing one grace budget across the whole expiry pass: with
        # cancel_event already set the daemon thread exits at its next wake, and
        # if the final poll had already landed we get the real result.
        result = _await(
            pollers.pop(name), name, timeout=max(0.0, grace_deadline - time.monotonic())
        )
        # Only the silent-None timeout from the grace join gets relabeled with the
        # expiry reason. A real Azure failure, an empty result, or a harvest
        # exception that landed in the race window is reported as itself.
        if result.succeeded or (
            result.error is not None and not result.error.startswith("OCR timeout:")
        ):
            return result
        if batch_expired:
            error = f"OCR batch budget exceeded after {batch_timeout}s (pdf still pending)"
        else:
            error = f"OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT={cfg.AZURE_DOCINTEL_TIMEOUT}s"
        return OCRResult(pdf_name=name, error=error)

    def _iter() -> Iterator[tuple[str, OCRResult]]:
        while pollers:
            now = time.monotonic()
            next_deadline = min(deadline[n] for n in pollers)
            if batch_deadline is not None:
                next_deadline = min(next_deadline, batch_deadline)
            try:
                name = ready.get(timeout=max(0.0, next_deadline - now))
            except queue.Empty:
                now = time.monotonic()
                batch_expired = batch_deadline is not None and now >= batch_deadline
                if batch_expired:
                    expired = list(pollers)
                else:
                    expired = [n for n in pollers if deadline[n] <= now]
                # Cancel every expired poller first so their threads wind down
                # concurrently; then join each against one shared grace budget.
                for n in expired:
                    _cancel(n)
                grace_deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
                for n in expired:
                    yield n, _harvest_expired(n, batch_expired, grace_deadline)
                continue
            if name in seen or name not in pollers:
                # Duplicate callback (azure-core fires immediately on an
                # already-done poller AND appends to its list) or already
                # harvested on expiry.
                continue
            seen.add(name)
            # No local may hold the poller across the yield: the consumer applies
            # the result while we are suspended, and the poller owns the full
            # response body. pop() inside the call releases it on return.
            yield name, _await(pollers.pop(name), name, timeout=None)

    return _iter()


def await_all(
    pollers: dict[str, AnalyzeDocumentLROPoller],
    integrator: "AzureDocIntelIntegrator",
    timeout: float | None,
    *,
    config: PyPdfToTextConfig | None = None,
) -> dict[str, OCRResult]:
    """Collect ``iter_results`` into a dict, with ``timeout`` as the batch budget.

    Kept for callers that want every result at once. ``AZURE_DOCINTEL_TIMEOUT``
    still applies to each PDF individually, and ``pollers`` is consumed exactly
    as by ``iter_results``.

    Returns:
        dict mapping pdf_name to OCRResult. Never raises.
    """
    return dict(iter_results(pollers, integrator, config=config, batch_timeout=timeout))


@dataclass
class AzureDocIntelIntegrator:
    """
    Extract text from pdf images via calls to Azure Document Intelligence OCR API.
    """

    config: PyPdfToTextConfig = field(default_factory=PyPdfToTextConfig)
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)

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
        timeout: float | None = None,
    ) -> OCRResult:
        """Wait for the given poller to complete and build an OCRResult.

        On timeout, the poller's CancellablePolling.cancel_event is set so
        the SDK's daemon poll thread terminates cleanly. The returned
        OCRResult carries error=str when the wait timed out, the SDK raised
        an AzureError, or the result had zero pages.

        Args:
            poller: poller previously returned by ``self.submit``.
            pdf_name: identifier for log correlation; carried into the
                returned OCRResult.
            config: optional per-call override. Defaults to ``self.config``.
            timeout: seconds to wait for the poller. Defaults to
                ``cfg.AZURE_DOCINTEL_TIMEOUT``.

        Returns:
            An OCRResult. Never raises on Azure/timeout errors; check
            ``result.succeeded`` and ``result.error``.
        """
        cfg = config or self.config
        prefix = f"[{pdf_name}] " if pdf_name else ""
        raw: AnalyzeResult | None
        error: str | None = None
        effective_timeout = timeout if timeout is not None else cfg.AZURE_DOCINTEL_TIMEOUT
        try:
            raw = poller.result(effective_timeout)
        except HttpResponseError as e:
            raw = None
            error = f"OCR failed: HttpResponseError: {e}"
        except AzureError as e:
            raw = None
            error = f"OCR failed: {type(e).__name__}: {e}"
        if raw is None and error is None:
            # poller.result returned None — the timeout-without-exception case.
            error = f"OCR timeout: poller returned no analyzeResult after {effective_timeout}s"
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
        result = OCRResult(pdf_name=pdf_name, pages=pages, error=error, raw=raw)
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


AZURE_READ = AzureDocIntelIntegrator()
