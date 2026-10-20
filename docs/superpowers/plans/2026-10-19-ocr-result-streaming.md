# OCR Result Streaming and Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flatten batch-OCR peak memory by harvesting and applying each Azure result as its poller completes, and cut steady-state memory by dropping the raw `AnalyzeResult` and `ExtractedPage.azure_page` unless `debug_path` is set.

**Architecture:** A new module-level generator `iter_results` in `azure_docintel_integrator.py` replaces the wait-for-all phase of `await_all`: done-callbacks enqueue poller names, the coordinator thread pops each poller as it is harvested, and each PDF has its own deadline anchored at submit time (`AZURE_DOCINTEL_TIMEOUT`), with an optional batch-wide budget (`AZURE_DOCINTEL_BATCH_TIMEOUT`). `OCRResult` becomes a plain transport dataclass (`pdf_name`, `pages`, `error`, `raw`); its three helpers become module functions that take `AnalyzeResult` explicitly. `PdfExtract._apply_ocr_result` consumes the helpers and then releases `raw` and skips `azure_page` when there is no `debug_path`. The deprecated thread-local surface on `AzureDocIntelIntegrator` is removed.

**Tech Stack:** Python 3.10+, `azure-ai-documentintelligence`, `azure-core` LRO pollers, `pypdf`, `tqdm`, `unittest` + `unittest.mock`, `pytest` runner, `ruff`, `pyright`.

**Spec:** [`docs/superpowers/specs/2026-10-19-ocr-result-streaming-design.md`](../specs/2026-10-19-ocr-result-streaming-design.md)

**Python environment prefix:** All `python`, `pytest`, `ruff`, and `pyright` commands must be prefixed with conda activation. Use Git Bash for these commands:

```bash
PREFIX="source /c/Users/samha/anaconda3/etc/profile.d/conda.sh && conda activate pypdftotext &&"
```

Throughout this plan `<PREFIX>` refers to that string. Example: `<PREFIX> python -m pytest tests/test_config.py -v`.

## Global Constraints

- Line length 99; `ruff check pypdftotext/` and `ruff format --check pypdftotext/` must pass (tests are excluded from linting).
- `pyright` in standard mode must pass on `pypdftotext/`. No new `# pyright: ignore` or `# noqa` without a justifying comment; the existing `# noqa: BLE001` pattern in `batch.py` is the only sanctioned suppression and it carries its justification inline.
- Page indices are 0-based everywhere; Azure `page_number` is 1-based.
- Done-callbacks registered on Azure pollers must never call `poller.result()`; they run on the SDK's daemon polling thread.
- `_apply_ocr_result` runs only on the coordinator thread (`PdfReader` is not thread safe).
- `AZURE_DOCINTEL_TIMEOUT` is a per-PDF timeout. Only `AZURE_DOCINTEL_BATCH_TIMEOUT` may bound a whole batch.
- Doctests run via `tests/test_docstrings.py`; any doctest `Example:` in `ocr_result.py` must pass under `doctest.NORMALIZE_WHITESPACE`.
- No version bump, CHANGELOG, or README edits (maintainer does these separately).

## Review Focus

Inputs the spec implies but which are most likely to bite, each pinned to a test in the owning task:

1. **A poller whose done-callback fires twice** (azure-core invokes immediately on an already-done poller *and* appends to the list). `iter_results` must yield that PDF exactly once. Pinned in Task 5 (`test_iter_results_yields_each_name_once_when_callback_fires_twice`).
2. **One PDF expires while another with a later `submitted_at` is still within its own deadline.** Only the expired one gets a timeout result; the other is still awaited. Pinned in Task 5 (`test_iter_results_per_pdf_deadline_anchored_at_submit_time`).
3. **`_apply_ocr_result` raises for one PDF mid-stream.** The batch must keep harvesting and applying the remaining PDFs. Pinned in Task 6 (`test_apply_exception_for_one_pdf_does_not_stop_stream`).
4. **`debug_path` set.** `azure.json` and `azure_content.txt` are written from the full `raw`, and both `ocr_result.raw` and `azure_page` are retained. Pinned in Task 2 (`test_apply_retains_raw_and_azure_page_with_debug_path`).
5. **Cancelled poller whose final poll had already landed** (the lucky race). `iter_results` must yield the real result, not a synthesized timeout. Pinned in Task 5 (`test_iter_results_expired_poller_with_late_success_yields_success`).

---

## File Structure

**New files:**

- `tests/test_ocr_result.py` — tests for the slimmed `OCRResult` and the three module functions (the `TestOCRResult` class moves here from `tests/test_pdf_extract.py`).

**Modified files:**

- `pypdftotext/ocr_result.py` — slim `OCRResult`; add module functions `page_at_index`, `rotation_degrees`, `handwritten_ratio`.
- `pypdftotext/pdf_extract.py` — `_apply_ocr_result` uses module functions; gates `azure_page` and `raw` retention on `debug_path`.
- `pypdftotext/extracted_page.py` — `azure_page` docstring.
- `pypdftotext/azure_docintel_integrator.py` — remove deprecated surface; add `iter_results`; `await_one(timeout=)`; `await_all` becomes a wrapper; rename `_BUDGET_GRACE_SECONDS` to `_CANCEL_GRACE_SECONDS`.
- `pypdftotext/_config.py` — `AZURE_DOCINTEL_BATCH_TIMEOUT`; corrected `AZURE_DOCINTEL_TIMEOUT` docstring.
- `pypdftotext/batch.py` — `_perform_batch_ocr` streams via `iter_results`.
- `CLAUDE.md` — reflect new shapes.
- `tests/test_pdf_extract.py`, `tests/test_batch.py`, `tests/test_await_all.py`, `tests/test_azure_docintel_integrator.py`, `tests/test_config.py` — updated as described per task.

**Branch:** create `ocr-result-streaming` from `main` before Task 1 (via the using-git-worktrees skill at execution time).

---

### Task 1: Slim `OCRResult` and move helpers to module functions

**Files:**
- Modify: `pypdftotext/ocr_result.py`
- Modify: `pypdftotext/azure_docintel_integrator.py:177-183` (synthesized budget result), `:358-364` (await_one result construction), `:400-452` (the three deprecated wrappers delegate to the module functions until Task 3 deletes them)
- Modify: `pypdftotext/batch.py:236-245` (no-client result)
- Modify: `pypdftotext/pdf_extract.py:23` (import) and `:360-410` (`_apply_ocr_result` call sites only; retention gating is Task 2)
- Create: `tests/test_ocr_result.py`
- Modify: `tests/test_pdf_extract.py` (delete `TestOCRResult` class at lines 832-927; remove `config=` from every `OCRResult(` call; replace deprecated integrator calls in `TestOCRIntegration` at ~405-425 and ~490-505), `tests/test_batch.py` (remove `config=` from every `OCRResult(` call)

**Interfaces:**
- Produces:
  - `OCRResult(pdf_name: str, pages: list[str] = [], error: str | None = None, raw: AnalyzeResult | None = None)`, dataclass, with `succeeded -> bool` property equal to `error is None and bool(pages)`.
  - `page_at_index(raw: AnalyzeResult, page_index: int) -> DocumentPage | None`
  - `rotation_degrees(raw: AnalyzeResult, page_index: int, config: PyPdfToTextConfig) -> float`
  - `handwritten_ratio(raw: AnalyzeResult, page_index: int, config: PyPdfToTextConfig) -> float`
  - All three live in `pypdftotext/ocr_result.py`. Bodies are the existing method bodies with `self.raw` replaced by `raw` and `self.config` by `config`. The existing `logger.warning` in `handwritten_ratio` stays.

- [ ] **Step 1: Create `tests/test_ocr_result.py` with failing tests**

Move the body of `TestOCRResult` from `tests/test_pdf_extract.py` (including `_fake_analyze_result_dict`) into the new file, then rewrite so the assertions are:

```python
def test_succeeded_true_when_pages_and_no_error(self):
    result = OCRResult(pdf_name="x.pdf", pages=["page1 text"])
    self.assertTrue(result.succeeded)

def test_succeeded_false_when_error_set(self):
    self.assertFalse(OCRResult(pdf_name="x.pdf", error="OCR timeout: simulated").succeeded)

def test_succeeded_false_when_no_pages(self):
    self.assertFalse(OCRResult(pdf_name="x.pdf").succeeded)

def test_succeeded_does_not_depend_on_raw(self):
    raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
    result = OCRResult(pdf_name="x.pdf", pages=["a"], raw=raw)
    result.raw = None
    self.assertTrue(result.succeeded)

def test_ocr_result_has_no_config_field(self):
    self.assertNotIn("config", {f.name for f in dataclasses.fields(OCRResult)})

def test_page_at_index_returns_page_when_present(self):
    raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=2))
    self.assertEqual(page_at_index(raw, 0).page_number, 1)
    self.assertEqual(page_at_index(raw, 1).page_number, 2)

def test_page_at_index_returns_none_when_missing(self):
    raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
    self.assertIsNone(page_at_index(raw, 99))

def test_rotation_degrees_zero_when_page_missing(self):
    raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
    self.assertEqual(rotation_degrees(raw, 5, PyPdfToTextConfig()), 0.0)

def test_rotation_degrees_returns_angle_above_threshold(self):
    d = self._fake_analyze_result_dict(num_pages=1); d["pages"][0]["angle"] = 90.0
    self.assertEqual(rotation_degrees(AnalyzeResult(d), 0, PyPdfToTextConfig()), 90.0)

def test_rotation_degrees_zero_below_threshold(self):
    cfg = PyPdfToTextConfig(overrides={"MIN_OCR_ROTATION_DEGREES": 5.0})
    d = self._fake_analyze_result_dict(num_pages=1); d["pages"][0]["angle"] = 2.0
    self.assertEqual(rotation_degrees(AnalyzeResult(d), 0, cfg), 0.0)

def test_handwritten_ratio_zero_when_no_styles(self):
    raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
    self.assertEqual(handwritten_ratio(raw, 0, PyPdfToTextConfig()), 0.0)

def test_handwritten_ratio_counts_confident_handwritten_spans(self):
    d = self._fake_analyze_result_dict(num_pages=1)   # page 0 span offset 0, length 10
    d["styles"] = [{"isHandwritten": True, "confidence": 0.95,
                    "spans": [{"offset": 0, "length": 5}]}]
    self.assertAlmostEqual(handwritten_ratio(AnalyzeResult(d), 0, PyPdfToTextConfig()), 0.5)

def test_handwritten_ratio_ignores_low_confidence_styles(self):
    d = self._fake_analyze_result_dict(num_pages=1)
    d["styles"] = [{"isHandwritten": True, "confidence": 0.1,
                    "spans": [{"offset": 0, "length": 5}]}]
    self.assertEqual(handwritten_ratio(AnalyzeResult(d), 0, PyPdfToTextConfig()), 0.0)

def test_ocr_result_publicly_importable(self):   # unchanged from the moved class
```

Imports at the top of the file: `dataclasses`, `unittest`, `AnalyzeResult`, `PyPdfToTextConfig`, and `from pypdftotext.ocr_result import OCRResult, handwritten_ratio, page_at_index, rotation_degrees`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `<PREFIX> python -m pytest tests/test_ocr_result.py -v`
Expected: FAIL with `ImportError: cannot import name 'page_at_index'`.

- [ ] **Step 3: Rewrite `pypdftotext/ocr_result.py`**

Dataclass fields in this order: `pdf_name: str`, `pages: list[str] = field(default_factory=list)`, `error: str | None = None`, `raw: AnalyzeResult | None = None`. `succeeded` property returns `self.error is None and bool(self.pages)`. Delete the three methods and the `config` field; add the three module functions with the signatures in Interfaces. Update the class docstring (drop the `config` attribute description; note that `raw` is `None` after `PdfExtract._apply_ocr_result` unless `debug_path` is set). Keep a doctest `Example:` on `succeeded` and add one on `page_at_index` using a minimal `AnalyzeResult({"pages": [{"pageNumber": 1}]})`; both must construct without `config=`.

- [ ] **Step 4: Update construction and call sites in the package**

- `azure_docintel_integrator.py` `await_one`: `OCRResult(pdf_name=pdf_name, pages=pages, error=error, raw=raw)`.
- `azure_docintel_integrator.py` `await_all` synthesized budget result: drop `config=cfg`, `raw=None`, `pages=[]` (defaults).
- `azure_docintel_integrator.py` deprecated wrappers `handwritten_ratio` / `rotation_degrees` / `page_at_index`: keep their `DeprecationWarning`, but delegate to the module functions as `handwritten_ratio(result.raw, page_index, self.config)` / `rotation_degrees(result.raw, page_index, self.config)` / `page_at_index(result.raw, page_index)`, returning `0.0` / `0.0` / `None` when `result is None or result.raw is None`. Task 3 deletes them.
- `batch.py` no-client result: drop `config=self.config`, `raw=None`, `pages=[]`.
- `pdf_extract.py`: import `handwritten_ratio, page_at_index, rotation_degrees` alongside `OCRResult`. In `_apply_ocr_result`, change the failure-branch condition to `if not result.succeeded or result.raw is None:` (narrows `raw` for pyright; `await_one` never produces a succeeded result with `raw=None`), bind `raw = result.raw`, and replace the three method calls with `rotation_degrees(raw, og_pg_idx, self.config)`, `handwritten_ratio(raw, og_pg_idx, self.config)`, `page_at_index(raw, og_pg_idx)`. No retention change yet.

- [ ] **Step 5: Update tests that construct `OCRResult`**

In `tests/test_pdf_extract.py` delete the `TestOCRResult` class (moved in Step 1) and remove the `config=...` argument from every remaining `OCRResult(` call (`_build_success_ocr_result`, the two failure fixtures in `TestOcrEndToEnd`). In `tests/test_batch.py` remove `config=config or self.cfg` / `config=self.cfg` from every `OCRResult(` call. Verify with:

Run: `grep -rn "OCRResult(" tests/ pypdftotext/ | grep "config="`
Expected: no output.

Then, in `tests/test_pdf_extract.py` `TestOCRIntegration`, replace the deprecated integrator calls with the module functions applied to the pickled fixture (import `from pypdftotext.ocr_result import handwritten_ratio, page_at_index, rotation_degrees` at the top of the file). In `test_ocr_integration` (~405-425):

```python
for i in range(4):
    ratio = handwritten_ratio(self.all70th_ocr_result, i, config)
    self.assertIsInstance(ratio, float)
    self.assertGreaterEqual(ratio, 0.0)
    self.assertLessEqual(ratio, 1.0)
    self.assertEqual(pdf.extracted_pages[i].handwritten_ratio, ratio)
    rotation = rotation_degrees(self.all70th_ocr_result, i, config)
    expected_angle = self.all70th_ocr_result.pages[i].angle or 0.0
    if abs(expected_angle) > config.MIN_OCR_ROTATION_DEGREES:
        self.assertEqual(rotation, expected_angle)
    else:
        self.assertEqual(rotation, 0.0)
    self.assertEqual(page_at_index(self.all70th_ocr_result, i).page_number, i + 1)
```

In `test_rotation_correction` (~490-505): `rotation = rotation_degrees(self.all70th_ocr_result, i, config)`; the remaining assertions are unchanged. Leave the two `assertIsNotNone(page.azure_page)` assertions alone; Task 2 flips them.

- [ ] **Step 6: Run the affected suites**

Run: `<PREFIX> python -m pytest tests/test_ocr_result.py tests/test_pdf_extract.py tests/test_batch.py tests/test_azure_docintel_integrator.py tests/test_await_all.py tests/test_docstrings.py -v`
Expected: all PASS.

- [ ] **Step 7: Lint, format, type-check**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add pypdftotext/ocr_result.py pypdftotext/azure_docintel_integrator.py pypdftotext/batch.py pypdftotext/pdf_extract.py tests/test_ocr_result.py tests/test_pdf_extract.py tests/test_batch.py
git commit -m "refactor: slim OCRResult; move page helpers to module functions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `_apply_ocr_result` uses module functions and gates retention on `debug_path`

**Files:**
- Modify: `pypdftotext/pdf_extract.py:360-428` (`_apply_ocr_result`), `:23` (import)
- Modify: `pypdftotext/extracted_page.py:30` (docstring)
- Test: `tests/test_pdf_extract.py` (`TestOcrEndToEnd`, and the two `assertIsNotNone(page.azure_page)` sites at ~403 and ~509)

**Interfaces:**
- Consumes: `page_at_index`, `rotation_degrees`, `handwritten_ratio` from Task 1.
- Produces: after `_apply_ocr_result(result)` returns, `result.raw is None` and every `ExtractedPage.azure_page is None` when `self.debug_path` is falsy; both retained when it is set. `PdfExtract.ocr_result` is still the same `OCRResult` object.

- [ ] **Step 1: Write failing tests in `TestOcrEndToEnd` (`tests/test_pdf_extract.py`)**

```python
def test_apply_drops_raw_and_azure_page_without_debug_path(self):
    # same setup as test_ocr_end_to_end_success
    ...
    _ = extract.extracted_pages
    self.assertTrue(extract.ocr_result.succeeded)
    self.assertIsNone(extract.ocr_result.raw)
    for idx in extract.ocr_page_idxs:
        self.assertIsNone(extract.extracted_pages[idx].azure_page)
        self.assertEqual(extract.extracted_pages[idx].source, "OCR")

def test_apply_retains_raw_and_azure_page_with_debug_path(self):
    with tempfile.TemporaryDirectory() as tmp:
        debug_path = Path(tmp)
        extract = PdfExtract(self.pdf_path.read_bytes(), config=cfg, pdf_name="t.pdf",
                             debug_path=debug_path)
        ...  # same mock_azure wiring; _build_success_ocr_result(extract)
        _ = extract.extracted_pages
        self.assertIsNotNone(extract.ocr_result.raw)
        for idx in extract.ocr_page_idxs:
            page = extract.extracted_pages[idx]
            self.assertIsNotNone(page.azure_page)
            self.assertEqual(page.azure_page.page_number, idx + 1)
        self.assertTrue((debug_path / "azure.json").exists())
        self.assertTrue((debug_path / "azure_content.txt").exists())
        self.assertTrue((debug_path / "ocr_pages.json").exists())
```

Also change the two existing `self.assertIsNotNone(page.azure_page)` assertions in `TestOCRIntegration` (around lines 403 and 509) to `self.assertIsNone(page.azure_page)` with the comment `# azure_page is retained only when debug_path is set`.

- [ ] **Step 2: Run to verify failure**

Run: `<PREFIX> python -m pytest tests/test_pdf_extract.py -k "TestOcrEndToEnd" -v`
Expected: the two new tests FAIL (`AttributeError: 'OCRResult' object has no attribute 'rotation_degrees'` or the retention assertions).

- [ ] **Step 3: Gate retention in `_apply_ocr_result`**

- In the per-page loop: `ext_pg.azure_page = page_at_index(raw, og_pg_idx) if self.debug_path else None`.
- After the existing `if self.debug_path and self.ocr_result is not None:` debug-write block, add: `if not self.debug_path: result.raw = None`.
- Docstring: state that `raw` and `azure_page` are retained only when `debug_path` is set.

- [ ] **Step 4: Update `ExtractedPage.azure_page` docstring** in `pypdftotext/extracted_page.py` to: `azure_page: The Azure DocumentPage for this page when it was OCR'd AND the owning PdfExtract has a debug_path; otherwise None.`

- [ ] **Step 5: Run the suites**

Run: `<PREFIX> python -m pytest tests/test_pdf_extract.py tests/test_batch.py tests/test_docstrings.py -v`
Expected: all PASS except the two `TestOCRIntegration` tests that still call `azure_integrator.handwritten_ratio` / `rotation_degrees` / `page_at_index` (fixed in Task 3).

- [ ] **Step 6: Lint, format, type-check**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/pdf_extract.py pypdftotext/extracted_page.py tests/test_pdf_extract.py
git commit -m "perf: release raw OCR result and azure_page unless debug_path is set

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Remove the deprecated `AzureDocIntelIntegrator` surface

**Files:**
- Modify: `pypdftotext/azure_docintel_integrator.py` (imports at 7 and 12; `_thread_local` field at 195-199; `reset` 201-215; `last_result` 217-232; assignments in `await_one` at 366-367; `handwritten_ratio` / `rotation_degrees` / `page_at_index` at 400-452; docstring mentions at 117-120 and 315-316)
- Test: `tests/test_azure_docintel_integrator.py` (delete `TestDeprecatedSurface` and `TestThreadLocalIsolation`; drop the `warnings` import), `tests/test_pdf_extract.py` (`TestOCRIntegration` loops at ~405-425 and ~490-505)

**Interfaces:**
- Produces: `AzureDocIntelIntegrator` exposes only `config`, `client`, `create_client`, `submit`, `await_one`, `ocr_pages`. No `_thread_local`.

- [ ] **Step 1: Write the failing test** in `tests/test_azure_docintel_integrator.py`, class `TestNoDeprecatedSurface`:

```python
def test_deprecated_members_removed(self):
    integrator = AzureDocIntelIntegrator(self.cfg)
    for name in ("reset", "last_result", "handwritten_ratio", "rotation_degrees",
                 "page_at_index", "_thread_local"):
        self.assertFalse(hasattr(integrator, name), name)
```

- [ ] **Step 2: Run to verify failure**

Run: `<PREFIX> python -m pytest tests/test_azure_docintel_integrator.py::TestNoDeprecatedSurface -v`
Expected: FAIL on `reset`.

- [ ] **Step 3: Remove the deprecated surface** from `pypdftotext/azure_docintel_integrator.py`: the five members, the `_thread_local` dataclass field, the two `self._thread_local...` assignments at the end of `await_one`, the `import warnings`, and `DocumentPage` from the models import. Remove the "Its `_thread_local` is updated..." sentence from the `await_all` docstring and the "Updates `self._thread_local`..." sentence from the `await_one` docstring.

- [ ] **Step 4: Delete the tests that exercised the deprecated surface**

`tests/test_azure_docintel_integrator.py`: delete `TestDeprecatedSurface` and `TestThreadLocalIsolation`; delete `import warnings`. (`tests/test_pdf_extract.py` no longer references the deprecated methods after Task 1.)

- [ ] **Step 5: Run the suites**

Run: `<PREFIX> python -m pytest tests/test_azure_docintel_integrator.py tests/test_pdf_extract.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint, format, type-check**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`
Expected: clean (ruff will flag unused imports if any were missed).

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_azure_docintel_integrator.py tests/test_pdf_extract.py
git commit -m "refactor: remove deprecated thread-local surface from AzureDocIntelIntegrator

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `AZURE_DOCINTEL_BATCH_TIMEOUT` config field

**Files:**
- Modify: `pypdftotext/_config.py:23-24` (TypedDict) and `:78-79` (dataclass)
- Test: `tests/test_config.py` (class `TestPyPdfToTextConfig`, pytest style with `@pytest.mark.unit`)

**Interfaces:**
- Produces: `PyPdfToTextConfig.AZURE_DOCINTEL_BATCH_TIMEOUT: int | None = None`; key `AZURE_DOCINTEL_BATCH_TIMEOUT: int | None` in `PyPdfToTextConfigOverrides`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.unit
def test_batch_timeout_default_is_none(self):
    config = PyPdfToTextConfig(overrides={"INHERIT_CONSTANTS": False})
    assert config.AZURE_DOCINTEL_BATCH_TIMEOUT is None

@pytest.mark.unit
def test_batch_timeout_override(self):
    config = PyPdfToTextConfig(overrides={"AZURE_DOCINTEL_BATCH_TIMEOUT": 300})
    assert config.AZURE_DOCINTEL_BATCH_TIMEOUT == 300
```

- [ ] **Step 2: Run to verify failure**

Run: `<PREFIX> python -m pytest tests/test_config.py -k batch_timeout -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the field** directly after `AZURE_DOCINTEL_TIMEOUT` in both the TypedDict and the dataclass. Docstrings, verbatim from the spec:

- `AZURE_DOCINTEL_TIMEOUT`: `"""How long to wait, in seconds, for a single PDF's Azure OCR result, measured from its submission. Default is 60."""`
- `AZURE_DOCINTEL_BATCH_TIMEOUT`: `"""Optional wall-clock budget, in seconds, for the OCR phase of a PdfExtractBatch, measured from the moment all submissions complete. None (default) means no batch-wide bound; AZURE_DOCINTEL_TIMEOUT still applies to each PDF individually."""`

- [ ] **Step 4: Run to verify pass**

Run: `<PREFIX> python -m pytest tests/test_config.py tests/test_docstrings.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, format, type-check, commit**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`

```bash
git add pypdftotext/_config.py tests/test_config.py
git commit -m "feat(config): add AZURE_DOCINTEL_BATCH_TIMEOUT; clarify per-PDF timeout

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `iter_results` streaming generator; `await_all` becomes a wrapper

**Files:**
- Modify: `pypdftotext/azure_docintel_integrator.py` (`_BUDGET_GRACE_SECONDS` at 85-88; `await_all` at 92-184; `await_one` signature at 301-306 and `poller.result(...)` at 333)
- Test: `tests/test_await_all.py`

**Interfaces:**
- Consumes: `OCRResult` from Task 1; `AZURE_DOCINTEL_TIMEOUT` semantics from Task 4.
- Produces:
  - `iter_results(pollers: dict[str, AnalyzeDocumentLROPoller], integrator: AzureDocIntelIntegrator, *, config: PyPdfToTextConfig | None = None, submitted_at: Mapping[str, float] | None = None, batch_timeout: float | None = None) -> Iterator[tuple[str, OCRResult]]`. Pops each entry from `pollers` before yielding it.
  - `await_one(self, poller, pdf_name: str = "", *, config: PyPdfToTextConfig | None = None, timeout: float | None = None) -> OCRResult`; `timeout=None` means `cfg.AZURE_DOCINTEL_TIMEOUT`.
  - `await_all(pollers, integrator, timeout, *, config=None) -> dict[str, OCRResult]` = `dict(iter_results(pollers, integrator, config=config, batch_timeout=timeout))`.
  - `_CANCEL_GRACE_SECONDS: float = 5.0` (renamed from `_BUDGET_GRACE_SECONDS`).
  - Error strings: per-PDF expiry `f"OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT={cfg.AZURE_DOCINTEL_TIMEOUT}s"`; batch expiry `f"OCR batch budget exceeded after {batch_timeout}s (pdf still pending)"`.

- [ ] **Step 1: Write failing tests in `tests/test_await_all.py`**

Add `iter_results` and `import time` to the imports. Extend `_fake_poller` with a `fire_callbacks: bool = True` parameter; when `False`, set `poller._stored_callbacks = []` and `poller.add_done_callback.side_effect = poller._stored_callbacks.append`, so a test can fire stored callbacks later with `for fn in poller._stored_callbacks: fn(poller)`. Add class `TestIterResults(unittest.TestCase)` with the same `setUp` as `TestAwaitAll`:

```python
def test_iter_results_yields_in_completion_order_and_consumes_pollers(self):
    pollers = {"a": _fake_poller(immediate_result=_make_analyze_result("a")),
               "b": _fake_poller(immediate_result=_make_analyze_result("b"))}
    out = list(iter_results(pollers, self.integrator, config=self.cfg))
    self.assertEqual({n for n, _ in out}, {"a", "b"})
    self.assertTrue(all(r.succeeded for _, r in out))
    self.assertEqual(pollers, {})           # consumed

def test_iter_results_releases_poller_before_yield(self):
    pollers = {"a": _fake_poller(immediate_result=_make_analyze_result("a"))}
    gen = iter_results(pollers, self.integrator, config=self.cfg)
    name, _ = next(gen)
    self.assertEqual(name, "a")
    self.assertNotIn("a", pollers)          # popped before the yield

def test_iter_results_empty_pollers_yields_nothing(self):
    self.assertEqual(list(iter_results({}, self.integrator, config=self.cfg)), [])

def test_iter_results_yields_each_name_once_when_callback_fires_twice(self):
    poller = _fake_poller(immediate_result=_make_analyze_result("a"))
    def fire_twice(fn): fn(poller); fn(poller)
    poller.add_done_callback.side_effect = fire_twice
    out = list(iter_results({"a": poller}, self.integrator, config=self.cfg))
    self.assertEqual([n for n, _ in out], ["a"])

def test_iter_results_per_pdf_timeout_cancels_and_reports(self):
    cfg = PyPdfToTextConfig(base=self.cfg, overrides={"AZURE_DOCINTEL_TIMEOUT": 1})
    pending = _fake_poller(immediate_result=None, fire_callbacks=False)   # result() -> None
    t0 = time.monotonic()
    out = dict(iter_results({"slow": pending}, self.integrator, config=cfg))
    self.assertLess(time.monotonic() - t0, 4.0)
    self.assertFalse(out["slow"].succeeded)
    self.assertEqual(out["slow"].error, "OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT=1s")
    self.assertTrue(pending._polling_method.cancel_event.is_set())
    pending.result.assert_called_once()
    self.assertEqual(pending.result.call_args.args[0], 5.0)   # _CANCEL_GRACE_SECONDS

def test_iter_results_per_pdf_deadline_anchored_at_submit_time(self):
    cfg = PyPdfToTextConfig(base=self.cfg, overrides={"AZURE_DOCINTEL_TIMEOUT": 1})
    old = _fake_poller(immediate_result=None, fire_callbacks=False)
    fresh = _fake_poller(immediate_result=_make_analyze_result("fresh"), fire_callbacks=False)
    now = time.monotonic()
    gen = iter_results({"old": old, "fresh": fresh}, self.integrator, config=cfg,
                       submitted_at={"old": now - 10.0, "fresh": now})
    name, result = next(gen)             # "old" expired immediately; "fresh" still has ~1s
    self.assertEqual(name, "old"); self.assertFalse(result.succeeded)
    for fn in fresh._stored_callbacks: fn(fresh)      # fresh completes within its window
    name, result = next(gen)
    self.assertEqual(name, "fresh"); self.assertTrue(result.succeeded)

def test_iter_results_batch_timeout_reports_budget_exceeded(self):
    pending = _fake_poller(immediate_result=None, fire_callbacks=False)
    out = dict(iter_results({"p": pending}, self.integrator, config=self.cfg, batch_timeout=0.5))
    self.assertTrue(out["p"].error.startswith("OCR batch budget exceeded after 0.5s"))
    self.assertTrue(pending._polling_method.cancel_event.is_set())

def test_iter_results_expired_poller_with_late_success_yields_success(self):
    cfg = PyPdfToTextConfig(base=self.cfg, overrides={"AZURE_DOCINTEL_TIMEOUT": 1})
    lucky = _fake_poller(immediate_result=_make_analyze_result("lucky"), fire_callbacks=False)
    out = dict(iter_results({"lucky": lucky}, self.integrator, config=cfg))
    self.assertTrue(out["lucky"].succeeded)          # result() returned the real payload after cancel
    self.assertTrue(lucky._polling_method.cancel_event.is_set())

def test_iter_results_callback_does_not_call_result(self):
    """Regression: the done-callback must only enqueue; poller.result() runs on the
    coordinator thread. Mirrors TestAwaitAllThreadSafety for iter_results."""
    raw = _make_analyze_result("x")
    poller = _fake_poller(immediate_result=raw, fire_callbacks=False)
    result_thread = {}
    def _result(timeout=None):
        result_thread["ident"] = threading.get_ident()
        return raw
    poller.result.side_effect = _result
    gen = iter_results({"x": poller}, self.integrator, config=self.cfg)
    worker = threading.Thread(target=lambda: [fn(poller) for fn in poller._stored_callbacks])
    worker.start(); worker.join()
    self.assertNotIn("ident", result_thread)              # callback did not call result()
    name, result = next(gen)
    self.assertTrue(result.succeeded)
    self.assertEqual(result_thread["ident"], threading.get_ident())
```

This test requires `iter_results` to register its done-callbacks **eagerly, at call time**, not on the first `next()`. A plain generator function would defer everything until `next(gen)`, and the worker thread would find `_stored_callbacks` empty. See Step 4: `iter_results` is a regular function that registers callbacks, then returns an inner generator.

Keep the existing `TestAwaitAll` tests unchanged; they must still pass through the wrapper. Add one assertion to `test_await_all_synthesizes_budget_exceeded_on_timeout`: `self.assertEqual(results["bravo"].error, "OCR batch budget exceeded after 0.5s (pdf still pending)")`.

Also add to `tests/test_azure_docintel_integrator.py::TestAwaitOne`:

```python
def test_await_one_timeout_kwarg_overrides_config(self):
    poller = MagicMock(name="poller"); poller.result.return_value = self._populated_analyze_result(1)
    AzureDocIntelIntegrator(self.cfg).await_one(poller, pdf_name="x.pdf", timeout=2.5)
    poller.result.assert_called_once_with(2.5)
```

- [ ] **Step 2: Run to verify failure**

Run: `<PREFIX> python -m pytest tests/test_await_all.py tests/test_azure_docintel_integrator.py::TestAwaitOne -v`
Expected: FAIL with `ImportError: cannot import name 'iter_results'` / `TypeError: unexpected keyword 'timeout'`.

- [ ] **Step 3: Implement `await_one(timeout=)`**

Add the keyword; `poller.result(timeout if timeout is not None else cfg.AZURE_DOCINTEL_TIMEOUT)`. The timeout-without-exception error text stays `f"OCR timeout: poller returned no analyzeResult after {cfg.AZURE_DOCINTEL_TIMEOUT}s"`.

- [ ] **Step 4: Implement `iter_results`** in `pypdftotext/azure_docintel_integrator.py`, replacing the body of `await_all`

Imports: `import queue`, `import time`, `from collections.abc import Iterator, Mapping`. Rename `_BUDGET_GRACE_SECONDS` to `_CANCEL_GRACE_SECONDS` and update its docstring to: *"How long a cancelled poller's thread is joined during harvest before its result is treated as missing. Internal tuning constant; not configurable."*

`iter_results` is a **regular function**, not a generator function: it registers the done-callbacks immediately and returns an inner generator `_iter()`. This guarantees no completion is missed between the call and the first `next()` (production wraps the returned iterator in `tqdm` before iterating), and it is what the thread-safety test in Step 1 depends on.

Algorithm (the parts the signature and tests do not determine):

```python
cfg = config or integrator.config
start = time.monotonic()
ready: queue.Queue[str] = queue.Queue()
seen: set[str] = set()
deadline = {n: (submitted_at or {}).get(n, start) + cfg.AZURE_DOCINTEL_TIMEOUT for n in pollers}
batch_deadline = None if batch_timeout is None else start + batch_timeout
for name, poller in list(pollers.items()):
    poller.add_done_callback(lambda _pm, n=name: ready.put(n))   # never touch poller.result() here

def _harvest_expired(name, batch_expired: bool) -> OCRResult:
    poller = pollers.pop(name); seen.add(name)
    cancel_event = getattr(getattr(poller, "_polling_method", None), "cancel_event", None)
    if cancel_event is not None: cancel_event.set()
    result = integrator.await_one(poller, pdf_name=name, config=cfg, timeout=_CANCEL_GRACE_SECONDS)
    if result.succeeded: return result
    error = (f"OCR batch budget exceeded after {batch_timeout}s (pdf still pending)" if batch_expired
             else f"OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT={cfg.AZURE_DOCINTEL_TIMEOUT}s")
    return OCRResult(pdf_name=name, error=error)

while pollers:
    now = time.monotonic()
    next_deadline = min(deadline[n] for n in pollers)
    if batch_deadline is not None: next_deadline = min(next_deadline, batch_deadline)
    try:
        name = ready.get(timeout=max(0.0, next_deadline - now))
    except queue.Empty:
        now = time.monotonic()
        batch_expired = batch_deadline is not None and now >= batch_deadline
        expired = list(pollers) if batch_expired else [n for n in pollers if deadline[n] <= now]
        for n in expired:
            yield n, _harvest_expired(n, batch_expired)
        continue
    if name in seen or name not in pollers:
        continue
    seen.add(name)
    poller = pollers.pop(name)
    yield name, integrator.await_one(poller, pdf_name=name, config=cfg)
```

The `while pollers:` loop and `_harvest_expired` live inside the inner `def _iter() -> Iterator[tuple[str, OCRResult]]:`; the function ends with `return _iter()`.

Then `await_all` becomes `return dict(iter_results(pollers, integrator, config=config, batch_timeout=timeout))`, docstring reduced to: collective wait with a batch budget; per-PDF `AZURE_DOCINTEL_TIMEOUT` also applies; consumes `pollers`.

Docstring for `iter_results` must state: consumes `pollers`; per-PDF deadline anchored at `submitted_at` (or generator entry); `batch_timeout` anchored at entry; results yielded in completion order; never raises for Azure/timeout causes; callbacks only enqueue.

- [ ] **Step 5: Run to verify pass**

Run: `<PREFIX> python -m pytest tests/test_await_all.py tests/test_azure_docintel_integrator.py tests/test_batch.py -v`
Expected: all PASS (`test_batch.py` still patches `await_all`, which still exists).

- [ ] **Step 6: Lint, format, type-check**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`
Expected: clean. If pyright objects to `queue.Queue[str]` at runtime on 3.10, move the annotation into a string or rely on `from __future__ import annotations` (add it at the top of the module if absent).

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_await_all.py tests/test_azure_docintel_integrator.py
git commit -m "feat(ocr): stream batch results via iter_results with per-PDF deadlines

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `_perform_batch_ocr` harvests and applies as results complete

**Files:**
- Modify: `pypdftotext/batch.py:5-19` (imports), `:200-265` (`_perform_batch_ocr`)
- Test: `tests/test_batch.py` (every `fake_await_all` → `fake_iter_results`; `TestPerformBatchOcrSubmitAndAwait`)

**Interfaces:**
- Consumes: `iter_results`, `OCRResult` (Task 5 / Task 1); `AZURE_DOCINTEL_BATCH_TIMEOUT` (Task 4).
- Produces: `PdfExtractBatch._perform_batch_ocr` imports `iter_results` (not `await_all`) from `.azure_docintel_integrator`, records `submitted_at[name] = time.monotonic()` after each successful `submit`, and applies each result inside the `for pdf_name, result in pbar:` loop. `batch.py` no longer imports `await_all`.

- [ ] **Step 1: Update existing tests and add streaming tests in `tests/test_batch.py`**

Replace every `fake_await_all(pollers, integrator, timeout, *, config=None)` with a generator `fake_iter_results(pollers, integrator, *, config=None, submitted_at=None, batch_timeout=None)` that yields `(name, OCRResult(...))` tuples (same payloads as today), and every `patch("pypdftotext.batch.await_all", ...)` with `patch("pypdftotext.batch.iter_results", ...)`. In `test_perform_batch_ocr_uses_azure_read_no_threadpool` rename the mock variable and assert `mock_iter_results.call_count == 1`, `set(args[0].keys()) == {"a", "b"}`, `kwargs["batch_timeout"] is None`, and `set(kwargs["submitted_at"].keys()) == {"a", "b"}`.

Add to `TestPerformBatchOcrSubmitAndAwait`:

```python
def test_results_are_applied_as_they_stream(self):
    batch = PdfExtractBatch({"a": self.pdf_bytes, "b": self.pdf_bytes}, config=self.cfg)
    applied_before_second_yield = {}
    def fake_iter_results(pollers, integrator, *, config=None, submitted_at=None, batch_timeout=None):
        ext_a = batch.pdf_extracts["a"]
        yield "a", OCRResult(pdf_name="a", raw=self._make_raw(ext_a),
                             pages=["OCR_A"] * len(ext_a.ocr_page_idxs))
        # By the time the consumer asks for the next item, "a" must already be applied.
        applied_before_second_yield["a"] = ext_a.extracted_pages[ext_a.ocr_page_idxs[0]].text
        ext_b = batch.pdf_extracts["b"]
        yield "b", OCRResult(pdf_name="b", raw=self._make_raw(ext_b),
                             pages=["OCR_B"] * len(ext_b.ocr_page_idxs))
    with patch.object(AZURE_READ, "submit", return_value=MagicMock(name="poller")), \
         patch("pypdftotext.batch.iter_results", side_effect=fake_iter_results):
        batch.extract_all()
    self.assertEqual(applied_before_second_yield["a"], "OCR_A")

def test_apply_exception_for_one_pdf_does_not_stop_stream(self):
    batch = PdfExtractBatch({"boom": self.pdf_bytes, "ok": self.pdf_bytes}, config=self.cfg)
    def fake_iter_results(pollers, integrator, *, config=None, submitted_at=None, batch_timeout=None):
        for name in ("boom", "ok"):
            ext = batch.pdf_extracts[name]
            yield name, OCRResult(pdf_name=name, raw=self._make_raw(ext),
                                  pages=["OCR"] * len(ext.ocr_page_idxs))
    with patch.object(batch.pdf_extracts["boom"], "_apply_ocr_result",
                      side_effect=RuntimeError("apply failed")), \
         patch.object(AZURE_READ, "submit", return_value=MagicMock(name="poller")), \
         patch("pypdftotext.batch.iter_results", side_effect=fake_iter_results):
        results = batch.extract_all()
    ok = results["ok"]
    for idx in ok.ocr_page_idxs:
        self.assertEqual(ok.extracted_pages[idx].text, "OCR")

def test_no_client_pdfs_get_error_and_are_not_streamed(self):
    batch = PdfExtractBatch({"a": self.pdf_bytes, "b": self.pdf_bytes}, config=self.cfg)
    submit_results = iter([None, MagicMock(name="poller")])    # "a" has no client, "b" does
    def fake_iter_results(pollers, integrator, *, config=None, submitted_at=None, batch_timeout=None):
        self.assertEqual(set(pollers), {"b"}); self.assertEqual(set(submitted_at), {"b"})
        ext = batch.pdf_extracts["b"]
        yield "b", OCRResult(pdf_name="b", raw=self._make_raw(ext), pages=["OCR_B"] * len(ext.ocr_page_idxs))
    with patch.object(AZURE_READ, "submit", side_effect=lambda *a, **k: next(submit_results)), \
         patch("pypdftotext.batch.iter_results", side_effect=fake_iter_results):
        results = batch.extract_all()
    a = results["a"]
    self.assertTrue(a.extracted_pages[a.ocr_page_idxs[0]].ocr_error.startswith("OCR failed: no client available"))
    self.assertEqual(results["b"].extracted_pages[results["b"].ocr_page_idxs[0]].text, "OCR_B")
```

- [ ] **Step 2: Run to verify failure**

Run: `<PREFIX> python -m pytest tests/test_batch.py -v`
Expected: FAIL with `AttributeError: module 'pypdftotext.batch' has no attribute 'iter_results'`.

- [ ] **Step 3: Rewrite `_perform_batch_ocr`** per spec section 6

Imports: `import time`; `from .azure_docintel_integrator import AZURE_READ, iter_results` (drop `await_all`); `from azure.ai.documentintelligence import AnalyzeDocumentLROPoller` for the `pollers` annotation. Submission loop records `submitted_at[pdf_name] = time.monotonic()` immediately after a non-`None` `submit`. The harvest loop is the `tqdm(iter_results(...), total=len(pollers), desc="Awaiting OCR results", disable=..., position=..., leave=None)` iteration with `pbar.set_postfix_str(pdf_name)` and the existing per-PDF `try/except Exception` + `logger.error(...)` around `ocr_pdfs[pdf_name]._apply_ocr_result(result)`. Update the method docstring to "Submit all OCR-eligible PDFs, then apply each result as it completes." Remove the "Phase 1/2/3" comments.

- [ ] **Step 4: Run to verify pass**

Run: `<PREFIX> python -m pytest tests/test_batch.py tests/test_pdf_extract.py tests/test_await_all.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, format, type-check**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/batch.py tests/test_batch.py
git commit -m "perf(batch): apply OCR results as they complete instead of after all complete

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md` (Architecture items 3, 4, 5, 7; "OCR Triggering Logic"; "Error Handling"; "Thread Safety")

- [ ] **Step 1: Update `CLAUDE.md`**

- Item 3 (`pdf_extract.py`): add "`ocr_result: OCRResult | None` is set after OCR; its `raw` and every `ExtractedPage.azure_page` are released unless `debug_path` is set."
- Item 4 (`extracted_page.py`): add "`azure_page` is populated only when the owning `PdfExtract` has a `debug_path`."
- Item 5 (`azure_docintel_integrator.py`): replace the "Singleton pattern ... Manages OCR result caching and page mapping" lines with: "`submit()` returns an Azure poller; `await_one()` turns a poller into an `OCRResult`. Module-level `iter_results()` streams `(pdf_name, OCRResult)` in completion order with a per-PDF deadline (`AZURE_DOCINTEL_TIMEOUT`, anchored at submit) and an optional batch budget (`AZURE_DOCINTEL_BATCH_TIMEOUT`); `await_all()` is a dict-collecting wrapper. No per-thread or per-session result state is kept." Add a line: "`ocr_result.py` holds the `OCRResult` transport dataclass and the module functions `page_at_index`, `rotation_degrees`, `handwritten_ratio` that take an `AnalyzeResult` explicitly."
- Item 7 (`batch.py`): "submits every OCR-eligible PDF, then applies each result as its poller completes (streaming, bounded peak memory)."
- "Key Design Patterns" → Batch OCR bullet: replace "processes them in a single API call" with "submits one Azure request per PDF, then harvests and applies results as they complete."
- "Thread Safety": replace the singleton-client sentence with "`AzureDocIntelIntegrator` keeps no per-call state; `DocumentIntelligenceClient` instances are cached by credentials in `client_for`." Add: "Azure poller done-callbacks run on the SDK's daemon thread and must only enqueue; harvesting happens on the coordinator thread."
- "Environment Variables" is unchanged (the new field has no env var).

- [ ] **Step 2: Full test run**

Run: `<PREFIX> python -m pytest tests/ -v`
Expected: all PASS, no skips beyond the pre-existing "Sample PDF not available" guards.

- [ ] **Step 3: Lint, format, type-check**

Run: `<PREFIX> ruff check pypdftotext/ && ruff format --check pypdftotext/ && pyright pypdftotext/`
Expected: clean.

- [ ] **Step 4: Confirm no stragglers**

Run: `grep -rn "last_result\|_thread_local\|_BUDGET_GRACE_SECONDS\|result\.rotation_degrees\|result\.handwritten_ratio\|result\.page_at_index\|OCRResult(.*config=" pypdftotext/ tests/ CLAUDE.md`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe streaming batch OCR, OCRResult retention, and timeout semantics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
