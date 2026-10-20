# OCR Result Streaming and Retention: Design Spec

**Date:** 2026-10-19
**Status:** Draft (pre-implementation)
**Scope:** Internal restructure of the batch OCR harvest path, the `OCRResult` value type, and the `AzureDocIntelIntegrator` deprecated surface. Public API changes: `OCRResult` loses its `config` field and its three helper methods (moved to module functions); `ExtractedPage.azure_page` and `PdfExtract.ocr_result.raw` are retained only when `debug_path` is set; five deprecated members of `AzureDocIntelIntegrator` are removed; one new config field `AZURE_DOCINTEL_BATCH_TIMEOUT` is added.

---

## TL;DR

- **Peak memory during batch OCR is uncapped.** `_perform_batch_ocr` submits every PDF, `await_all` waits for *all* pollers, then harvests every result into a dict, then applies. At harvest time every poller holds its full result response (JSON bytes, decoded text, parsed dict) while every `OCRResult` holds the parsed `AnalyzeResult` and rendered page strings. Fix: harvest and apply each result as its poller completes, popping the poller as it is harvested.
- **Steady-state memory is dominated by `OCRResult.raw`.** Measured on a synthetic 10-page prebuilt-read result: parsed Python objects are ~5x the JSON size (~4.1 MB per 10 pages), of which `pages[].words` is ~81%. Nothing in the package reads `raw` after `_apply_ocr_result` except the `debug_path` writes. Fix: drop `raw` and `ExtractedPage.azure_page` unless `debug_path` is set.
- **`AZURE_DOCINTEL_TIMEOUT` is a per-PDF timeout, not a batch budget.** The current `await_all` misuses it as a wall-clock bound on the whole batch. Fix: each poller gets its own deadline anchored at its submit time. A batch-wide bound is a separate, optional config field.
- **`OCRResult` is larger than its job.** It is a transport object from `await_one` to `_apply_ocr_result`. Its three helpers (`page_at_index`, `rotation_degrees`, `handwritten_ratio`) are pure functions of `(AnalyzeResult, page_index, config)` and move to module functions in `ocr_result.py`. The `config` field exists only to feed them and is dropped.
- **Deprecated integrator surface is removed.** `reset`, `last_result`, `handwritten_ratio`, `rotation_degrees`, `page_at_index`, and the `_thread_local` that backs them go away. These are the only other retention point for `raw`.

---

## Measurements behind the retention decision

Synthetic prebuilt-read result, 10 pages, 400 words/page, 8 words/line:

| Portion | JSON bytes | Share |
|---|---|---|
| pages.words | 636,759 | 81% |
| pages.lines | 94,156 | 12% |
| paragraphs | 28,371 | 4% |
| content | 26,837 | 3% |
| styles, languages | 164 | 0% |

| Parsed Python objects | Bytes |
|---|---|
| Full result | 4,141,500 |
| Geometry only (page number, angle, size) | 31,668 |

`content` is a red herring: it is one contiguous `str`. The cost is the per-word dicts, polygons and spans.

SDK facts that make the drop safe: `AnalyzeResult` and `DocumentPage` are thin wrappers over the parsed JSON dict; nested models are cached back into that dict, so `raw.pages[i]` is the same object every access and is the object stored on `ExtractedPage.azure_page`. Setting `OCRResult.raw = None` releases the whole graph provided `azure_page` is not also holding a page.

---

## Design

### 1. `OCRResult` slimmed; helpers become module functions

`pypdftotext/ocr_result.py`:

```python
@dataclass
class OCRResult:
    pdf_name: str
    pages: list[str] = field(default_factory=list)
    error: str | None = None
    raw: AnalyzeResult | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and bool(self.pages)


def page_at_index(raw: AnalyzeResult, page_index: int) -> DocumentPage | None: ...
def rotation_degrees(raw: AnalyzeResult, page_index: int, config: PyPdfToTextConfig) -> float: ...
def handwritten_ratio(raw: AnalyzeResult, page_index: int, config: PyPdfToTextConfig) -> float: ...
```

- `succeeded` no longer depends on `raw`, because `raw` is dropped after apply. `await_one` already converts an empty `raw.pages` into an error before constructing the result, so `error is None and bool(pages)` is equivalent at construction time.
- The three functions take `AnalyzeResult` (never `None`). Callers only invoke them on a succeeded result. Bodies are the existing method bodies with `self.raw` -> `raw` and `self.config` -> `config`.
- `OCRResult` stays exported from `pypdftotext/__init__.py`. The three functions are importable from `pypdftotext.ocr_result` but not re-exported.

### 2. Retention gated on `debug_path`

In `PdfExtract._apply_ocr_result`:

- `ext_pg.azure_page` is assigned only when `self.debug_path` is set; otherwise it stays `None`.
- After the existing debug writes (`ocr_pages.json`, `azure.json`, `azure_content.txt`), if `self.debug_path` is not set, `result.raw = None`.
- `PdfExtract.ocr_result` is still retained (it carries `pages` and `error`, and `succeeded` keeps working).
- `ExtractedPage.azure_page` docstring states it is populated only when `debug_path` is set.

No config flag. There are no consumers of `raw` or `azure_page` outside debugging; a flag can be added later if one appears.

### 3. Deprecated integrator surface removed

From `AzureDocIntelIntegrator`: `reset()`, `last_result` property, `handwritten_ratio()`, `rotation_degrees()`, `page_at_index()`, the `_thread_local` field, the two `_thread_local` assignments in `await_one`, the `warnings` import, and the now-unused `DocumentPage` import. `AnalyzeResult` import stays (used by `await_one`'s local annotation).

### 4. Per-PDF timeout and optional batch budget

`pypdftotext/_config.py`:

- `AZURE_DOCINTEL_TIMEOUT: int = 60` keeps its value; docstring corrected to: *"How long to wait, in seconds, for a single PDF's Azure OCR result, measured from its submission. Default is 60."*
- New `AZURE_DOCINTEL_BATCH_TIMEOUT: int | None = None`: *"Optional wall-clock budget, in seconds, for the OCR phase of a `PdfExtractBatch`, measured from the moment all submissions complete. `None` (default) means no batch-wide bound; `AZURE_DOCINTEL_TIMEOUT` still applies to each PDF individually."* Added to `PyPdfToTextConfigOverrides` as well.

### 5. Streaming harvest: `iter_results`

`pypdftotext/azure_docintel_integrator.py`:

```python
def iter_results(
    pollers: dict[str, AnalyzeDocumentLROPoller],
    integrator: AzureDocIntelIntegrator,
    *,
    config: PyPdfToTextConfig | None = None,
    submitted_at: Mapping[str, float] | None = None,
    batch_timeout: float | None = None,
) -> Iterator[tuple[str, OCRResult]]:
```

Semantics:

- **Consumes `pollers`.** Each entry is popped from the caller's dict before its result is yielded. When iteration ends the dict is empty. This is how the poller's response body is released as soon as it is harvested; the caller must not keep a second reference.
- **Eager registration.** `iter_results` is a regular function that registers the done-callbacks at call time and returns an inner generator, so no completion can be missed between the call and the first `next()` (the batch wraps the iterator in `tqdm` before iterating).
- **Completion signalling.** A done-callback per poller enqueues its name on a `queue.Queue[str]`. The callback does nothing else (it runs on the SDK's daemon polling thread; calling `poller.result()` there raises `RuntimeError: cannot join current thread`). A `seen` set de-duplicates, because azure-core's `add_done_callback` both invokes immediately on an already-done poller and appends to the callback list.
- **Per-PDF deadline.** `deadline[name] = (submitted_at[name] if provided else start) + cfg.AZURE_DOCINTEL_TIMEOUT`, where `start = time.monotonic()` on generator entry. `submitted_at` values are `time.monotonic()` readings taken by the caller right after each `submit`.
- **Batch deadline.** `start + batch_timeout` when `batch_timeout` is not `None`; otherwise none.
- **Loop.** While pollers remain: wait on the queue until the earliest pending deadline (per-PDF or batch). On a name: skip if already seen or already popped; otherwise pop and yield `(name, integrator.await_one(poller, pdf_name=name, config=cfg))`. On queue timeout: compute the expired set (every pending poller whose per-PDF deadline has passed; or all pending if the batch deadline has passed). For each expired poller: set its `cancel_event` (guarded `getattr`, as today), pop it, harvest with `integrator.await_one(poller, pdf_name=name, config=cfg, timeout=_CANCEL_GRACE_SECONDS)`. If the harvested result `succeeded` (lucky race: the final poll had already landed), yield it as-is. Otherwise yield an `OCRResult(pdf_name=name, pages=[], error=...)` with:
  - per-PDF expiry: `f"OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT={cfg.AZURE_DOCINTEL_TIMEOUT}s"`
  - batch expiry: `f"OCR batch budget exceeded after {batch_timeout}s (pdf still pending)"`
- **Never raises** for Azure/timeout causes (same contract as `await_one`).

`await_one` gains a keyword `timeout: float | None = None`; `None` means `cfg.AZURE_DOCINTEL_TIMEOUT`. It is passed to `poller.result(...)`. The existing timeout error text is unchanged for the default path.

`_BUDGET_GRACE_SECONDS` is renamed `_CANCEL_GRACE_SECONDS` (value stays `5.0`) and now bounds how long a cancelled poller's thread is joined during harvest.

`await_all(pollers, integrator, timeout, *, config=None) -> dict[str, OCRResult]` is kept as a thin wrapper: `dict(iter_results(pollers, integrator, config=config, batch_timeout=timeout))`. Its `timeout` keeps meaning "batch budget", which is what its existing tests assert.

### 6. `_perform_batch_ocr` streams and applies

```python
pollers: dict[str, AnalyzeDocumentLROPoller] = {}
submitted_at: dict[str, float] = {}
for pdf_name, extract in ocr_pdfs.items():
    poller = AZURE_READ.submit(extract.body, extract.ocr_page_idxs, pdf_name=pdf_name, config=self.config)
    if poller is None:
        extract._apply_ocr_result(OCRResult(pdf_name=pdf_name, error="OCR failed: no client available (...)"))
        continue
    pollers[pdf_name] = poller
    submitted_at[pdf_name] = time.monotonic()

pbar = tqdm(
    iter_results(pollers, AZURE_READ, config=self.config, submitted_at=submitted_at,
                 batch_timeout=self.config.AZURE_DOCINTEL_BATCH_TIMEOUT),
    total=len(pollers), desc="Awaiting OCR results",
    disable=self.config.DISABLE_PROGRESS_BAR, position=self.config.PROGRESS_BAR_POSITION, leave=None,
)
for pdf_name, result in pbar:
    pbar.set_postfix_str(pdf_name)
    try:
        ocr_pdfs[pdf_name]._apply_ocr_result(result)
    except Exception as e:  # noqa: BLE001  # batch survives per-PDF apply failures
        logger.error(...)
```

Results are applied in completion order. `_apply_ocr_result` is per-extract and order-independent, and it runs on the coordinator thread, so `PdfReader` is never touched from another thread.

---

## Out of scope

- An in-flight submission cap (chunking). Once results stream, a cap is a small follow-on; it is not needed for the memory goal.
- Interleaving submission with harvesting. Submissions complete first; early completions wait in the queue for at most the remaining submit time.
- Version bump and CHANGELOG; the maintainer does these separately.
- README changes. The README does not document these config fields or `OCRResult`.

## Compatibility notes

- `OCRResult(config=...)` constructor calls break. ~40 such calls exist in `tests/`, none in `pypdftotext/` outside the three sites that are rewritten.
- `OCRResult.handwritten_ratio()` / `.rotation_degrees()` / `.page_at_index()` methods are removed in favour of the module functions.
- `ExtractedPage.azure_page` is `None` unless `debug_path` is set.
- `AzureDocIntelIntegrator.last_result` / `.reset()` / `.handwritten_ratio()` / `.rotation_degrees()` / `.page_at_index()` are removed. `PdfExtract.handwritten_ratio(page_index)` is unaffected.
