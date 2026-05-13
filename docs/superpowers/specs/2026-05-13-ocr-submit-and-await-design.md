# OCR Submit-and-Await: Design Spec

**Date:** 2026-05-13
**Status:** Draft (pre-implementation)
**Scope:** Internal restructure of OCR plumbing in `pypdftotext`. Public API surface (`PdfExtract`, `PdfExtractBatch`, `ExtractedPage`, `pdf_text_pages`, `pdf_text_page_lines`, `AZURE_READ`) remains backward-compatible. Adds `OCRResult` to the public API and one new field each to `ExtractedPage` and `PyPdfToTextConfig`.

---

## TL;DR

- Fix the original bug: `poller.result(timeout)` returns `None` on timeout, downstream code accesses `None.pages` and raises `AttributeError`. The misleading `INFO "OCR'd successfully"` log fires regardless of result validity.
- Replace `AzureDocIntelIntegrator.last_result` (session-scoped mutable state) with an immutable per-call `OCRResult` value type. Eliminates a class of cross-thread contamination (pattern P4 below).
- Restructure the batch path from "thread-pool-per-PDF + blocking poller" to "submit all → collective await". Removes one layer of threading and consolidates connection pooling behind a single shared `DocumentIntelligenceClient`.
- Cancel pending pollers cleanly on timeout via a `CancellablePolling` subclass — no stranded daemon threads.
- Soft-fail on OCR errors: log, set `ExtractedPage.ocr_error`, never raise. Public API gains no new exception types.
- Back-compat preserved: deprecated `AZURE_READ.last_result` and the integrator's `handwritten_ratio` / `rotation_degrees` / `page_at_index` continue to work via `threading.local()`, with `DeprecationWarning`.

---

## Background

### Original bug

[`AzureDocIntelIntegrator.ocr_pages`](../../../pypdftotext/azure_docintel_integrator.py) at line 89:

```python
self.last_result = poller.result(self.config.AZURE_DOCINTEL_TIMEOUT)
logger.info("%s%s pages OCR'd successfully. Creating fixed width pages.", prefix, len(pages))
ocr_pbar = tqdm(self.last_result.pages, ...)
```

The Azure SDK's `LROPoller.result(timeout)` does **not** raise on timeout. From `azure.core.polling._poller.LROPoller`:

```python
def result(self, timeout=None):
    self.wait(timeout)
    return self._polling_method.resource()

def wait(self, timeout=None):
    self._thread.join(timeout=timeout)
    try:
        raise self._exception
    except TypeError:   # _exception is None when the thread is still running
        pass
```

`Thread.join(timeout)` returns silently when the timeout elapses. The polling daemon thread continues running. `self._exception` is `None` so the `raise None` raises `TypeError`, which is swallowed. Control proceeds to `_polling_method.resource()`, which calls the deserialization callback on the most recent polled response (typically `{"status": "running", ...}` with no `analyzeResult` key). `_deserialize(AnalyzeResult, None)` returns `None`.

Result: `self.last_result = None`, success log fires, then `self.last_result.pages` raises `AttributeError: 'NoneType' object has no attribute 'pages'`.

### Cross-thread contamination patterns

| Pattern | Description | Currently safe? |
| --- | --- | --- |
| **P1** | Multi-process (Celery, multiprocessing). One `PdfExtract` per process. | ✅ |
| **P2** | Multi-thread. One `PdfExtract` per thread (each thread builds its own integrator via `_extract_pages` line 304). | ✅ |
| **P3** | Multi-thread sharing one `PdfExtract`. | ❌ — `PdfReader`/`PdfWriter` not thread-safe. Documented. **Out of scope for this rewrite; deferred to a follow-up spec.** |
| **P4** | Multi-thread sharing one `AzureDocIntelIntegrator` (e.g. passing `AZURE_READ` to many `PdfExtract` instances across threads). | ❌ — `last_result` is overwritten; `handwritten_ratio(idx)` returns data from the most recently completed OCR, for the wrong PDF. **Silent corruption.** This rewrite fixes P4 by construction. |

### Threading inefficiency

`PdfExtractBatch._perform_batch_ocr` spawns `MAX_WORKERS=10` worker threads. Each worker calls `poller.result(timeout)`, which itself spawns a daemon polling thread inside the SDK. So `~20` threads per batch where `~10` are doing nothing but `Thread.join()`.

The poller already encapsulates the asynchronous wait. The user-level worker thread adds no value — it exists solely because `poller.result()` is a blocking interface.

---

## Goals

1. Fix the original bug: no silent `None` results, no misleading success log, no `AttributeError` downstream.
2. Eliminate P4 contamination structurally — no session-scoped mutable result state on the integrator.
3. Reduce the batch path's thread count by ~50% by replacing worker-thread-per-PDF with collective wait.
4. Consolidate `DocumentIntelligenceClient` instances behind credential-keyed caching, sized via config.
5. Clean up pending pollers on timeout — no stranded daemon threads.
6. Preserve full backward compatibility for currently-public API surfaces, with deprecation warnings on retired surfaces.

## Non-goals

- Switching to `asyncio` / `AsyncDocumentIntelligenceClient`. Conflict with caller event loops makes this riskier than the value it adds; sync SDK throughout.
- Continuation-token-based crash recovery. Feasible (`poller.continuation_token()` exposed by SDK) but out of scope for this effort.
- Cancelling Azure-side processing. The Document Intelligence API has no cancel endpoint; we can only stop *waiting* locally.
- P3 thread-safety. Tracked separately; revisit after this rewrite lands.
- New exception types in the public API. Soft-fail with `ocr_error` attribute is the chosen failure model.

---

## Design

### Architecture

**Before:**

```text
PdfExtract.ocr()
  └─> azure.ocr_pages(pdf, pages)
        └─> blocks on poller.result(timeout)
              └─> writes azure.last_result  ← mutable session state, P4 footgun

PdfExtractBatch._perform_batch_ocr()
  └─> ThreadPoolExecutor(MAX_WORKERS)
        └─> for each PDF: new AzureDocIntelIntegrator(), new client, .ocr_pages(...)
              └─> N×2 threads (worker + SDK poller daemon)
```

**After:**

```text
azure_docintel_integrator.py
  ├── AzureDocIntelIntegrator       (stateless re: results; holds default config + thread-local back-compat)
  │     ├── submit(pdf, pages, *, config=None) -> AnalyzeDocumentLROPoller | None    [non-blocking]
  │     └── await_one(poller, *, config=None) -> OCRResult                           [blocking, per-PDF]
  ├── OCRResult                     (new public dataclass — pages, raw AnalyzeResult, error)
  ├── CancellablePolling            (LROBasePolling subclass with a cancel Event)
  ├── AZURE_READ                    (canonical shared instance; existing public export)
  └── module-level helpers
        ├── client_for(config) -> DocumentIntelligenceClient | None    [cached by (endpoint, key)]
        └── await_all(pollers, integrator, timeout, *, config=None) -> dict[str, OCRResult]

pdf_extract.py
  └── PdfExtract.ocr(azure=AZURE_READ)
        ├── poller = azure.submit(self.body, self.ocr_page_idxs, pdf_name=…, config=self.config)
        ├── result = azure.await_one(poller, pdf_name=…, config=self.config)
        └── self._apply_ocr_result(result)

batch.py
  └── PdfExtractBatch._perform_batch_ocr()
        ├── pollers = {name: AZURE_READ.submit(ext.body, ext.ocr_page_idxs, pdf_name=name, config=self.config)
        │              for name, ext in ocr_pdfs.items()}
        ├── results = await_all(pollers, AZURE_READ, timeout=self.config.AZURE_DOCINTEL_TIMEOUT,
        │                       config=self.config)
        └── for name, ext in ocr_pdfs.items(): ext._apply_ocr_result(results[name])
                                ↑ no worker thread pool; one SDK daemon poll thread per poller
```

### Components

#### `OCRResult` — new public dataclass

```python
@dataclass(frozen=True)
class OCRResult:
    """Value type returned by a single OCR call. Replaces session-scoped last_result."""
    pdf_name: str
    config: PyPdfToTextConfig             # config snapshot used at OCR time
    raw: AnalyzeResult | None             # underlying SDK result; None on hard failure
    pages: list[str]                      # rendered fixed-width text, one per submitted page index
    error: str | None = None              # human-readable failure reason; None on success

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.raw is not None and bool(self.raw.pages)

    def handwritten_ratio(self, page_index: int) -> float: ...
    def rotation_degrees(self, page_index: int) -> float: ...
    def page_at_index(self, page_index: int) -> DocumentPage | None: ...
```

The three methods are relocated from `AzureDocIntelIntegrator` with identical semantics. They read `self.config` for thresholds (`OCR_HANDWRITTEN_CONFIDENCE_LIMIT`, `MIN_OCR_ROTATION_DEGREES`).

`OCRResult` is added to `pypdftotext/__init__.py`'s `__all__`.

#### `AzureDocIntelIntegrator` — new methods, deprecated old surface

```python
class AzureDocIntelIntegrator:
    config: PyPdfToTextConfig
    client: DocumentIntelligenceClient | None   # legacy: prefer client_for(config)
    _thread_local: threading.local              # NEW: per-thread last_result/ocr_result for back-compat

    # === New API ===
    def submit(self, pdf, pages, pdf_name="", *, config=None) -> AnalyzeDocumentLROPoller | None:
        """Submit pages for OCR. Returns a poller, or None if no client is configured.
        Passes a CancellablePolling instance so await_one can cancel cleanly."""

    def await_one(self, poller, pdf_name="", *, config=None) -> OCRResult:
        """Wait for one poller to complete (cfg.AZURE_DOCINTEL_TIMEOUT). On timeout:
        sets the poller's cancel event, builds OCRResult with error set, updates
        self._thread_local for back-compat."""

    def ocr_pages(self, pdf, pages, pdf_name="") -> list[str]:
        """Thin wrapper: submit + await_one. Signature unchanged from current."""

    # === Deprecated (emit DeprecationWarning; back-compat via _thread_local) ===
    @property
    def last_result(self) -> AnalyzeResult: ...
    def handwritten_ratio(self, page_index, handwritten_confidence_limit=None) -> float: ...
    def rotation_degrees(self, page_index) -> float: ...
    def page_at_index(self, page_index) -> DocumentPage | None: ...
    def reset(self) -> None: ...   # clears thread-local
```

#### Module-level helpers

```python
_client_cache: dict[tuple[str, str], DocumentIntelligenceClient] = {}
_client_cache_lock: threading.Lock = threading.Lock()

def client_for(config: PyPdfToTextConfig) -> DocumentIntelligenceClient | None:
    """Return a cached client for (endpoint, key). Builds a new one with a sized
    urllib3 pool (config.AZURE_CLIENT_POOL_MAXSIZE) if not cached. Returns None
    if endpoint or key is missing."""

_BUDGET_GRACE_SECONDS: float = 5.0
"""How long await_all waits after signalling cancellation for late callbacks to fire
before synthesizing 'budget exceeded' results. Internal tuning constant; not configurable."""

def await_all(
    pollers: Mapping[str, AnalyzeDocumentLROPoller],
    integrator: AzureDocIntelIntegrator,
    timeout: float | None,
    *,
    config: PyPdfToTextConfig | None = None,
) -> dict[str, OCRResult]:
    """Collective wait via add_done_callback + threading.Event.
    On budget exceeded: set cancel_event on remaining pollers, wait
    _BUDGET_GRACE_SECONDS for late callbacks, synthesize 'OCR batch budget exceeded'
    for any still missing."""
```

#### `CancellablePolling` — subclass of `LROBasePolling`

```python
class CancellablePolling(LROBasePolling):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_event = threading.Event()

    def _delay(self) -> None:
        # Interruptible sleep
        self.cancel_event.wait(self._extract_delay())

    def finished(self) -> bool:
        return self.cancel_event.is_set() or super().finished()
```

Passed via `client.begin_analyze_document(..., polling=CancellablePolling(lro_delay))`. Brittleness note: depends on `_delay`, `_extract_delay`, and `finished` being stable in `LROBasePolling`. A canary test in `tests/test_cancellable_polling.py` will fail loudly if the SDK refactors these.

#### `ExtractedPage` — one new optional field

```python
@dataclass
class ExtractedPage:
    # ... existing fields unchanged ...
    ocr_error: str | None = None   # NEW: set when OCR was attempted and failed; None otherwise
```

On failure, `text` stays `""`, `source` stays `"embedded"`, `azure_page` stays `None`. Callers can detect failure via `if ext_pg.ocr_error: ...`.

#### `PdfExtract` — new attribute, new internal apply

```python
class PdfExtract:
    ocr_result: OCRResult | None = None   # NEW public attribute, set after ocr() runs

    def ocr(self, azure: AzureDocIntelIntegrator = AZURE_READ):
        """Default integrator is the canonical singleton."""
        if not self._should_ocr(): return
        poller = azure.submit(self.body, self.ocr_page_idxs, pdf_name=self.pdf_name, config=self.config)
        if poller is None: return                  # no client configured — same as today
        result = azure.await_one(poller, pdf_name=self.pdf_name, config=self.config)
        self._apply_ocr_result(result)

    def _apply_ocr_result(self, result: OCRResult) -> None:
        """Internal: stash result, update ExtractedPages, apply rotations, set ocr_error on failure."""
```

The old `self._azure.config = self.config` mutation (currently at line 302-303) is removed — config is passed per-call.

#### `PdfExtractBatch._perform_batch_ocr` — three serial phases

```python
def _perform_batch_ocr(self):
    ocr_pdfs = {name: ext for name, ext in self.pdf_extracts.items() if <OCR triggered>}
    if not ocr_pdfs: return
    # Phase 1: submit all
    pollers = {
        name: AZURE_READ.submit(ext.body, ext.ocr_page_idxs, pdf_name=name, config=self.config)
        for name, ext in ocr_pdfs.items()
    }
    # Drop entries where submit returned None (no client)
    pollers = {name: p for name, p in pollers.items() if p is not None}
    # Phase 2: collective wait
    results = await_all(pollers, AZURE_READ, timeout=self.config.AZURE_DOCINTEL_TIMEOUT, config=self.config)
    # Phase 3: apply
    for name, ext in ocr_pdfs.items():
        if name in results:
            ext._apply_ocr_result(results[name])
```

The existing `ThreadPoolExecutor` for OCR is removed. The `ThreadPoolExecutor` for `_pull_s3_parallel` remains untouched.

#### `_config.py` — one new field

```python
AZURE_CLIENT_POOL_MAXSIZE: int = 20
"""urllib3 connection pool size for the shared DocumentIntelligenceClient.
Default sized for MAX_WORKERS=10 with headroom. Increase if running larger batches."""
```

### Data flow

#### Single-PDF OCR

```text
caller → PdfExtract.ocr(AZURE_READ)
  → AZURE_READ.submit(pdf, pages, config=self.config)
      → client = client_for(config)            [cached by (endpoint, key)]
      → poller = client.begin_analyze_document(..., polling=CancellablePolling(...))
      → return poller
  → AZURE_READ.await_one(poller, config=self.config)
      → try: raw = poller.result(config.AZURE_DOCINTEL_TIMEOUT)
      → on timeout: poller._polling_method.cancel_event.set(); raw = None; error = "OCR timeout: ..."
      → on AzureError: error = "OCR failed: {type}: {msg}"
      → build OCRResult(pdf_name, config, raw, pages, error)
      → _thread_local.last_result = raw or AnalyzeResult({})
      → _thread_local.ocr_result = result
      → return result
  → PdfExtract._apply_ocr_result(result)
      → if result.succeeded: populate pages, source="OCR", handwritten_ratio, azure_page
      → else: set ocr_error on every affected page
      → self.ocr_result = result
```

A caller doing `pdf_text_pages(...)` then `AZURE_READ.last_result` (deprecated) sees the raw result for their thread, matching today's behavior exactly.

#### Batch OCR

```text
PdfExtractBatch.extract_all()
  → _extract_embedded_text()    [serial, _batch_mode=True suppresses per-PDF OCR]
  → _perform_batch_ocr()
      → Phase 1: serial submit, one HTTP POST per PDF → dict[name, poller]
      → Phase 2: await_all(pollers, AZURE_READ, timeout, config)
          → for each poller: poller.add_done_callback(lambda p, n=name: _on_done(n, p))
          → wait on threading.Event (set when all callbacks fire OR overall timeout)
          → if timeout: for each remaining poller: cancel_event.set()
          → wait _BUDGET_GRACE_SECONDS for late callbacks
          → for any still missing: synthesize OCRResult(error="OCR batch budget exceeded...")
          → return dict[name, OCRResult]
      → Phase 3: serial apply, ext._apply_ocr_result(results[name])
```

No worker thread pool. SDK daemon poll threads do the actual waiting; one fan-in `Event` consolidates the wait.

### Failure semantics

| Failure | Behavior |
| --- | --- |
| OCR timeout (`poller.result` returns None) | `OCRResult(error="OCR timeout: poller returned no analyzeResult after Ns")`. ExtractedPage.ocr_error set on every affected page. No exception. |
| Azure HTTP / `AzureError` | `OCRResult(error="OCR failed: {type}: {msg}")`. Same downstream as timeout. |
| Response succeeded but `pages` empty | `OCRResult(error="OCR failed: empty result (analyzeResult.pages was empty)")` |
| No client configured (missing endpoint/key) | `submit()` returns `None`. `ocr()` early-returns. ocr_error *not* set (same as today's "never attempted"). Logged at ERROR. |
| Batch: overall timeout with N pending | Pending pollers' `cancel_event.set()` → poll loop exits within ~ms → callback fires (may capture lucky-race late result) or `OCRResult(error="OCR batch budget exceeded after Ns")` is synthesized. Daemon threads terminate cleanly. |
| Programmer error (wrong types, malformed pages list) | Raises `TypeError` / `ValueError`. Not caught. |
| Deprecated API access | `DeprecationWarning` once per call site (Python's default filter dedupes by `(module, lineno)`). |

**No new exception types are exported.** Callers detect failure via `ocr_result.error is not None` or `ext_pg.ocr_error is not None`.

#### Error string conventions

`OCRResult.error` and `ExtractedPage.ocr_error` use the grammar `OCR <verb>: <detail>` so log/structured filtering is straightforward:

```text
"OCR timeout: poller returned no analyzeResult after 60s"
"OCR failed: HttpResponseError(404) Resource not found"
"OCR failed: AzureError <msg>"
"OCR failed: empty result (analyzeResult.pages was empty)"
"OCR batch budget exceeded after 60s (pdf still pending)"
```

#### Logging changes

Today's misleading INFO line ("X pages OCR'd successfully") fires unconditionally after `poller.result()` returns. Replaced with conditional logging:

```python
if result.succeeded:
    logger.info("[%s] OCR completed: %d pages rendered.", pdf_name, len(result.pages))
else:
    logger.error("[%s] OCR did not complete: %s", pdf_name, result.error)
```

The "Creating fixed width pages" phrasing is dropped — implementation detail, and misleading because the pages are already built when the log fires.

### Back-compat and deprecation plan

`AZURE_READ` and the integrator's public methods are exported in `__all__` and used by `pdf_text_pages` (which the docstring warns is "NOT thread safe"). The deprecation surfaces:

| Surface | Replacement | Back-compat behavior |
| --- | --- | --- |
| `AZURE_READ.last_result` (attribute) | `PdfExtract.ocr_result.raw` or `OCRResult.raw` | `@property` backed by `threading.local()`. Each thread sees its own most-recent OCR's raw `AnalyzeResult`. Strict improvement over today's race for multi-threaded callers. Empty `AnalyzeResult({})` sentinel returned when no OCR has run on this thread. Emits `DeprecationWarning`. |
| `AzureDocIntelIntegrator.handwritten_ratio(idx)` | `OCRResult.handwritten_ratio(idx)` | Reads thread-local `ocr_result` and delegates. Emits `DeprecationWarning`. |
| `AzureDocIntelIntegrator.rotation_degrees(idx)` | `OCRResult.rotation_degrees(idx)` | Same. |
| `AzureDocIntelIntegrator.page_at_index(idx)` | `OCRResult.page_at_index(idx)` | Same. |
| `AzureDocIntelIntegrator.reset()` | (no replacement needed) | Clears thread-local. Emits `DeprecationWarning`. |

`AZURE_READ` itself is **not** deprecated — it's promoted to the canonical shared integrator. `pdf_text_pages` continues to use it as today.

Removal target: one minor version after the rewrite ships (e.g. 0.4 introduces deprecation, 0.5 removes the back-compat layer). Not part of this spec; raised at release-planning time.

### `ExtractedPage.source` semantics

`source` stays `Literal["embedded", "OCR"]`. On OCR failure, source remains `"embedded"` (the text *is* empty embedded content — OCR didn't succeed). Callers detect failure via `ocr_error`. Rationale: keeping the Literal narrow avoids forcing every consumer of `source` to handle a new variant.

### Public API delta (additive only)

- `pypdftotext.OCRResult` — new class
- `pypdftotext.PdfExtract.ocr_result: OCRResult | None` — new attribute
- `pypdftotext.ExtractedPage.ocr_error: str | None` — new field (default `None`, preserves equality semantics for fixtures)
- `pypdftotext.PyPdfToTextConfig.AZURE_CLIENT_POOL_MAXSIZE: int = 20` — new setting
- `PdfExtract.ocr(azure)` — default for `azure` parameter changes from "construct a fresh integrator" to `AZURE_READ`. Behavior identical for callers passing explicit `azure=`.

No removals. No type signature changes on existing public methods.

---

## Testing strategy

Coverage-based, not case-enumerated: tests aim to exercise distinct behaviors and branches, with parametrization where variants share assertion shape. Two named regression tests are kept dedicated (timeout-None and batch-budget-exceeded) because they document specific bugs/edge cases the rewrite fixes.

### Doctest (in docstrings)

- `OCRResult.succeeded`
- `OCRResult.page_at_index`

### New `tests/test_azure_docintel_integrator.py`

- `test_submit_returns_poller_when_client_available` — happy-path submit; mocked client returns `Mock(spec=AnalyzeDocumentLROPoller)`.
- `test_await_one_success` — fixture `AnalyzeResult` → `OCRResult.succeeded is True`, pages rendered. Thread-local update is implicit (verified separately).
- **`test_await_one_timeout_yields_error_result`** — regression test for the original bug. Mock `poller.result()` to return `None`. Assert `OCRResult.error.startswith("OCR timeout")`, `raw is None`, `pages == []`.
- `test_await_one_error_paths` *(parametrized)* — `HttpResponseError` raised by `poller.result()`, `AnalyzeResult(pages=[])`. Each variant asserts the appropriate `OCRResult.error` prefix.
- `test_thread_local_isolation_across_threads` — two `threading.Thread`s with distinct mocked results; each sees its own via the deprecated `AZURE_READ.last_result`. Regression test for P4 contamination fix.
- `test_deprecation_warnings_and_back_compat` — accesses each deprecated surface (`last_result`, `handwritten_ratio`, `rotation_degrees`, `page_at_index`, `reset`); asserts `DeprecationWarning` is emitted AND that the return value matches the equivalent `OCRResult` method (back-compat shim correctness).
- `test_client_for` *(parametrized)* — caching by `(endpoint, key)` (same → identical, different → new), missing creds → `None`, `AZURE_CLIENT_POOL_MAXSIZE` is forwarded to transport construction.

### New `tests/test_cancellable_polling.py` (SDK canary)

- `test_cancel_event_terminates_polling` — construct `CancellablePolling`, call `_delay()` in a thread, `cancel_event.set()` from main, assert thread returns within 100ms AND `finished()` returns `True` afterward. Doubles as the SDK canary: if `_delay`/`finished`/`_extract_delay` go away in a future `azure-core`, this test fails to construct or run.

### New `tests/test_await_all.py`

- `test_await_all_collects_all_results` *(parametrized: N=0, N=1, N=3)* — pollers fire their done callbacks; `await_all` returns a dict with one `OCRResult(succeeded=True)` per name.
- **`test_await_all_synthesizes_budget_exceeded_on_timeout`** — M of N pollers don't complete by the budget. Remaining pollers' `cancel_event` gets set; their callbacks may fire during the grace window (lucky-race covered: that variant asserts the real result wins) or get synthesized as `OCRResult(error="OCR batch budget exceeded...")`. Regression test for the cleanup path.

### Extensions to `tests/test_pdf_extract.py`

- `test_ocr_end_to_end` *(parametrized: success / timeout / azure-error)* — `PdfExtract.ocr()` invoked with a mocked azure integrator. For success: assert `ExtractedPage.text` populated, `source == "OCR"`, `handwritten_ratio` set, `ocr_result.succeeded is True`, no false INFO log. For failure variants: assert `ocr_error` set on every affected page, `text == ""`, `source == "embedded"`, ERROR log present, no INFO success log. Also verifies the default `azure=AZURE_READ` by invoking `ocr()` with no argument and asserting AZURE_READ's thread-local was populated.

### Extensions to `tests/test_batch.py`

- `test_perform_batch_ocr_uses_azure_read_no_threadpool` — patch `AZURE_READ.submit` and the module-level `await_all`; assert both are called, AND assert `ThreadPoolExecutor` is NOT instantiated inside `_perform_batch_ocr` (scoped — `_pull_s3_parallel` still uses one and is unaffected).
- `test_partial_failure_does_not_crash_batch` — `await_all` returns a mix of successful and error `OCRResult`s; batch completes; failed PDFs have `ocr_error` set; others have populated text.

Existing tests in the suite (including the `samples/all70th.bin` fixture-based ones) must continue to pass without modification. This is verified by running the full suite at CI time, not via a dedicated test entry.

### Manual / smoke verification (not in CI)

```bash
<PREFIX> pytest tests/ -v
<PREFIX> pytest tests/test_azure_docintel_integrator.py tests/test_cancellable_polling.py tests/test_await_all.py -v
<PREFIX> pyright pypdftotext/
<PREFIX> ruff check pypdftotext/
<PREFIX> ruff format --check pypdftotext/
<PREFIX> pytest --doctest-modules pypdftotext/
```

---

## Migration / rollout

1. Land on a feature branch with all changes in one PR. Diff is medium (~600 lines new, ~150 lines changed). Single reviewable unit.
2. Existing `tests/` directory exercises real fixtures (`samples/all70th.bin` is a pickled `AnalyzeResult`); the new flow must produce byte-identical output for the success path. Add the new tests, ensure existing tests pass unchanged.
3. Release as a minor version bump (0.3.8 → 0.4.0). Document deprecations in the changelog.
4. Schedule deprecation-layer removal for the *next* minor (0.5.0). Out of scope for this spec.

---

## Out of scope / deferred

- **P3 thread-safety** (multi-thread sharing one `PdfExtract`). `PdfReader`/`PdfWriter` are not thread-safe today, documented in CLAUDE.md. Revisit after this rewrite lands. Saved to project auto-memory as `project_p3_followup`.
- **Asyncio core.** Considered and declined. Conflict with caller event loops outweighs the efficiency gain over sync submit-and-await.
- **Continuation-token crash recovery.** Feasible but a separate concern. `OCRResult` does not currently expose `continuation_token`; it could be added later as an optional field without breaking anything.
- **Cancelling Azure-side processing.** No API path exists; we can only stop waiting locally. Cancelled-but-still-processing operations on Azure run to completion and count against quota, same as today's "timed out but kept polling" behavior.
- **Removing the deprecation layer.** Planned for one minor version after this rewrite ships.

---

## Open questions

None remaining. All design decisions resolved during brainstorming:

- Scope: submit-and-await + `OCRResult` (broader than surgical fix, narrower than asyncio rewrite). Resolved.
- Failure model: soft-fail + `ExtractedPage.ocr_error` attribute. Resolved.
- `AZURE_READ` future: promoted to canonical shared instance with thread-local back-compat. Resolved.
- `OCRResult` publicity: public, exported in `__all__`. Resolved.
- Cancellation: `CancellablePolling` subclass; accept SDK-private-API brittleness, covered by canary test. Resolved.
- Per-call config override: via `config=` kwarg on `submit`/`await_one`/`await_all`. Resolved.

---

## Appendix: Patterns table (P1–P4) for quick reference

See "Cross-thread contamination patterns" above. The TL;DR:

- **P1 (multi-process)** — safe, no change
- **P2 (one PdfExtract per thread)** — safe, no change
- **P3 (shared PdfExtract across threads)** — unsafe today, **deferred**
- **P4 (shared AzureDocIntelIntegrator across threads)** — unsafe today, **fixed by this rewrite** via per-call `OCRResult` value type
