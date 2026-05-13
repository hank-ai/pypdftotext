# OCR Submit-and-Await Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the session-scoped, blocking-poller OCR plumbing with a submit-and-await architecture backed by per-call `OCRResult` value objects. Fixes the silent-`None` timeout bug, eliminates cross-thread `AzureDocIntelIntegrator` state contamination (pattern P4), and removes one layer of threading from the batch path.

**Architecture:** `AzureDocIntelIntegrator.last_result` (mutable session state) is replaced with `OCRResult` (an immutable per-call value type carrying the raw `AnalyzeResult`, rendered pages, and an optional error string). The integrator gains `submit()` (non-blocking) and `await_one()` (blocking, per-PDF) methods. A new module-level `await_all()` collectively waits on a dict of pollers, cancelling pending ones on timeout via a `CancellablePolling` subclass of `LROBasePolling`. The `AZURE_READ` singleton is promoted to a credential-cached shared client. Per-thread `last_result` access is preserved via `threading.local()` with `DeprecationWarning`. `PdfExtract` gains a public `ocr_result: OCRResult | None` attribute; `ExtractedPage` gains `ocr_error: str | None`. `PdfExtractBatch._perform_batch_ocr` drops its `ThreadPoolExecutor` in favor of submit-all-then-await-all.

**Tech Stack:** Python 3.10+, `azure-ai-documentintelligence` SDK, `pypdf`, `tqdm`, `unittest.TestCase` + `unittest.mock`, `pytest` runner, `ruff`, `pyright`.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-ocr-submit-and-await-design.md`](../specs/2026-05-13-ocr-submit-and-await-design.md)

**Python environment prefix:** All `python`, `pip`, `pytest`, `ruff`, and `pyright` commands must be prefixed with conda activation. Use Git Bash (not PowerShell) for these commands:

```bash
PREFIX="source /c/Users/samha/anaconda3/etc/profile.d/conda.sh && conda activate pypdftotext &&"
```

Throughout this plan, `<PREFIX>` refers to that string. Example: `<PREFIX> pytest tests/test_config.py -v`.

---

## File Structure

**New files:**

- `pypdftotext/ocr_result.py` — `OCRResult` dataclass with `succeeded` / `handwritten_ratio` / `rotation_degrees` / `page_at_index`
- `pypdftotext/_cancellable_polling.py` — `CancellablePolling(LROBasePolling)` subclass (internal; underscore prefix)
- `tests/test_azure_docintel_integrator.py` — submit / await_one / client_for / deprecation tests
- `tests/test_cancellable_polling.py` — SDK canary
- `tests/test_await_all.py` — collective-wait behavior tests

**Modified files:**

- `pypdftotext/azure_docintel_integrator.py` — adds submit / await_one / client_for / await_all / `_BUDGET_GRACE_SECONDS`; replaces `last_result` attribute with thread-local-backed deprecated property; deprecates `handwritten_ratio` / `rotation_degrees` / `page_at_index` / `reset`
- `pypdftotext/extracted_page.py` — adds `ocr_error: str | None = None`
- `pypdftotext/_config.py` — adds `AZURE_CLIENT_POOL_MAXSIZE: int = 20`
- `pypdftotext/pdf_extract.py` — adds `ocr_result` attribute and `_apply_ocr_result()` method; rewrites `ocr()`; default `azure=AZURE_READ`; removes `self._azure.config = self.config` mutation
- `pypdftotext/batch.py` — rewrites `_perform_batch_ocr` to use submit + await_all; removes OCR `ThreadPoolExecutor`
- `pypdftotext/__init__.py` — adds `OCRResult` to imports and `__all__`

**Unchanged:** `layout.py`, `header_footer_detection.py`, `page_fingerprint.py`, `tests/test_pdf_name.py`, `tests/test_docstrings.py` (will auto-pick-up new doctests).

---

## Tasks

### Task 1: Add `AZURE_CLIENT_POOL_MAXSIZE` config field

**Files:**

- Modify: `pypdftotext/_config.py:13-50` (add to TypedDict) and `pypdftotext/_config.py:65-172` (add to dataclass)
- Test: `tests/test_config.py` (extend existing)

- [ ] **Step 1: Write failing test**

Append to `tests/test_config.py`:

```python
def test_azure_client_pool_maxsize_default():
    """AZURE_CLIENT_POOL_MAXSIZE defaults to 20."""
    from pypdftotext import PyPdfToTextConfig
    config = PyPdfToTextConfig()
    assert config.AZURE_CLIENT_POOL_MAXSIZE == 20


def test_azure_client_pool_maxsize_override():
    """AZURE_CLIENT_POOL_MAXSIZE can be set via overrides."""
    from pypdftotext import PyPdfToTextConfig
    config = PyPdfToTextConfig(overrides={"AZURE_CLIENT_POOL_MAXSIZE": 50})
    assert config.AZURE_CLIENT_POOL_MAXSIZE == 50
```

- [ ] **Step 2: Run test to verify it fails**

```bash
<PREFIX> pytest tests/test_config.py::test_azure_client_pool_maxsize_default tests/test_config.py::test_azure_client_pool_maxsize_override -v
```

Expected: FAIL with `AttributeError: 'PyPdfToTextConfig' object has no attribute 'AZURE_CLIENT_POOL_MAXSIZE'`.

- [ ] **Step 3: Add field to TypedDict**

In `pypdftotext/_config.py`, in the `PyPdfToTextConfigOverrides` TypedDict (around line 13-50), add:

```python
    AZURE_CLIENT_POOL_MAXSIZE: int
```

Place it alphabetically near the other `AZURE_*` fields.

- [ ] **Step 4: Add field to dataclass with default and docstring**

In `pypdftotext/_config.py`, in the `_ConfigMixIn` dataclass (around line 65+), after the existing `AZURE_DOCINTEL_MODEL` field, add:

```python
    AZURE_CLIENT_POOL_MAXSIZE: int = 20
    """urllib3 connection pool size for the shared DocumentIntelligenceClient.
    Default sized for MAX_WORKERS=10 with headroom. Increase if running batches
    that submit more concurrent OCR requests than this value."""
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_config.py -v
```

Expected: both new tests PASS; all existing tests in `test_config.py` continue to PASS.

- [ ] **Step 6: Run lint and type check**

```bash
<PREFIX> ruff check pypdftotext/_config.py
<PREFIX> ruff format --check pypdftotext/_config.py
<PREFIX> pyright pypdftotext/_config.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/_config.py tests/test_config.py
git commit -m "Add AZURE_CLIENT_POOL_MAXSIZE config field

Sizes the urllib3 connection pool for the shared DocumentIntelligenceClient.
Default 20, override via PyPdfToTextConfig.AZURE_CLIENT_POOL_MAXSIZE."
```

---

### Task 2: Add `ocr_error` field to `ExtractedPage`

**Files:**

- Modify: `pypdftotext/extracted_page.py`
- Test: `tests/test_pdf_extract.py` (extend existing)

- [ ] **Step 1: Write failing test**

Append to `tests/test_pdf_extract.py`:

```python
class TestExtractedPageOcrError(unittest.TestCase):
    def test_ocr_error_defaults_to_none(self):
        """ExtractedPage.ocr_error is None by default."""
        from pypdf import PdfReader
        from pypdftotext.extracted_page import ExtractedPage
        # Use any page from any sample PDF; we don't need OCR to test the field.
        # Construct a minimal mock PageObject via MagicMock.
        from unittest.mock import MagicMock
        page = ExtractedPage(page_obj=MagicMock(), handwritten_ratio=0.0, text="hello")
        self.assertIsNone(page.ocr_error)

    def test_ocr_error_can_be_set(self):
        """ExtractedPage.ocr_error accepts string assignment."""
        from unittest.mock import MagicMock
        from pypdftotext.extracted_page import ExtractedPage
        page = ExtractedPage(page_obj=MagicMock(), handwritten_ratio=0.0, text="")
        page.ocr_error = "OCR timeout: simulated"
        self.assertEqual(page.ocr_error, "OCR timeout: simulated")
```

If `tests/test_pdf_extract.py` doesn't already have a `unittest` import, add it. Check first.

- [ ] **Step 2: Run tests to verify they fail**

```bash
<PREFIX> pytest tests/test_pdf_extract.py::TestExtractedPageOcrError -v
```

Expected: FAIL with `TypeError: ExtractedPage.__init__() got an unexpected keyword argument 'ocr_error'` (if test_ocr_error_can_be_set is structured to construct with kwarg) OR `AttributeError: 'ExtractedPage' object has no attribute 'ocr_error'`.

- [ ] **Step 3: Add field to ExtractedPage dataclass**

In `pypdftotext/extracted_page.py`, in the `ExtractedPage` dataclass body (after `footer: str = ""` around line 47), add:

```python
    ocr_error: str | None = None
    """Failure reason set when OCR was attempted and did not complete successfully.
    None if OCR succeeded OR if OCR was never attempted for this page."""
```

Also update the class docstring's `Attributes:` section. Add a line after the `footer` attribute description:

```
        ocr_error: Failure reason set when OCR was attempted and failed. None
            when OCR succeeded or was never attempted.
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_pdf_extract.py -v
```

Expected: new tests PASS; all existing tests in `test_pdf_extract.py` continue to PASS.

- [ ] **Step 5: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/extracted_page.py
<PREFIX> pyright pypdftotext/extracted_page.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/extracted_page.py tests/test_pdf_extract.py
git commit -m "Add ExtractedPage.ocr_error field

Carries a human-readable failure reason when OCR was attempted but did not
complete successfully. None when OCR succeeded or was never attempted."
```

---

### Task 3: Create `OCRResult` dataclass

**Files:**

- Create: `pypdftotext/ocr_result.py`
- Test: `tests/test_pdf_extract.py` (extend) — full integrator tests come later

- [ ] **Step 1: Write failing test**

Append to `tests/test_pdf_extract.py`:

```python
class TestOCRResult(unittest.TestCase):
    def _fake_analyze_result_dict(self, num_pages=2):
        """Build a minimal dict suitable for AnalyzeResult.__init__."""
        return {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-read",
            "stringIndexType": "textElements",
            "content": "page1 text\npage2 text",
            "pages": [
                {"pageNumber": i + 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                 "unit": "inch", "spans": [{"offset": i * 11, "length": 10}],
                 "words": [], "lines": [], "selectionMarks": []}
                for i in range(num_pages)
            ],
            "styles": [],
        }

    def test_succeeded_true_when_raw_has_pages_and_no_error(self):
        from azure.ai.documentintelligence.models import AnalyzeResult
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
        result = OCRResult(
            pdf_name="x.pdf",
            config=PyPdfToTextConfig(),
            raw=raw,
            pages=["page1 text"],
        )
        self.assertTrue(result.succeeded)
        self.assertIsNone(result.error)

    def test_succeeded_false_when_error_set(self):
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        result = OCRResult(
            pdf_name="x.pdf",
            config=PyPdfToTextConfig(),
            raw=None,
            pages=[],
            error="OCR timeout: simulated",
        )
        self.assertFalse(result.succeeded)

    def test_succeeded_false_when_raw_none(self):
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        result = OCRResult(pdf_name="x.pdf", config=PyPdfToTextConfig(), raw=None, pages=[])
        self.assertFalse(result.succeeded)

    def test_page_at_index_returns_page_when_present(self):
        from azure.ai.documentintelligence.models import AnalyzeResult
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=2))
        result = OCRResult(
            pdf_name="x.pdf", config=PyPdfToTextConfig(), raw=raw, pages=["a", "b"],
        )
        page = result.page_at_index(0)
        self.assertIsNotNone(page)
        self.assertEqual(page.page_number, 1)
        page2 = result.page_at_index(1)
        self.assertEqual(page2.page_number, 2)

    def test_page_at_index_returns_none_when_missing(self):
        from azure.ai.documentintelligence.models import AnalyzeResult
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
        result = OCRResult(
            pdf_name="x.pdf", config=PyPdfToTextConfig(), raw=raw, pages=["a"],
        )
        self.assertIsNone(result.page_at_index(99))

    def test_rotation_degrees_zero_when_page_missing(self):
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        result = OCRResult(pdf_name="x.pdf", config=PyPdfToTextConfig(), raw=None, pages=[])
        self.assertEqual(result.rotation_degrees(0), 0.0)

    def test_handwritten_ratio_zero_when_no_styles(self):
        from azure.ai.documentintelligence.models import AnalyzeResult
        from pypdftotext import PyPdfToTextConfig
        from pypdftotext.ocr_result import OCRResult
        raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
        result = OCRResult(
            pdf_name="x.pdf", config=PyPdfToTextConfig(), raw=raw, pages=["a"],
        )
        self.assertEqual(result.handwritten_ratio(0), 0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
<PREFIX> pytest tests/test_pdf_extract.py::TestOCRResult -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'pypdftotext.ocr_result'`.

- [ ] **Step 3: Create `OCRResult`**

Create `pypdftotext/ocr_result.py`:

```python
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
        pages: One rendered fixed-width text string per page index submitted,
            in submission order. Empty when the OCR call failed.
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
        return (
            self.error is None
            and self.raw is not None
            and bool(self.raw.pages)
        )

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
        if self.raw is None:
            return 0.0
        page = self.page_at_index(page_index)
        if page is None or not page.spans:
            return 0.0
        page_start = min(span.offset for span in page.spans)
        page_end = max(span.offset + span.length for span in page.spans)
        # Selection marks like :selected: and :unselected: are encoded in
        # span offsets but not rendered; subtract their lengths.
        length_reduction = sum(
            sel.span.length for sel in (page.selection_marks or [])
        )
        # And subtract embedded newlines.
        length_reduction += self.raw.content[page_start:page_end].count("\n")
        page_length = page_end - page_start - length_reduction
        if page_length <= 0:
            logger.warning(
                "Cannot compute handwritten ratio for page index %s: "
                "length reduction (%s) >= span (%s, %s)",
                page_index, length_reduction, page_start, page_end,
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
```

- [ ] **Step 4: Run unit tests to verify they pass**

```bash
<PREFIX> pytest tests/test_pdf_extract.py::TestOCRResult -v
```

Expected: all PASS.

- [ ] **Step 5: Run doctests**

```bash
<PREFIX> pytest --doctest-modules pypdftotext/ocr_result.py -v
```

Expected: doctests PASS.

- [ ] **Step 6: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/ocr_result.py
<PREFIX> ruff format --check pypdftotext/ocr_result.py
<PREFIX> pyright pypdftotext/ocr_result.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/ocr_result.py tests/test_pdf_extract.py
git commit -m "Add OCRResult value type

Immutable per-OCR-call result carrying the raw AnalyzeResult, rendered page
strings, an optional error description, and helper methods (succeeded,
page_at_index, rotation_degrees, handwritten_ratio) relocated from
AzureDocIntelIntegrator. Replaces session-scoped last_result."
```

---

### Task 4: Export `OCRResult` from the public package

**Files:**

- Modify: `pypdftotext/__init__.py`
- Test: `tests/test_pdf_extract.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/test_pdf_extract.py` inside `TestOCRResult`:

```python
    def test_ocr_result_publicly_importable(self):
        """OCRResult is importable from the top-level package."""
        import pypdftotext
        self.assertTrue(hasattr(pypdftotext, "OCRResult"))
        from pypdftotext.ocr_result import OCRResult as _Direct
        self.assertIs(pypdftotext.OCRResult, _Direct)
        self.assertIn("OCRResult", pypdftotext.__all__)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
<PREFIX> pytest tests/test_pdf_extract.py::TestOCRResult::test_ocr_result_publicly_importable -v
```

Expected: FAIL — `OCRResult` not present on package.

- [ ] **Step 3: Add import and `__all__` entry**

In `pypdftotext/__init__.py`, add an import after the other internal imports (around line 16, after `from .pdf_extract import ...`):

```python
from .ocr_result import OCRResult
```

In the `__all__` list at the bottom (around line 101-113), add `"OCRResult"` alphabetically (between `"ExtractedPage"` and `"PyPdfToTextConfig"`):

```python
__all__ = [
    "constants",
    "layout",
    "AZURE_READ",
    "pdf_text_pages",
    "pdf_text_page_lines",
    "ExtractedPage",
    "OCRResult",
    "PyPdfToTextConfig",
    "PyPdfToTextConfigOverrides",
    "AllPagesRemovedError",
    "PdfExtract",
    "PdfExtractBatch",
]
```

- [ ] **Step 4: Run test, verify it passes**

```bash
<PREFIX> pytest tests/test_pdf_extract.py::TestOCRResult -v
```

Expected: all PASS.

- [ ] **Step 5: Lint**

```bash
<PREFIX> ruff check pypdftotext/__init__.py
<PREFIX> ruff format --check pypdftotext/__init__.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/__init__.py tests/test_pdf_extract.py
git commit -m "Export OCRResult from pypdftotext top-level"
```

---

### Task 5: Create `CancellablePolling` subclass

**Files:**

- Create: `pypdftotext/_cancellable_polling.py`
- Create: `tests/test_cancellable_polling.py`

- [ ] **Step 1: Write failing canary test**

Create `tests/test_cancellable_polling.py`:

```python
"""SDK canary test: CancellablePolling depends on private LROBasePolling internals.

If azure-core refactors `_delay`, `_extract_delay`, or `finished` in
LROBasePolling, the construction or behavior here will fail loudly — that's
the point. CI failure here means: go read the azure-core changelog.
"""

import threading
import time
import unittest
from unittest.mock import MagicMock

from pypdftotext._cancellable_polling import CancellablePolling


class TestCancellablePolling(unittest.TestCase):
    def _make_polling(self, lro_delay=0.05):
        """Construct a CancellablePolling with a short polling interval.

        We bypass `initialize()` (which needs a full pipeline response) by
        injecting the attributes `_delay` reads directly. This keeps the test
        focused on the cancel-event semantics rather than the SDK plumbing.
        """
        polling = CancellablePolling(lro_delay)
        # Minimal scaffolding so _extract_delay/_extract_delay don't crash.
        polling._timeout = lro_delay
        polling._pipeline_response = MagicMock()
        polling._pipeline_response.http_response.headers = {}
        return polling

    def test_cancel_event_terminates_polling(self):
        """cancel_event.set() makes _delay return immediately AND finished()
        return True."""
        polling = self._make_polling(lro_delay=10.0)  # Would normally sleep 10s
        # finished() should be False before cancel is set (super().finished()
        # raises without an _operation; we override that).
        self.assertFalse(polling.cancel_event.is_set())

        # Cancel from a sidecar thread; main thread blocks in _delay.
        def cancel_after():
            time.sleep(0.05)
            polling.cancel_event.set()
        threading.Thread(target=cancel_after, daemon=True).start()

        start = time.monotonic()
        polling._delay()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5, "expected _delay to return within 500ms after cancel")
        self.assertTrue(polling.finished())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
<PREFIX> pytest tests/test_cancellable_polling.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'pypdftotext._cancellable_polling'`.

- [ ] **Step 3: Create `CancellablePolling`**

Create `pypdftotext/_cancellable_polling.py`:

```python
"""Internal subclass of LROBasePolling that supports cooperative cancellation.

`LROPoller.result(timeout)` does not raise on timeout; it returns whatever
`_polling_method.resource()` produces given the most recent polled response.
That leaves the SDK's daemon polling thread running indefinitely, even when
the caller no longer cares about the result.

`CancellablePolling` adds a `cancel_event` Event. When set, the next call to
`_delay()` returns immediately and `finished()` reports True, causing the
polling loop in `LROBasePolling._poll()` to exit cleanly. The done callbacks
then fire (possibly capturing a just-completed result; see await_one in
azure_docintel_integrator), and the daemon thread terminates.

Brittleness note: this subclass depends on the private `_delay`,
`_extract_delay`, and `finished` methods of LROBasePolling. The test in
tests/test_cancellable_polling.py is a canary that will fail loudly if those
methods are refactored in a future azure-core version.
"""

from __future__ import annotations

import threading

from azure.core.polling.base_polling import LROBasePolling


class CancellablePolling(LROBasePolling):
    """LROBasePolling with cooperative cancellation via a threading.Event."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_event = threading.Event()

    def _delay(self) -> None:
        """Sleep for the inter-poll delay, returning immediately if cancelled.

        Overrides ``LROBasePolling._delay``, which calls
        ``self._transport.sleep(delay)`` unconditionally. ``Event.wait(timeout)``
        gives us the same blocking behavior but returns early when the event is
        set, enabling cancellation within milliseconds.
        """
        self.cancel_event.wait(self._extract_delay())

    def finished(self) -> bool:
        """True when cancelled OR when the polled operation reports done."""
        if self.cancel_event.is_set():
            return True
        return super().finished()
```

- [ ] **Step 4: Run canary test, verify it passes**

```bash
<PREFIX> pytest tests/test_cancellable_polling.py -v
```

Expected: PASS.

- [ ] **Step 5: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/_cancellable_polling.py
<PREFIX> ruff format --check pypdftotext/_cancellable_polling.py
<PREFIX> pyright pypdftotext/_cancellable_polling.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/_cancellable_polling.py tests/test_cancellable_polling.py
git commit -m "Add CancellablePolling for cooperative LRO cancellation

LROBasePolling subclass with a cancel_event. When set, the next _delay()
returns immediately and finished() reports True, causing the SDK's polling
loop to exit cleanly. Tests serve as an SDK canary for the private methods
we override."
```

---

### Task 6: Add `client_for()` module helper with credential-keyed caching

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Create: `tests/test_azure_docintel_integrator.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_azure_docintel_integrator.py`:

```python
"""Tests for AzureDocIntelIntegrator and its module-level helpers."""

import os
import threading
import unittest
import warnings
from unittest.mock import MagicMock, patch

from pypdftotext import PyPdfToTextConfig
from pypdftotext.azure_docintel_integrator import (
    AzureDocIntelIntegrator,
    AZURE_READ,
    client_for,
    _client_cache,
    _client_cache_lock,
)


class TestClientFor(unittest.TestCase):
    def setUp(self):
        # Reset the cache between tests so they're hermetic.
        with _client_cache_lock:
            _client_cache.clear()

    def _config_with_creds(self, endpoint, key, pool_maxsize=20):
        return PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": endpoint,
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": key,
            "AZURE_CLIENT_POOL_MAXSIZE": pool_maxsize,
        })

    def test_client_for(self):
        """Parametrized: caching, missing creds, pool size kwarg."""
        # Clear environment AZURE_* vars so config values take precedence.
        with patch.dict(os.environ, {}, clear=False):
            for var in ("AZURE_DOCINTEL_ENDPOINT", "AZURE_DOCINTEL_SUBSCRIPTION_KEY"):
                os.environ.pop(var, None)

            # 1. Missing creds → None.
            self.assertIsNone(client_for(self._config_with_creds("", "")))
            self.assertIsNone(client_for(self._config_with_creds("https://x.example", "")))
            self.assertIsNone(client_for(self._config_with_creds("", "key123")))

            # 2. Same (endpoint, key) → same client object.
            cfg_a1 = self._config_with_creds("https://a.example", "key-a")
            cfg_a2 = self._config_with_creds("https://a.example", "key-a")
            client_a1 = client_for(cfg_a1)
            client_a2 = client_for(cfg_a2)
            self.assertIsNotNone(client_a1)
            self.assertIs(client_a1, client_a2)

            # 3. Different key → different client.
            cfg_b = self._config_with_creds("https://a.example", "key-b")
            client_b = client_for(cfg_b)
            self.assertIsNot(client_a1, client_b)

            # 4. Different endpoint → different client.
            cfg_c = self._config_with_creds("https://c.example", "key-a")
            client_c = client_for(cfg_c)
            self.assertIsNot(client_a1, client_c)

            # 5. Pool size kwarg forwarded.
            cfg_pool = self._config_with_creds(
                "https://pool.example", "key-pool", pool_maxsize=42,
            )
            # Patch the SDK constructor to capture the transport kwarg.
            with patch(
                "pypdftotext.azure_docintel_integrator.DocumentIntelligenceClient"
            ) as mock_client_cls, patch(
                "pypdftotext.azure_docintel_integrator.RequestsTransport"
            ) as mock_transport_cls:
                mock_transport_cls.return_value = MagicMock(name="transport")
                mock_client_cls.return_value = MagicMock(name="client")
                client_for(cfg_pool)
                # RequestsTransport called with pool_maxsize=42.
                kwargs = mock_transport_cls.call_args.kwargs
                self.assertEqual(kwargs.get("connection_pool_maxsize"), 42)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py -v
```

Expected: FAIL with `ImportError: cannot import name 'client_for' from 'pypdftotext.azure_docintel_integrator'`.

- [ ] **Step 3: Add `client_for` and supporting state to the integrator module**

In `pypdftotext/azure_docintel_integrator.py`, add these imports near the top (with the existing imports):

```python
import threading

from azure.core.pipeline.transport import RequestsTransport
```

Then, immediately before the `@dataclass` declaration of `AzureDocIntelIntegrator` (around line 19), add:

```python
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
    key = (
        os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY")
        or config.AZURE_DOCINTEL_SUBSCRIPTION_KEY
    )
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
                endpoint, AzureKeyCredential(key), transport=transport,
            )
            _client_cache[cache_key] = client
            logger.info(
                "Cached new Azure OCR client: endpoint='%s', pool_maxsize=%s",
                endpoint, config.AZURE_CLIENT_POOL_MAXSIZE,
            )
        return client
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py -v
```

Expected: PASS.

- [ ] **Step 5: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py
<PREFIX> ruff format --check pypdftotext/azure_docintel_integrator.py
<PREFIX> pyright pypdftotext/azure_docintel_integrator.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_azure_docintel_integrator.py
git commit -m "Add client_for() module helper with credential-keyed cache

Lazy-caches DocumentIntelligenceClient instances by (endpoint, key) so that
credential rotation between calls transparently produces a new client. Pool
size sourced from config.AZURE_CLIENT_POOL_MAXSIZE."
```

---

### Task 7: Add `submit()` method to `AzureDocIntelIntegrator`

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Test: `tests/test_azure_docintel_integrator.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/test_azure_docintel_integrator.py`:

```python
class TestSubmit(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()

    def test_submit_returns_poller_when_client_available(self):
        """Happy path: submit returns a poller from the SDK."""
        cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        fake_poller = MagicMock(name="poller")
        fake_client = MagicMock(name="client")
        fake_client.begin_analyze_document.return_value = fake_poller
        # Patch the env-var precedence path so config creds take effect.
        with patch.dict(os.environ, {}, clear=False):
            for var in ("AZURE_DOCINTEL_ENDPOINT", "AZURE_DOCINTEL_SUBSCRIPTION_KEY"):
                os.environ.pop(var, None)
            with patch(
                "pypdftotext.azure_docintel_integrator.client_for",
                return_value=fake_client,
            ):
                integrator = AzureDocIntelIntegrator(cfg)
                poller = integrator.submit(b"pdfbytes", [0, 2], pdf_name="x.pdf")
        self.assertIs(poller, fake_poller)
        # Verify begin_analyze_document was invoked with the expected pages list
        # (1-based, comma-joined) and the model from config.
        kwargs = fake_client.begin_analyze_document.call_args.kwargs
        self.assertEqual(kwargs["model_id"], cfg.AZURE_DOCINTEL_MODEL)
        self.assertEqual(kwargs["pages"], "1,3")
        # polling kwarg should be a CancellablePolling instance.
        from pypdftotext._cancellable_polling import CancellablePolling
        self.assertIsInstance(kwargs["polling"], CancellablePolling)

    def test_submit_returns_none_when_no_client(self):
        """No creds → submit returns None and logs an error."""
        cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "",
        })
        with patch.dict(os.environ, {}, clear=False):
            for var in ("AZURE_DOCINTEL_ENDPOINT", "AZURE_DOCINTEL_SUBSCRIPTION_KEY"):
                os.environ.pop(var, None)
            integrator = AzureDocIntelIntegrator(cfg)
            with self.assertLogs(
                "pypdftotext.azure_docintel_integrator", level="ERROR"
            ) as captured:
                result = integrator.submit(b"pdf", [0])
        self.assertIsNone(result)
        self.assertTrue(
            any("no client" in line.lower() for line in captured.output),
            f"expected 'no client' in logs; got {captured.output!r}",
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestSubmit -v
```

Expected: FAIL with `AttributeError: 'AzureDocIntelIntegrator' object has no attribute 'submit'`.

- [ ] **Step 3: Implement `submit()`**

In `pypdftotext/azure_docintel_integrator.py`, add the following method to the `AzureDocIntelIntegrator` class (place it before `ocr_pages`):

```python
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
            prefix, len(pages), len(pdf),
        )
        polling = CancellablePolling(client._config.polling_interval)
        poller = client.begin_analyze_document(
            model_id=cfg.AZURE_DOCINTEL_MODEL,
            body=io.BytesIO(pdf),
            pages=",".join(str(pg + 1) for pg in pages),
            polling=polling,
        )
        return poller
```

Also add the import for `CancellablePolling` near the top of the file with the other internal imports:

```python
from ._cancellable_polling import CancellablePolling
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestSubmit -v
```

Expected: both tests PASS.

- [ ] **Step 5: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py
<PREFIX> pyright pypdftotext/azure_docintel_integrator.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_azure_docintel_integrator.py
git commit -m "Add AzureDocIntelIntegrator.submit non-blocking method

Returns an AnalyzeDocumentLROPoller backed by CancellablePolling. Accepts a
per-call config override; uses client_for() for credential-keyed client
caching."
```

---

### Task 8: Add `await_one()` method to `AzureDocIntelIntegrator`

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Test: `tests/test_azure_docintel_integrator.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_azure_docintel_integrator.py`:

```python
class TestAwaitOne(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
            "AZURE_DOCINTEL_TIMEOUT": 30,
        })

    def _populated_analyze_result(self, num_pages=1):
        from azure.ai.documentintelligence.models import AnalyzeResult
        return AnalyzeResult({
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-read",
            "stringIndexType": "textElements",
            "content": " ".join(f"page{i+1}" for i in range(num_pages)),
            "pages": [
                {"pageNumber": i + 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                 "unit": "inch", "spans": [{"offset": i * 6, "length": 5}],
                 "words": [], "lines": [], "selectionMarks": []}
                for i in range(num_pages)
            ],
            "styles": [],
        })

    def test_await_one_success(self):
        """Happy path: poller returns AnalyzeResult → OCRResult.succeeded."""
        raw = self._populated_analyze_result(num_pages=2)
        poller = MagicMock(name="poller")
        poller.result.return_value = raw
        integrator = AzureDocIntelIntegrator(self.cfg)
        result = integrator.await_one(poller, pdf_name="x.pdf")
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.pages), 2)
        self.assertIs(result.raw, raw)
        self.assertIsNone(result.error)

    def test_await_one_timeout_yields_error_result(self):
        """REGRESSION: poller.result(timeout) returns None on timeout. Must
        produce OCRResult(error="OCR timeout: ...") and not crash."""
        poller = MagicMock(name="poller")
        poller.result.return_value = None
        # Provide a polling method so cancel_event.set() doesn't crash.
        poller._polling_method = MagicMock()
        poller._polling_method.cancel_event = threading.Event()
        integrator = AzureDocIntelIntegrator(self.cfg)
        result = integrator.await_one(poller, pdf_name="x.pdf")
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.raw)
        self.assertEqual(result.pages, [])
        self.assertIsNotNone(result.error)
        self.assertTrue(result.error.startswith("OCR timeout"))
        # The cancel_event should have been set so the daemon poll thread can exit.
        self.assertTrue(poller._polling_method.cancel_event.is_set())

    def test_await_one_error_paths(self):
        """Parametrized: HttpResponseError and empty-pages produce OCRResult.error."""
        from azure.core.exceptions import HttpResponseError
        integrator = AzureDocIntelIntegrator(self.cfg)

        # 1. Poller raises HttpResponseError.
        poller_http = MagicMock(name="poller_http")
        poller_http.result.side_effect = HttpResponseError("server error")
        poller_http._polling_method = MagicMock()
        poller_http._polling_method.cancel_event = threading.Event()
        result_http = integrator.await_one(poller_http, pdf_name="x.pdf")
        self.assertFalse(result_http.succeeded)
        self.assertTrue(result_http.error.startswith("OCR failed"))
        self.assertIn("HttpResponseError", result_http.error)

        # 2. AnalyzeResult with zero pages.
        from azure.ai.documentintelligence.models import AnalyzeResult
        empty_result = AnalyzeResult({
            "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
            "stringIndexType": "textElements", "content": "",
            "pages": [], "styles": [],
        })
        poller_empty = MagicMock(name="poller_empty")
        poller_empty.result.return_value = empty_result
        result_empty = integrator.await_one(poller_empty, pdf_name="x.pdf")
        self.assertFalse(result_empty.succeeded)
        self.assertEqual(result_empty.error, "OCR failed: empty result (analyzeResult.pages was empty)")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestAwaitOne -v
```

Expected: FAIL with `AttributeError: 'AzureDocIntelIntegrator' object has no attribute 'await_one'`.

- [ ] **Step 3: Implement `await_one()`**

In `pypdftotext/azure_docintel_integrator.py`, add these imports near the top:

```python
from azure.core.exceptions import AzureError, HttpResponseError

from .ocr_result import OCRResult
```

Then add this method to `AzureDocIntelIntegrator`, immediately after `submit`:

```python
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
            pdf_name=pdf_name, config=cfg, raw=raw, pages=pages, error=error,
        )
        # Update thread-local for back-compat consumers.
        self._thread_local.last_result = raw if raw is not None else AnalyzeResult({})
        self._thread_local.ocr_result = result
        if result.succeeded:
            logger.info(
                "%sOCR completed: %d pages rendered.", prefix, len(result.pages),
            )
        else:
            logger.error("%sOCR did not complete: %s", prefix, result.error)
        return result
```

Also add `_thread_local` as a dataclass field in `AzureDocIntelIntegrator`. Find the existing fields (around lines 25-27):

```python
    config: PyPdfToTextConfig = field(default_factory=PyPdfToTextConfig)
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)
    last_result: AnalyzeResult = field(default_factory=lambda: AnalyzeResult({}), init=False)
```

Replace the `last_result` field declaration with `_thread_local`:

```python
    config: PyPdfToTextConfig = field(default_factory=PyPdfToTextConfig)
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)
    _thread_local: threading.local = field(
        default_factory=threading.local, init=False, repr=False,
    )
```

This removes `last_result` as a dataclass field. Task 9 will re-add it as a deprecated @property.

This change breaks references inside the existing `ocr_pages`, `handwritten_ratio`, `rotation_degrees`, `page_at_index` methods. **Patch those references now** so the module still imports:

In `ocr_pages` (around line 89), replace:

```python
self.last_result = poller.result(self.config.AZURE_DOCINTEL_TIMEOUT)
```

with a temporary direct read of thread-local that we'll replace fully in Task 11:

```python
# (Temporary: Task 11 swaps ocr_pages to a thin wrapper around submit+await_one.)
self._thread_local.last_result = poller.result(self.config.AZURE_DOCINTEL_TIMEOUT)
```

Then in the same method, replace every reference to `self.last_result` with `self._thread_local.last_result` (lines 94, 144, 163, 212 in the original; the line numbers shift after this edit, so search-and-replace within `ocr_pages` and the three downstream methods).

Also update `reset()` (around line 57-59):

```python
    def reset(self):
        """Clear last_result from previous run."""
        self._thread_local.last_result = AnalyzeResult({})
```

Finally, in `pypdftotext/pdf_extract.py:355-358`, update the debug-write code that reads `azure.last_result.as_dict()` and `azure.last_result.content`:

```python
                (self.debug_path / "azure.json").write_text(
                    json.dumps(azure._thread_local.last_result.as_dict(), indent=2),
                    "utf-8",
                )
                (self.debug_path / "azure_content.txt").write_text(
                    azure._thread_local.last_result.content or "",
                    "utf-8",
                )
```

These are temporary internal accesses; Task 12 replaces them with `extract.ocr_result.raw.as_dict()`.

- [ ] **Step 4: Run all tests, verify nothing broke from the field swap**

```bash
<PREFIX> pytest tests/ -v
```

Expected:
- The new `TestAwaitOne` tests PASS.
- All existing tests (`test_batch.py`, `test_pdf_extract.py`, `test_config.py`, `test_pdf_name.py`) continue to PASS — they don't access `last_result` directly.

If anything fails, the failure points to a missed internal reference. Use `<PREFIX> grep -n 'self\.last_result\|azure\.last_result' pypdftotext/*.py` (Git Bash) to find it. (Use the Grep tool in your editor for the same purpose.)

- [ ] **Step 5: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py pypdftotext/pdf_extract.py
<PREFIX> pyright pypdftotext/
```

Expected: no errors. `pyright` may complain that `AzureDocIntelIntegrator` has no `last_result` attribute (since we removed the field); ignore for now — Task 9 adds it back as a property.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py pypdftotext/pdf_extract.py tests/test_azure_docintel_integrator.py
git commit -m "Add await_one method; move last_result to thread-local storage

await_one wraps the blocking poller.result(timeout) call, catches AzureError
and the silent-None timeout case, builds an OCRResult, signals
CancellablePolling.cancel_event on failure, and updates _thread_local for
back-compat callers. Internal references to self.last_result are routed
through self._thread_local until Task 9 reintroduces the deprecated
property."
```

---

### Task 9: Reintroduce `last_result` as a deprecated property

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Test: `tests/test_azure_docintel_integrator.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_azure_docintel_integrator.py`:

```python
class TestDeprecatedSurface(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })

    def _run_one_ocr(self, integrator):
        """Helper: run a fake successful OCR so thread-local is populated."""
        from azure.ai.documentintelligence.models import AnalyzeResult
        raw = AnalyzeResult({
            "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
            "stringIndexType": "textElements", "content": "page1",
            "pages": [{"pageNumber": 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                       "unit": "inch", "spans": [{"offset": 0, "length": 5}],
                       "words": [], "lines": [], "selectionMarks": []}],
            "styles": [],
        })
        poller = MagicMock()
        poller.result.return_value = raw
        return integrator.await_one(poller, pdf_name="x.pdf"), raw

    def test_last_result_emits_deprecation_warning(self):
        integrator = AzureDocIntelIntegrator(self.cfg)
        _, raw = self._run_one_ocr(integrator)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = integrator.last_result
        self.assertEqual(len(caught), 1)
        self.assertEqual(caught[0].category, DeprecationWarning)
        self.assertIs(value, raw)

    def test_last_result_default_when_no_ocr_on_thread(self):
        """Accessing last_result before any OCR on this thread returns the
        empty AnalyzeResult sentinel (back-compat with the original init)."""
        from azure.ai.documentintelligence.models import AnalyzeResult
        integrator = AzureDocIntelIntegrator(self.cfg)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            value = integrator.last_result
        self.assertIsInstance(value, AnalyzeResult)
        self.assertIsNone(value.pages)  # AnalyzeResult({}).pages is None.
```

- [ ] **Step 2: Run test, verify it fails**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestDeprecatedSurface::test_last_result_emits_deprecation_warning tests/test_azure_docintel_integrator.py::TestDeprecatedSurface::test_last_result_default_when_no_ocr_on_thread -v
```

Expected: FAIL with `AttributeError: 'AzureDocIntelIntegrator' object has no attribute 'last_result'`.

- [ ] **Step 3: Add the `last_result` deprecated property**

In `pypdftotext/azure_docintel_integrator.py`, add `import warnings` at the top, then add this method to `AzureDocIntelIntegrator` (place it after `__post_init__`, before `create_client`):

```python
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
```

Now also update the temporary `self._thread_local.last_result = ...` reference inside `ocr_pages` to keep it untouched (Task 11 rewrites `ocr_pages`); and any debug-path internal accesses you added in Task 8 to `pypdftotext/pdf_extract.py:355-358` stay routed through `_thread_local` (still internal, no warning).

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestDeprecatedSurface -v
```

Expected: PASS.

- [ ] **Step 5: Verify existing tests still pass**

```bash
<PREFIX> pytest tests/ -v
```

Expected: all PASS. No existing test should be reading `azure.last_result` (which would now emit a DeprecationWarning that propagates in test output but doesn't fail).

- [ ] **Step 6: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py
<PREFIX> pyright pypdftotext/azure_docintel_integrator.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_azure_docintel_integrator.py
git commit -m "Reintroduce last_result as deprecated thread-local property

Thread-local-backed @property emits DeprecationWarning. Each thread sees
its own most-recent OCR result, strictly improving on the pre-rewrite race
where multiple PdfExtracts sharing one integrator clobbered each other."
```

---

### Task 10: Deprecate `handwritten_ratio` / `rotation_degrees` / `page_at_index` / `reset` on integrator

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Test: `tests/test_azure_docintel_integrator.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `TestDeprecatedSurface` in `tests/test_azure_docintel_integrator.py`:

```python
    def test_handwritten_ratio_back_compat(self):
        """Deprecated wrapper returns the same value as OCRResult.handwritten_ratio."""
        integrator = AzureDocIntelIntegrator(self.cfg)
        result, _ = self._run_one_ocr(integrator)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            integrator_value = integrator.handwritten_ratio(0)
        # Warning emitted.
        self.assertEqual(len(caught), 1)
        self.assertEqual(caught[0].category, DeprecationWarning)
        # Value matches OCRResult.
        self.assertEqual(integrator_value, result.handwritten_ratio(0))

    def test_rotation_degrees_back_compat(self):
        integrator = AzureDocIntelIntegrator(self.cfg)
        result, _ = self._run_one_ocr(integrator)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(integrator.rotation_degrees(0), result.rotation_degrees(0))
        self.assertEqual(caught[0].category, DeprecationWarning)

    def test_page_at_index_back_compat(self):
        integrator = AzureDocIntelIntegrator(self.cfg)
        result, _ = self._run_one_ocr(integrator)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertIs(integrator.page_at_index(0), result.page_at_index(0))
        self.assertEqual(caught[0].category, DeprecationWarning)

    def test_reset_clears_thread_local_with_warning(self):
        from azure.ai.documentintelligence.models import AnalyzeResult
        integrator = AzureDocIntelIntegrator(self.cfg)
        self._run_one_ocr(integrator)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            integrator.reset()
        self.assertEqual(caught[0].category, DeprecationWarning)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.assertIsNone(integrator.last_result.pages)  # back to empty sentinel
```

Also add a thread-isolation test (this is the P4 regression test):

```python
class TestThreadLocalIsolation(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()

    def test_thread_local_isolation_across_threads(self):
        """REGRESSION (P4): two threads sharing one integrator each see their
        own most-recent OCR result, not each other's."""
        from azure.ai.documentintelligence.models import AnalyzeResult

        def make_raw(label):
            return AnalyzeResult({
                "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
                "stringIndexType": "textElements", "content": label,
                "pages": [{"pageNumber": 1, "angle": 0.0, "width": 8.5,
                           "height": 11.0, "unit": "inch",
                           "spans": [{"offset": 0, "length": len(label)}],
                           "words": [], "lines": [], "selectionMarks": []}],
                "styles": [],
            })

        cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        integrator = AzureDocIntelIntegrator(cfg)
        results = {}

        def worker(label):
            poller = MagicMock()
            poller.result.return_value = make_raw(label)
            integrator.await_one(poller, pdf_name=label)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                results[label] = integrator.last_result.content

        threads = [
            threading.Thread(target=worker, args=("alpha",)),
            threading.Thread(target=worker, args=("bravo",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each thread saw its own label, not the other's.
        self.assertEqual(results["alpha"], "alpha")
        self.assertEqual(results["bravo"], "bravo")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestDeprecatedSurface tests/test_azure_docintel_integrator.py::TestThreadLocalIsolation -v
```

Expected: `TestThreadLocalIsolation` likely PASSES already (thread-local is in place). `TestDeprecatedSurface::test_*_back_compat` and `test_reset_clears_thread_local_with_warning` FAIL because the existing integrator methods don't emit warnings yet (and `page_at_index` may return None when reading from the thread-local instead of the field).

- [ ] **Step 3: Replace the integrator's `handwritten_ratio` / `rotation_degrees` / `page_at_index` / `reset` with deprecated wrappers**

In `pypdftotext/azure_docintel_integrator.py`, replace the existing implementations of these four methods with thin wrappers that delegate to `OCRResult` via thread-local:

```python
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
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py -v
```

Expected: all PASS, including `TestDeprecatedSurface` and `TestThreadLocalIsolation`.

- [ ] **Step 5: Run the whole suite to verify nothing else regresses**

```bash
<PREFIX> pytest tests/ -v
```

Expected: all PASS. Existing callers in `pdf_extract.py:373, 387, 388` invoke the integrator's deprecated methods (still functional, but will emit DeprecationWarnings during test runs). Task 12 routes those calls through `OCRResult` instead.

- [ ] **Step 6: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py
<PREFIX> pyright pypdftotext/azure_docintel_integrator.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_azure_docintel_integrator.py
git commit -m "Deprecate integrator handwritten_ratio/rotation_degrees/page_at_index/reset

Thin wrappers that read this thread's most-recent OCRResult from
_thread_local and delegate. Each emits DeprecationWarning pointing at the
OCRResult replacement. P4 cross-thread contamination regression test
included."
```

---

### Task 11: Convert `ocr_pages()` to a thin wrapper

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Test: existing `tests/test_batch.py` / `tests/test_pdf_extract.py` continue to exercise this path

- [ ] **Step 1: Replace `ocr_pages` body**

In `pypdftotext/azure_docintel_integrator.py`, replace the entire `ocr_pages` method body with:

```python
    def ocr_pages(
        self, pdf: bytes, pages: list[int], pdf_name: str = "",
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
```

Remove `create_client()`'s body and have it delegate to `client_for()` for back-compat:

```python
    def create_client(self) -> bool:
        """Create or retrieve the cached DocumentIntelligenceClient.

        Returns True if a client is available (newly cached or pre-existing),
        False otherwise. The actual client object is stored on
        ``self.client`` for back-compat with any callers that introspect it.
        """
        self.client = client_for(self.config)
        if self.client is None:
            endpoint = (
                os.getenv("AZURE_DOCINTEL_ENDPOINT") or self.config.AZURE_DOCINTEL_ENDPOINT
            )
            logger.error("Failed to obtain Azure OCR Client at endpoint='%s'", endpoint)
            return False
        return True
```

- [ ] **Step 2: Run the full suite**

```bash
<PREFIX> pytest tests/ -v
```

Expected: all PASS. The existing test_batch.py and test_pdf_extract.py exercise `ocr_pages` indirectly through `PdfExtract.ocr` and `PdfExtractBatch._perform_batch_ocr`. They should still pass — output equality should be preserved because `await_one` builds `pages` via the same `layout.fixed_width_page` calls.

If a test fails, the most likely cause is the `tqdm` progress bar that previously wrapped the page iteration. The new `await_one` does not emit a per-page progress bar. If existing tests assert on progress-bar output (unlikely), reconcile by either keeping the old progress bar in `ocr_pages` (wrap the `result.pages` build) or updating the test.

- [ ] **Step 3: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py
<PREFIX> pyright pypdftotext/azure_docintel_integrator.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py
git commit -m "Convert ocr_pages to a thin wrapper over submit + await_one

Legacy callers see identical behavior — same signature, same return shape,
same logged events on success. Failures now log at ERROR and return an
empty list instead of raising AttributeError on a None last_result."
```

---

### Task 12: Add `_BUDGET_GRACE_SECONDS` constant and `await_all()` helper

**Files:**

- Modify: `pypdftotext/azure_docintel_integrator.py`
- Create: `tests/test_await_all.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_await_all.py`:

```python
"""Tests for the module-level await_all collective wait helper."""

import threading
import time
import unittest
from unittest.mock import MagicMock

from pypdftotext import PyPdfToTextConfig
from pypdftotext.azure_docintel_integrator import (
    AzureDocIntelIntegrator,
    await_all,
    _BUDGET_GRACE_SECONDS,
)


def _fake_poller(immediate_result=None, raises=None):
    """Build a mocked poller whose done callback fires when .result() is
    called the way await_one calls it. We simulate the SDK's callback
    semantics by invoking registered callbacks during result()."""
    poller = MagicMock()
    callbacks = []
    poller.add_done_callback.side_effect = lambda fn: callbacks.append(fn)
    if raises is not None:
        def _raises(*args, **kwargs):
            for cb in callbacks:
                cb(poller)
            raise raises
        poller.result.side_effect = _raises
    else:
        def _returns(*args, **kwargs):
            for cb in callbacks:
                cb(poller)
            return immediate_result
        poller.result.side_effect = _returns
    poller._polling_method = MagicMock()
    poller._polling_method.cancel_event = threading.Event()
    return poller


def _make_analyze_result(label):
    from azure.ai.documentintelligence.models import AnalyzeResult
    return AnalyzeResult({
        "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
        "stringIndexType": "textElements", "content": label,
        "pages": [{"pageNumber": 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                   "unit": "inch", "spans": [{"offset": 0, "length": len(label)}],
                   "words": [], "lines": [], "selectionMarks": []}],
        "styles": [],
    })


class TestAwaitAll(unittest.TestCase):
    def setUp(self):
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        self.integrator = AzureDocIntelIntegrator(self.cfg)

    def test_await_all_collects_all_results(self):
        """N=0, N=1, N=3 all return well-formed dicts."""
        for size in (0, 1, 3):
            with self.subTest(size=size):
                pollers = {
                    f"pdf{i}": _fake_poller(immediate_result=_make_analyze_result(f"pdf{i}"))
                    for i in range(size)
                }
                results = await_all(
                    pollers, self.integrator, timeout=5.0, config=self.cfg,
                )
                self.assertEqual(len(results), size)
                for name in pollers:
                    self.assertTrue(results[name].succeeded)

    def test_await_all_synthesizes_budget_exceeded_on_timeout(self):
        """Pollers that don't complete by the budget get synthesized error
        results; a poller that completes during the grace window wins."""
        from azure.ai.documentintelligence.models import AnalyzeResult

        # Poller A completes immediately.
        poller_a = _fake_poller(immediate_result=_make_analyze_result("alpha"))

        # Poller B blocks indefinitely (simulates "still polling Azure").
        poller_b = MagicMock()
        b_callbacks = []
        poller_b.add_done_callback.side_effect = lambda fn: b_callbacks.append(fn)
        b_block = threading.Event()

        def _b_result(timeout=None):
            b_block.wait(timeout)
            # When cancel event is set, return None (simulating SDK behavior).
            return None
        poller_b.result.side_effect = _b_result
        poller_b._polling_method = MagicMock()
        poller_b._polling_method.cancel_event = threading.Event()

        pollers = {"alpha": poller_a, "bravo": poller_b}
        # Use a short budget so timeout fires fast.
        results = await_all(pollers, self.integrator, timeout=0.5, config=self.cfg)

        self.assertTrue(results["alpha"].succeeded)
        self.assertFalse(results["bravo"].succeeded)
        self.assertTrue(results["bravo"].error.startswith("OCR batch budget exceeded"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
<PREFIX> pytest tests/test_await_all.py -v
```

Expected: FAIL — `ImportError: cannot import name 'await_all'`.

- [ ] **Step 3: Implement `_BUDGET_GRACE_SECONDS` and `await_all`**

In `pypdftotext/azure_docintel_integrator.py`, near `client_for`, add:

```python
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

    Phase 1: register a done callback on each poller. The callback builds
    an OCRResult via ``integrator.await_one`` (which returns near-instantly
    when called from inside the callback because the poller is already
    done) and records it in a shared results dict.

    Phase 2: block until either all callbacks have fired or the overall
    ``timeout`` elapses.

    Phase 3 (on timeout): set ``cancel_event`` on every poller whose result
    is still missing. The SDK's daemon poll loop exits within milliseconds
    and the callback fires (possibly capturing a lucky-race late result).
    Wait up to ``_BUDGET_GRACE_SECONDS`` for these late callbacks.

    Phase 4: synthesize ``OCRResult(error="OCR batch budget exceeded ...")``
    for any name still missing.

    Args:
        pollers: dict mapping pdf_name to poller.
        integrator: the integrator whose await_one is used. Its
            ``_thread_local`` is updated for the LAST callback to fire (race
            outcome; matches today's "undefined in batch context" semantics
            for AZURE_READ.last_result).
        timeout: overall budget in seconds. None means wait indefinitely.
        config: optional per-batch config override for ``await_one``.

    Returns:
        dict mapping pdf_name to OCRResult. Never raises.
    """
    cfg = config or integrator.config
    results: dict[str, OCRResult] = {}
    done_event = threading.Event()
    lock = threading.Lock()
    total = len(pollers)
    if total == 0:
        return results

    def _on_done(name: str, poller) -> None:
        # poller.result(small_timeout) is safe here because the poller is
        # done. We pass a tiny timeout so a bug in our callback path can't
        # hang forever.
        result = integrator.await_one(poller, pdf_name=name, config=cfg)
        with lock:
            if name not in results:
                results[name] = result
                if len(results) == total:
                    done_event.set()

    for name, poller in pollers.items():
        # Capture name via default arg to avoid late-binding closure bug.
        poller.add_done_callback(lambda p, n=name: _on_done(n, p))

    if not done_event.wait(timeout):
        # Budget elapsed. Cancel pending pollers and wait briefly for late
        # callbacks (lucky-race window).
        pending_names = [n for n in pollers if n not in results]
        for n in pending_names:
            polling_method = getattr(pollers[n], "_polling_method", None)
            cancel_event = getattr(polling_method, "cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
        done_event.wait(_BUDGET_GRACE_SECONDS)
        # Synthesize errors for any still missing.
        with lock:
            for n in pollers:
                if n not in results:
                    results[n] = OCRResult(
                        pdf_name=n, config=cfg, raw=None, pages=[],
                        error=(
                            f"OCR batch budget exceeded after {timeout}s "
                            f"(pdf still pending)"
                        ),
                    )
    return results
```

Also add to the imports near the top of the file:

```python
from collections.abc import Mapping
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_await_all.py -v
```

Expected: PASS.

- [ ] **Step 5: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/azure_docintel_integrator.py
<PREFIX> pyright pypdftotext/azure_docintel_integrator.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pypdftotext/azure_docintel_integrator.py tests/test_await_all.py
git commit -m "Add await_all collective wait with cooperative cancellation

Registers done callbacks on each poller, blocks until all complete or the
budget elapses. On budget exceeded, sets cancel_event on pending pollers,
waits _BUDGET_GRACE_SECONDS for late callbacks, then synthesizes 'OCR
batch budget exceeded' OCRResults for any still missing."
```

---

### Task 13: Add `PdfExtract.ocr_result` + `_apply_ocr_result()`; rewrite `ocr()`

**Files:**

- Modify: `pypdftotext/pdf_extract.py`
- Test: `tests/test_pdf_extract.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_pdf_extract.py`:

```python
class TestOcrEndToEnd(unittest.TestCase):
    """Coverage-based: success, timeout, azure-error variants of PdfExtract.ocr."""

    def setUp(self):
        from pathlib import Path
        self.samples_dir = Path("samples")
        self.pdf_path = self.samples_dir / "all70th.pdf"
        if not self.pdf_path.exists():
            self.skipTest("Sample PDF not available")
        self.cfg = PyPdfToTextConfig(overrides={
            "DISABLE_OCR": False,
            "MIN_LINES_OCR_TRIGGER": 1,
            "TRIGGER_OCR_PAGE_RATIO": 0.5,
            "DISABLE_PROGRESS_BAR": True,
            "MAX_CHARS_PER_PDF_PAGE": 25000,
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })

    def _build_success_ocr_result(self, extract):
        """Build a synthetic successful OCRResult for the pages extract needs."""
        from pypdftotext.ocr_result import OCRResult
        from azure.ai.documentintelligence.models import AnalyzeResult
        ocr_idxs = extract.ocr_page_idxs
        raw = AnalyzeResult({
            "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
            "stringIndexType": "textElements",
            "content": " ".join(f"page{i}" for i in ocr_idxs),
            "pages": [
                {"pageNumber": i + 1, "angle": 0.0, "width": 8.5,
                 "height": 11.0, "unit": "inch",
                 "spans": [{"offset": idx_pos * 6, "length": 5}],
                 "words": [], "lines": [], "selectionMarks": []}
                for idx_pos, i in enumerate(ocr_idxs)
            ],
            "styles": [],
        })
        pages = [f"OCR_PAGE_{i}" for i in ocr_idxs]
        return OCRResult(
            pdf_name=extract.pdf_name, config=extract.config,
            raw=raw, pages=pages,
        )

    def test_ocr_end_to_end_success(self):
        """Mocked azure produces a successful OCRResult; ExtractedPages reflect it."""
        from pypdftotext.pdf_extract import PdfExtract
        from unittest.mock import patch
        # Force OCR by suppressing embedded text on every page.
        cfg = PyPdfToTextConfig(base=self.cfg, overrides={"SUPPRESS_EMBEDDED_TEXT": True})
        extract = PdfExtract(self.pdf_path.read_bytes(), config=cfg, pdf_name="t.pdf")
        # Trigger _extract_pages so ocr_page_idxs is populated, but don't run ocr() yet.
        extract._extracted_pages = None
        with patch(
            "pypdftotext.pdf_extract.AzureDocIntelIntegrator"
        ) as mock_cls:
            mock_azure = mock_cls.return_value
            # When ocr() is invoked, return a fake OCRResult via await_one.
            mock_azure.submit.return_value = MagicMock(name="poller")
            mock_azure.await_one.side_effect = lambda p, **kw: self._build_success_ocr_result(extract)
            _ = extract.extracted_pages
        self.assertIsNotNone(extract.ocr_result)
        self.assertTrue(extract.ocr_result.succeeded)
        # Every page that was OCR'd has source="OCR" and non-empty text.
        for idx in extract.ocr_page_idxs:
            self.assertEqual(extract.extracted_pages[idx].source, "OCR")
            self.assertTrue(extract.extracted_pages[idx].text.startswith("OCR_PAGE_"))
            self.assertIsNone(extract.extracted_pages[idx].ocr_error)

    def test_ocr_end_to_end_failure_sets_ocr_error(self):
        """Mocked azure returns OCRResult with error; ExtractedPages get ocr_error."""
        from pypdftotext.pdf_extract import PdfExtract
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch
        cfg = PyPdfToTextConfig(base=self.cfg, overrides={"SUPPRESS_EMBEDDED_TEXT": True})
        extract = PdfExtract(self.pdf_path.read_bytes(), config=cfg, pdf_name="t.pdf")
        extract._extracted_pages = None
        failure = OCRResult(
            pdf_name="t.pdf", config=cfg, raw=None, pages=[],
            error="OCR timeout: simulated for test",
        )
        with patch(
            "pypdftotext.pdf_extract.AzureDocIntelIntegrator"
        ) as mock_cls:
            mock_azure = mock_cls.return_value
            mock_azure.submit.return_value = MagicMock(name="poller")
            mock_azure.await_one.return_value = failure
            _ = extract.extracted_pages
        self.assertIsNotNone(extract.ocr_result)
        self.assertFalse(extract.ocr_result.succeeded)
        for idx in extract.ocr_page_idxs:
            self.assertEqual(
                extract.extracted_pages[idx].ocr_error, "OCR timeout: simulated for test",
            )
            self.assertEqual(extract.extracted_pages[idx].text, "")
            self.assertEqual(extract.extracted_pages[idx].source, "embedded")

    def test_ocr_logs_no_false_success_on_failure(self):
        """The misleading 'OCR'd successfully' log must not fire on failure."""
        from pypdftotext.pdf_extract import PdfExtract
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch
        cfg = PyPdfToTextConfig(base=self.cfg, overrides={"SUPPRESS_EMBEDDED_TEXT": True})
        extract = PdfExtract(self.pdf_path.read_bytes(), config=cfg, pdf_name="t.pdf")
        extract._extracted_pages = None
        failure = OCRResult(
            pdf_name="t.pdf", config=cfg, raw=None, pages=[],
            error="OCR failed: HttpResponseError: simulated",
        )
        with patch("pypdftotext.pdf_extract.AzureDocIntelIntegrator") as mock_cls:
            mock_azure = mock_cls.return_value
            mock_azure.submit.return_value = MagicMock(name="poller")
            mock_azure.await_one.return_value = failure
            with self.assertLogs("pypdftotext", level="INFO") as captured:
                # Trigger the OCR path.
                _ = extract.extracted_pages
        # Assert no "successfully" log emitted at INFO.
        success_lines = [line for line in captured.output if "successfully" in line.lower()]
        self.assertEqual(success_lines, [])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
<PREFIX> pytest tests/test_pdf_extract.py::TestOcrEndToEnd -v
```

Expected: FAIL — `AttributeError: 'PdfExtract' object has no attribute 'ocr_result'` (or similar).

- [ ] **Step 3: Add `ocr_result` attribute and `_apply_ocr_result` to `PdfExtract`**

In `pypdftotext/pdf_extract.py`:

1. Add import near the top (with other internal imports):

```python
from .azure_docintel_integrator import AZURE_READ
from .ocr_result import OCRResult
```

2. Initialize `self.ocr_result` in `PdfExtract.__init__` (around line 107, near where `self.ocr_page_idxs` is initialized):

```python
        self.ocr_page_idxs: list[int] = []
        self.ocr_result: OCRResult | None = None  # set by ocr() if OCR runs
```

3. Update the docstring `Args/KwArgs` section of `__init__` to mention `self.ocr_result` under a new heading after `KwArgs`:

```
    Attributes (set after extraction):
        ocr_result: Result of the OCR call if OCR ran; None if OCR was
            never attempted (e.g. all pages had sufficient embedded text).
            On failure, ocr_result.succeeded is False and ocr_result.error
            describes the failure.
```

4. Rewrite `PdfExtract.ocr` (currently lines 326-392). Replace the entire method with:

```python
    def ocr(self, azure: AzureDocIntelIntegrator | None = None):
        """Run OCR on the pages identified during _extract_pages, applying
        results to self.extracted_pages.

        Args:
            azure: integrator to use. Defaults to the canonical AZURE_READ
                singleton; pass an explicit instance if you need credential
                isolation across PdfExtract instances.
        """
        if azure is None:
            azure = AZURE_READ
        if (
            len(self.ocr_page_idxs) / len(self.extracted_pages)
            < self.config.TRIGGER_OCR_PAGE_RATIO
        ):
            return
        poller = azure.submit(
            self.body, self.ocr_page_idxs, pdf_name=self.pdf_name, config=self.config,
        )
        if poller is None:
            # No client available; await_one wasn't called so log here.
            logger.error(
                "[%s] OCR submit failed: no client available", self.pdf_name,
            )
            return
        result = azure.await_one(poller, pdf_name=self.pdf_name, config=self.config)
        self._apply_ocr_result(result)

    def _apply_ocr_result(self, result: OCRResult) -> None:
        """Apply an OCRResult to self.extracted_pages.

        On success: each ocr_page_idx page gets its rendered text,
        source="OCR", handwritten_ratio, and azure_page populated; rotations
        are applied where Azure reports non-zero angles.

        On failure: every ocr_page_idx page gets ocr_error set; text stays
        empty, source stays "embedded", azure_page stays None.

        In both cases self.ocr_result is stashed for downstream introspection.
        """
        self.ocr_result = result
        rotated_pages = False
        if not result.succeeded:
            for og_pg_idx in self.ocr_page_idxs:
                self.extracted_pages[og_pg_idx].ocr_error = result.error
            return
        # Replacement substitutions only run in batch mode (single-mode
        # replacements happen globally in _extract_pages).
        replacements = (
            [
                (old_bytes.decode(), new_bytes.decode())
                for old_bytes, new_bytes in (self.config.REPLACE_BYTE_CODES or {}).items()
            ]
            if self._batch_mode
            else []
        )
        for ocr_idx, og_pg_idx in enumerate(self.ocr_page_idxs):
            ext_pg = self.extracted_pages[og_pg_idx]
            txt = result.pages[ocr_idx]
            if len(txt) > self.config.MAX_CHARS_PER_PDF_PAGE:
                logger.warning(
                    "[%s] Clearing corrupt OCR text pg_idx=%s; len(txt)=%s > %s char limit."
                    " Does page contain multiple text orientations?",
                    self.pdf_name, og_pg_idx, len(txt),
                    self.config.MAX_CHARS_PER_PDF_PAGE,
                )
                txt = ""
            elif rotation := result.rotation_degrees(og_pg_idx):
                if applied_rotation := -90 * int(round(rotation / 90.0)):
                    rotated_pages = True
                    ext_pg.page_obj.rotation += applied_rotation
            if replacements and txt:
                for old_, new_ in replacements:
                    txt = txt.replace(old_, new_)
            ext_pg.text = txt
            ext_pg.source = "OCR"
            ext_pg.handwritten_ratio = result.handwritten_ratio(og_pg_idx)
            ext_pg.azure_page = result.page_at_index(og_pg_idx)
            ext_pg.ocr_error = None
        if rotated_pages:
            logger.debug("[%s] Regenerating body with corrected page orientations.", self.pdf_name)
            self._regenerate_body()
```

5. Update `_extract_pages` (around lines 302-308) to drop the `self._azure.config = self.config` mutation and pass the integrator directly:

```python
        if self._azure is not None:
            azure = self._azure
        else:
            azure = AZURE_READ

        # Parallel Azure OCR API calls will be made later if in batch mode.
        if not self._batch_mode:
            self.ocr(azure)
```

(The previous code created a fresh `AzureDocIntelIntegrator(self.config)` per call; the new code uses `AZURE_READ` by default and respects an explicit `self._azure` injection.)

6. Update the debug-write code (formerly lines 350-359 in the original; line numbers will have shifted). The references to `azure.last_result.as_dict()` / `azure.last_result.content` introduced as temporary in Task 8 (`azure._thread_local.last_result...`) should now read from `self.ocr_result.raw`:

```python
            if self.debug_path and self.ocr_result is not None:
                (self.debug_path / "ocr_pages.json").write_text(
                    json.dumps(self.ocr_result.pages, indent=2, default=str),
                    "utf-8",
                )
                if self.ocr_result.raw is not None:
                    (self.debug_path / "azure.json").write_text(
                        json.dumps(self.ocr_result.raw.as_dict(), indent=2),
                        "utf-8",
                    )
                    (self.debug_path / "azure_content.txt").write_text(
                        self.ocr_result.raw.content or "",
                        "utf-8",
                    )
```

Place this block at the end of `_apply_ocr_result` (after `if rotated_pages:`).

Wait — `self.debug_path` is a `PdfExtract` instance attribute. `_apply_ocr_result` is a method on `PdfExtract`, so `self.debug_path` is in scope. Good.

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_pdf_extract.py -v
```

Expected: new `TestOcrEndToEnd` tests PASS; existing tests in `test_pdf_extract.py` continue to PASS.

- [ ] **Step 5: Run full suite**

```bash
<PREFIX> pytest tests/ -v
```

Expected: all PASS. `test_batch.py` will continue to pass because `_perform_batch_ocr` still uses the legacy path (Task 14 changes it).

- [ ] **Step 6: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/pdf_extract.py
<PREFIX> pyright pypdftotext/pdf_extract.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add pypdftotext/pdf_extract.py tests/test_pdf_extract.py
git commit -m "Rewrite PdfExtract.ocr to use submit + await_one + OCRResult

Adds PdfExtract.ocr_result (None until OCR runs), _apply_ocr_result handles
success and failure uniformly: success paths populate text/source/handwritten/
azure_page; failure paths set ExtractedPage.ocr_error. Default integrator
is now the AZURE_READ singleton. Removes the self._azure.config = self.config
mutation in favor of per-call config kwargs."
```

---

### Task 14: Rewrite `PdfExtractBatch._perform_batch_ocr` for submit-and-await

**Files:**

- Modify: `pypdftotext/batch.py`
- Test: `tests/test_batch.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_batch.py`:

```python
class TestPerformBatchOcrSubmitAndAwait(unittest.TestCase):
    """Coverage tests for the rewritten _perform_batch_ocr."""

    def setUp(self):
        from pathlib import Path
        self.samples_dir = Path("samples")
        if not (self.samples_dir / "all70th.pdf").exists():
            self.skipTest("Sample PDF not available")
        self.pdf_bytes = (self.samples_dir / "all70th.pdf").read_bytes()
        self.cfg = PyPdfToTextConfig(overrides={
            "DISABLE_OCR": False,
            "MIN_LINES_OCR_TRIGGER": 1,
            "TRIGGER_OCR_PAGE_RATIO": 0.5,
            "DISABLE_PROGRESS_BAR": True,
            "MAX_CHARS_PER_PDF_PAGE": 25000,
            "SUPPRESS_EMBEDDED_TEXT": True,
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })

    def test_perform_batch_ocr_uses_azure_read_no_threadpool(self):
        """Batch dispatches via AZURE_READ.submit + module-level await_all;
        does NOT instantiate a ThreadPoolExecutor for OCR."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"a": self.pdf_bytes, "b": self.pdf_bytes}, config=self.cfg)
        fake_poller = MagicMock(name="poller")

        def fake_await_all(pollers, integrator, timeout, *, config=None):
            return {
                name: OCRResult(
                    pdf_name=name, config=config or self.cfg, raw=None,
                    pages=["OCR_TEXT"] * len(batch.pdf_extracts[name].ocr_page_idxs),
                    error=None,
                )
                for name in pollers
            }

        with patch.object(AZURE_READ, "submit", return_value=fake_poller) as mock_submit, \
             patch("pypdftotext.batch.await_all", side_effect=fake_await_all) as mock_await_all, \
             patch("pypdftotext.batch.ThreadPoolExecutor") as mock_pool:
            batch.extract_all()
        # Submit was called once per PDF.
        self.assertEqual(mock_submit.call_count, 2)
        # await_all was called once with both pollers.
        self.assertEqual(mock_await_all.call_count, 1)
        args, kwargs = mock_await_all.call_args
        pollers_arg = args[0] if args else kwargs["pollers"]
        self.assertEqual(set(pollers_arg.keys()), {"a", "b"})
        # ThreadPoolExecutor must NOT be constructed inside _perform_batch_ocr.
        # (It is still used in _pull_s3_parallel for S3 fetches, but our inputs
        # are bytes — _pull_s3_parallel short-circuits.)
        self.assertEqual(mock_pool.call_count, 0)

    def test_partial_failure_does_not_crash_batch(self):
        """One PDF fails OCR (error result); the other succeeds; batch returns both."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"good": self.pdf_bytes, "bad": self.pdf_bytes}, config=self.cfg)
        fake_poller = MagicMock(name="poller")

        def fake_await_all(pollers, integrator, timeout, *, config=None):
            good_pages = ["OCR_GOOD"] * len(batch.pdf_extracts["good"].ocr_page_idxs)
            return {
                "good": OCRResult(
                    pdf_name="good", config=config or self.cfg, raw=None,
                    pages=good_pages, error=None,
                ),
                "bad": OCRResult(
                    pdf_name="bad", config=config or self.cfg, raw=None,
                    pages=[], error="OCR timeout: simulated",
                ),
            }

        with patch.object(AZURE_READ, "submit", return_value=fake_poller), \
             patch("pypdftotext.batch.await_all", side_effect=fake_await_all):
            results = batch.extract_all()
        # Both PDFs are in the results dict.
        self.assertIn("good", results)
        self.assertIn("bad", results)
        # Good has OCR text populated.
        good_extract = results["good"]
        for idx in good_extract.ocr_page_idxs:
            self.assertEqual(good_extract.extracted_pages[idx].text, "OCR_GOOD")
            self.assertIsNone(good_extract.extracted_pages[idx].ocr_error)
        # Bad has ocr_error set on all OCR pages.
        bad_extract = results["bad"]
        for idx in bad_extract.ocr_page_idxs:
            self.assertEqual(
                bad_extract.extracted_pages[idx].ocr_error, "OCR timeout: simulated",
            )
            self.assertEqual(bad_extract.extracted_pages[idx].text, "")
```

Note: The lambda inside the OCRResult succeeded check is intentionally `raw=None` even though `succeeded` would normally need a real `AnalyzeResult`. The `_apply_ocr_result` method only checks `result.succeeded` for the success branch; with `raw=None`, `succeeded` is False. **Adjust the success test mock to use a populated AnalyzeResult so success path is exercised.** Replace the `good` OCRResult with one carrying a real raw object built like in `TestAwaitOne._populated_analyze_result`. Use the same builder pattern.

For simplicity in the test, override `_apply_ocr_result` directly per case OR construct a real `AnalyzeResult`. Here's the corrected `fake_await_all` for `test_perform_batch_ocr_uses_azure_read_no_threadpool` and the good case in `test_partial_failure_does_not_crash_batch`:

```python
        def _make_raw(extract):
            from azure.ai.documentintelligence.models import AnalyzeResult
            ocr_idxs = extract.ocr_page_idxs
            return AnalyzeResult({
                "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
                "stringIndexType": "textElements",
                "content": " ".join(f"p{i}" for i in ocr_idxs),
                "pages": [
                    {"pageNumber": i + 1, "angle": 0.0, "width": 8.5,
                     "height": 11.0, "unit": "inch",
                     "spans": [{"offset": pos * 3, "length": 2}],
                     "words": [], "lines": [], "selectionMarks": []}
                    for pos, i in enumerate(ocr_idxs)
                ],
                "styles": [],
            })
```

Adjust the OCRResult construction in both tests to use `raw=_make_raw(extract)` for successful entries.

- [ ] **Step 2: Run tests to verify they fail**

```bash
<PREFIX> pytest tests/test_batch.py::TestPerformBatchOcrSubmitAndAwait -v
```

Expected: FAIL — `_perform_batch_ocr` currently uses `ThreadPoolExecutor`, so `mock_pool.call_count` will be ≥ 1. Also `await_all` is not yet imported in `batch.py`.

- [ ] **Step 3: Rewrite `_perform_batch_ocr`**

In `pypdftotext/batch.py`:

1. Update the imports near the top:

```python
from .azure_docintel_integrator import AzureDocIntelIntegrator, await_all
from .. import AZURE_READ  # (or `from pypdftotext import AZURE_READ`; pick whichever resolves)
```

The cleanest is to import from the integrator module directly:

```python
from .azure_docintel_integrator import (
    AzureDocIntelIntegrator,
    AZURE_READ,
    await_all,
)
```

2. Replace the body of `_perform_batch_ocr` (lines 180-222 in the current file) with:

```python
    def _perform_batch_ocr(self) -> dict[str, PdfExtract]:
        """Submit all OCR-eligible PDFs, await collectively, apply results."""
        ocr_pdfs = {
            pdf_name: extract
            for pdf_name, extract in self.pdf_extracts.items()
            if (
                len(extract.ocr_page_idxs) / len(extract.extracted_pages)
                >= self.config.TRIGGER_OCR_PAGE_RATIO
            )
        }
        if not ocr_pdfs:
            logger.debug(
                "No PDFs met OCR criteria (MIN_LINES_OCR_TRIGGER=%s, TRIGGER_OCR_PAGE_RATIO=%s)",
                self.config.MIN_LINES_OCR_TRIGGER,
                self.config.TRIGGER_OCR_PAGE_RATIO,
            )
            return self.pdf_extracts
        total_pages = sum(len(ext.ocr_page_idxs) for ext in ocr_pdfs.values())
        logger.info(
            "Submitting %s pages across %s PDFs for batch OCR", total_pages, len(ocr_pdfs),
        )
        # Phase 1: submit all
        pollers = {}
        for pdf_name, extract in ocr_pdfs.items():
            poller = AZURE_READ.submit(
                extract.body, extract.ocr_page_idxs,
                pdf_name=pdf_name, config=self.config,
            )
            if poller is not None:
                pollers[pdf_name] = poller
            else:
                # No client available; record failure inline.
                from .ocr_result import OCRResult
                extract._apply_ocr_result(OCRResult(
                    pdf_name=pdf_name, config=self.config, raw=None, pages=[],
                    error="OCR failed: no client available "
                          "(check AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_SUBSCRIPTION_KEY)",
                ))
        # Phase 2: collective wait
        results = await_all(
            pollers, AZURE_READ,
            timeout=self.config.AZURE_DOCINTEL_TIMEOUT,
            config=self.config,
        )
        # Phase 3: apply
        for pdf_name, extract in ocr_pdfs.items():
            if pdf_name in results:
                try:
                    extract._apply_ocr_result(results[pdf_name])
                except Exception as e:  # noqa: BLE001  # batch survives per-PDF apply failures
                    logger.error(
                        "PdfExtractBatch apply error for %s: %s", pdf_name, e,
                        exc_info=logger.getEffectiveLevel() == logging.DEBUG,
                    )
        return self.pdf_extracts
```

3. Remove the now-unused `_ocr_single_pdf` method (lines 224-250 in the current file).

4. Remove the now-unused imports from the top of `batch.py`:

```python
# Remove:
from concurrent.futures import Future, as_completed
from azure.core.exceptions import AzureError
```

Keep `ThreadPoolExecutor` import — it's still used by `_pull_s3_parallel`.

- [ ] **Step 4: Run tests, verify they pass**

```bash
<PREFIX> pytest tests/test_batch.py -v
```

Expected: new `TestPerformBatchOcrSubmitAndAwait` tests PASS; existing `TestPdfExtractBatch` tests PASS.

- [ ] **Step 5: Run full suite**

```bash
<PREFIX> pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: Lint and type check**

```bash
<PREFIX> ruff check pypdftotext/batch.py
<PREFIX> ruff format --check pypdftotext/
<PREFIX> pyright pypdftotext/
```

Expected: no errors.

- [ ] **Step 7: Final doctest sweep**

```bash
<PREFIX> pytest --doctest-modules pypdftotext/
```

Expected: all PASS, including the new OCRResult doctests.

- [ ] **Step 8: Commit**

```bash
git add pypdftotext/batch.py tests/test_batch.py
git commit -m "Rewrite PdfExtractBatch._perform_batch_ocr for submit-and-await

Replaces the per-PDF ThreadPoolExecutor with: submit all → await_all collective
wait → apply results serially. Drops _ocr_single_pdf and its imports. The S3
fetch ThreadPoolExecutor in _pull_s3_parallel is unaffected. PDFs without an
available client get an inline failure OCRResult applied; partial failures
don't crash the batch."
```

---

## Self-Review

After all 14 tasks are complete:

- [ ] **Run the full pre-merge check sequence:**

```bash
<PREFIX> pytest tests/ -v
<PREFIX> pytest --doctest-modules pypdftotext/
<PREFIX> ruff check pypdftotext/
<PREFIX> ruff format --check pypdftotext/
<PREFIX> pyright pypdftotext/
```

All expected: PASS / no errors.

- [ ] **Diff inspection:**

```bash
git log --oneline main..HEAD     # or the appropriate base branch
git diff main..HEAD --stat       # see file-level summary
```

Confirm:
- New files: `pypdftotext/ocr_result.py`, `pypdftotext/_cancellable_polling.py`, `tests/test_azure_docintel_integrator.py`, `tests/test_cancellable_polling.py`, `tests/test_await_all.py`.
- Modified files match the File Structure section above.
- No unintended drift in unrelated modules.

- [ ] **Verify the original bug is fixed:**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestAwaitOne::test_await_one_timeout_yields_error_result -v
```

This is the regression test. PASS confirms the silent-`None` timeout no longer crashes downstream.

- [ ] **Verify P4 contamination is fixed:**

```bash
<PREFIX> pytest tests/test_azure_docintel_integrator.py::TestThreadLocalIsolation::test_thread_local_isolation_across_threads -v
```

PASS confirms two threads sharing one integrator each see their own result.

- [ ] **Verify the batch path no longer uses ThreadPoolExecutor for OCR:**

```bash
<PREFIX> pytest tests/test_batch.py::TestPerformBatchOcrSubmitAndAwait::test_perform_batch_ocr_uses_azure_read_no_threadpool -v
```

PASS confirms the architecture change.

If any check fails, fix the underlying issue and re-run. Do not skip checks to make the suite pass.

---

## Notes for the implementer

- **Order matters.** Tasks 8 → 9 → 10 deal with the `last_result` migration. Task 8 removes the dataclass field, Task 9 reintroduces it as a deprecated property, Task 10 deprecates the methods that read it. Don't reorder.
- **`samples/` fixtures.** Tests reference `samples/all70th.pdf` and `samples/all70th.bin` (a pickled `AnalyzeResult`). If those don't exist locally, the relevant tests will `skipTest()`. That's expected for environments without the fixtures; CI should have them.
- **DeprecationWarning noise during tests.** Existing tests that call deprecated methods will emit warnings to stderr but won't fail. If you want to suppress them globally for clean CI output, add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "ignore::DeprecationWarning:pypdftotext.azure_docintel_integrator",
]
```

This is optional and out of scope for the spec — only add if the test output is noisy enough to bother you.

- **Commit hygiene.** Each task should produce exactly one commit. The commit messages in this plan are starting points — feel free to refine them, but keep each commit focused on its single task.

- **If a step's expected output doesn't match reality:** stop and investigate. Don't paper over a failing test with a `skip` decorator or change the assertion to match buggy behavior. The plan's expected outputs are the contract; deviation means either the plan is wrong (file an issue / amend the plan) or your implementation has a bug.
