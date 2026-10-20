"""Test module for batch processing functionality."""

import logging
import json
import pickle
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from azure.ai.documentintelligence.models import AnalyzeResult

from pypdftotext import PyPdfToTextConfig, AZURE_READ
from pypdftotext.batch import PdfExtractBatch
from pypdftotext.pdf_extract import PdfExtract
from pypdftotext.azure_docintel_integrator import AzureDocIntelIntegrator
from pypdftotext.ocr_result import OCRResult


def _streamed(fake):
    """Adapt a dict-returning fake into the iter_results shape (an iterator of
    (pdf_name, OCRResult) tuples) so the batch consumes it like the real thing."""

    def _fake_iter_results(pollers, integrator, *, config=None, submitted_at=None,
                           batch_timeout=None):
        return iter(fake(pollers, integrator, config=config).items())

    return _fake_iter_results


class TestPdfExtractBatch(unittest.TestCase):
    """Test cases for PdfExtractBatch class."""

    @classmethod
    def setUpClass(cls):
        """Load sample test data."""
        cls.samples_dir = Path("samples")

        # Load real Azure OCR result for realistic mocking
        cls.all70th_ocr_result = None
        if (cls.samples_dir / "all70th.bin").exists():
            with open(cls.samples_dir / "all70th.bin", "rb") as f:
                cls.all70th_ocr_result = pickle.load(f)

        # Load expected text output
        cls.all70th_expected_text = None
        if (cls.samples_dir / "all70th.json").exists():
            with open(cls.samples_dir / "all70th.json", "r") as f:
                cls.all70th_expected_text = json.load(f)

        # Load sample PDFs
        cls.all70th_pdf_bytes = None
        if (cls.samples_dir / "all70th.pdf").exists():
            cls.all70th_pdf_bytes = (cls.samples_dir / "all70th.pdf").read_bytes()

        cls.deid_epic_pdf_bytes = None
        if (cls.samples_dir / "deid_epic.pdf").exists():
            cls.deid_epic_pdf_bytes = (cls.samples_dir / "deid_epic.pdf").read_bytes()

    def setUp(self):
        """Set up test fixtures."""
        self.config = PyPdfToTextConfig(
            overrides={
                "DISABLE_OCR": False,
                "MIN_LINES_OCR_TRIGGER": 1,
                "TRIGGER_OCR_PAGE_RATIO": 0.5,
                "DISABLE_PROGRESS_BAR": True,
                "MAX_CHARS_PER_PDF_PAGE": 25000,
            }
        )

    def test_init_with_list(self):
        """Test PdfExtractBatch initialization with list input."""
        if not self.all70th_pdf_bytes:
            self.skipTest("Sample PDF not available")

        pdfs = [self.all70th_pdf_bytes] * 3
        batch = PdfExtractBatch(pdfs, config=self.config)

        self.assertIsInstance(batch.pdfs, dict)
        self.assertEqual(len(batch.pdfs), 3)
        self.assertEqual(batch.config, self.config)
        self.assertIn("PDF[0]", batch.pdfs)
        self.assertIn("PDF[1]", batch.pdfs)
        self.assertIn("PDF[2]", batch.pdfs)

    def test_init_with_dict(self):
        """Test PdfExtractBatch initialization with dict input."""
        if not self.deid_epic_pdf_bytes:
            self.skipTest("Sample PDF not available")

        pdfs = {"doc1": self.deid_epic_pdf_bytes, "doc2": self.deid_epic_pdf_bytes}
        batch = PdfExtractBatch(pdfs, config=self.config)

        self.assertIsInstance(batch.pdfs, dict)
        self.assertEqual(len(batch.pdfs), 2)
        self.assertIn("doc1", batch.pdfs)
        self.assertIn("doc2", batch.pdfs)

    def test_pdf_extracts_created_in_batch_mode(self):
        """Test that PdfExtract instances are created with batch mode flag."""
        if not self.all70th_pdf_bytes:
            self.skipTest("Sample PDF not available")

        pdfs = [self.all70th_pdf_bytes] * 2
        batch = PdfExtractBatch(pdfs, config=self.config)

        # All pdf_extracts should be created in __init__
        self.assertEqual(len(batch.pdf_extracts), 2)
        for pdf_extract in batch.pdf_extracts.values():
            self.assertIsInstance(pdf_extract, PdfExtract)
            self.assertTrue(pdf_extract._batch_mode)

    def test_extract_all_without_ocr(self):
        """Test extraction when OCR is not needed."""
        if not self.deid_epic_pdf_bytes:
            self.skipTest("Sample PDF not available")

        # Use config that prevents OCR
        no_ocr_config = PyPdfToTextConfig(
            overrides={
                "DISABLE_OCR": True,
                "DISABLE_PROGRESS_BAR": True,
            }
        )

        pdfs = {"test": self.deid_epic_pdf_bytes}
        batch = PdfExtractBatch(pdfs, config=no_ocr_config)

        result = batch.extract_all()

        # Should get extracted text without OCR
        self.assertIn("test", result)
        self.assertGreater(len(result["test"].extracted_pages), 0)
        # All pages should be from embedded text
        for page in result["test"].extracted_pages:
            self.assertEqual(page.source, "embedded")

    def test_extract_all_with_mock_azure(self):
        """Test extraction with mocked Azure OCR using the submit/await_all path."""
        if not self.all70th_pdf_bytes or not self.all70th_expected_text:
            self.skipTest("Sample data not available")

        # Use config that forces OCR (suppress embedded text, low ratio threshold)
        ocr_config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 1000,  # High threshold to force OCR
                "TRIGGER_OCR_PAGE_RATIO": 0.01,  # Low ratio to trigger easily
                "SUPPRESS_EMBEDDED_TEXT": True,
                "DISABLE_PROGRESS_BAR": True,
                "AZURE_DOCINTEL_ENDPOINT": "https://test.azure.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test_key",
            }
        )

        pdfs = {"all70th": self.all70th_pdf_bytes}
        batch = PdfExtractBatch(pdfs, config=ocr_config)
        fake_poller = MagicMock(name="poller")
        expected_pages = ["\n".join(page) for page in self.all70th_expected_text]

        def fake_results(pollers, integrator, *, config=None):
            extract = batch.pdf_extracts["all70th"]
            n_ocr = len(extract.ocr_page_idxs)
            raw = AnalyzeResult({
                "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
                "stringIndexType": "textElements",
                "content": " ".join(f"p{i}" for i in extract.ocr_page_idxs),
                "pages": [
                    {"pageNumber": i + 1, "angle": 0.0, "width": 8.5,
                     "height": 11.0, "unit": "inch",
                     "spans": [{"offset": pos * 3, "length": 2}],
                     "words": [], "lines": [], "selectionMarks": []}
                    for pos, i in enumerate(extract.ocr_page_idxs)
                ],
                "styles": [],
            })
            return {
                "all70th": OCRResult(
                    pdf_name="all70th",
                    raw=raw,
                    pages=expected_pages[:n_ocr],
                    error=None,
                )
            }

        with patch.object(AZURE_READ, "submit", return_value=fake_poller) as mock_submit, \
             patch("pypdftotext.batch.iter_results", side_effect=_streamed(fake_results)) as mock_iter_results:
            result = batch.extract_all()

        # Should have results
        self.assertIn("all70th", result)
        pdf_extract = result["all70th"]

        # Should have extracted pages
        self.assertGreater(len(pdf_extract.extracted_pages), 0)

        # OCR was submitted and awaited
        mock_submit.assert_called()
        mock_iter_results.assert_called()

    def test_parallel_ocr_with_multiple_pdfs(self):
        """Test that batch processes multiple PDFs via the submit/await_all path."""
        if not self.all70th_pdf_bytes or not self.all70th_ocr_result:
            self.skipTest("Sample data not available")

        # Use config that will trigger OCR
        ocr_config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 1000,  # Force OCR
                "TRIGGER_OCR_PAGE_RATIO": 0.01,
                "SUPPRESS_EMBEDDED_TEXT": True,
                "DISABLE_PROGRESS_BAR": True,
                "AZURE_DOCINTEL_ENDPOINT": "https://test.azure.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test_key",
            }
        )

        # Create batch with multiple PDFs
        pdfs = {
            "pdf1": self.all70th_pdf_bytes,
            "pdf2": self.all70th_pdf_bytes,
        }

        batch = PdfExtractBatch(pdfs, config=ocr_config)
        fake_poller = MagicMock(name="poller")

        def fake_results(pollers, integrator, *, config=None):
            results = {}
            for name in pollers:
                extract = batch.pdf_extracts[name]
                raw = AnalyzeResult({
                    "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
                    "stringIndexType": "textElements",
                    "content": " ".join(f"p{i}" for i in extract.ocr_page_idxs),
                    "pages": [
                        {"pageNumber": i + 1, "angle": 0.0, "width": 8.5,
                         "height": 11.0, "unit": "inch",
                         "spans": [{"offset": pos * 3, "length": 2}],
                         "words": [], "lines": [], "selectionMarks": []}
                        for pos, i in enumerate(extract.ocr_page_idxs)
                    ],
                    "styles": [],
                })
                results[name] = OCRResult(
                    pdf_name=name,
                    raw=raw,
                    pages=["OCR text"] * len(extract.ocr_page_idxs),
                    error=None,
                )
            return results

        with patch.object(AZURE_READ, "submit", return_value=fake_poller), \
             patch("pypdftotext.batch.iter_results", side_effect=_streamed(fake_results)):
            result = batch.extract_all()

        # Should have results for both PDFs
        self.assertEqual(len(result), 2)
        self.assertIn("pdf1", result)
        self.assertIn("pdf2", result)

    def test_ocr_error_handling(self):
        """Test that OCR errors are handled gracefully via the await_all path."""
        if not self.deid_epic_pdf_bytes:
            self.skipTest("Sample PDF not available")

        # Use config that triggers OCR
        ocr_config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 1000,  # Force OCR
                "TRIGGER_OCR_PAGE_RATIO": 0.01,
                "SUPPRESS_EMBEDDED_TEXT": True,
                "DISABLE_PROGRESS_BAR": True,
                "AZURE_DOCINTEL_ENDPOINT": "https://test.azure.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test_key",
            }
        )

        pdfs = {"failing_pdf": self.deid_epic_pdf_bytes}
        batch = PdfExtractBatch(pdfs, config=ocr_config)
        fake_poller = MagicMock(name="poller")

        def fake_results(pollers, integrator, *, config=None):
            # Simulate Azure API error returned as an OCRResult with error set
            return {
                name: OCRResult(
                    pdf_name=name,
                    raw=None, pages=[],
                    error="OCR failed: Azure API Error",
                )
                for name in pollers
            }

        with patch.object(AZURE_READ, "submit", return_value=fake_poller), \
             patch("pypdftotext.batch.iter_results", side_effect=_streamed(fake_results)):
            # Should not raise exception
            result = batch.extract_all()

        # Should still return the PdfExtract
        self.assertIn("failing_pdf", result)
        self.assertIsInstance(result["failing_pdf"], PdfExtract)
        # Should have extracted pages (with ocr_error set on OCR pages)
        pdf_extract = result["failing_pdf"]
        self.assertGreater(len(pdf_extract.extracted_pages), 0)
        # All OCR-eligible pages should carry the error
        for idx in pdf_extract.ocr_page_idxs:
            self.assertEqual(
                pdf_extract.extracted_pages[idx].ocr_error, "OCR failed: Azure API Error"
            )

    def test_real_pdf_processing_end_to_end(self):
        """Test complete workflow with real PDFs and no OCR."""
        if not self.all70th_pdf_bytes or not self.deid_epic_pdf_bytes:
            self.skipTest("Sample PDFs not available")

        # Use config that won't trigger OCR
        config = PyPdfToTextConfig(
            overrides={
                "DISABLE_OCR": True,  # No OCR for this test
                "DISABLE_PROGRESS_BAR": True,
            }
        )

        pdfs = {
            "all70th": self.all70th_pdf_bytes,
            "deid_epic": self.deid_epic_pdf_bytes,
        }

        batch = PdfExtractBatch(pdfs, config=config)
        result = batch.extract_all()

        # Verify we got results for both PDFs
        self.assertEqual(len(result), 2)
        self.assertIn("all70th", result)
        self.assertIn("deid_epic", result)

        # Verify text was extracted from both
        for name, pdf_extract in result.items():
            self.assertGreater(len(pdf_extract.extracted_pages), 0)
            full_text = pdf_extract.text
            self.assertIsInstance(full_text, str)
            self.assertGreater(len(full_text), 0)

    def test_config_inheritance(self):
        """Test that config is properly passed to all components."""
        if not self.deid_epic_pdf_bytes:
            self.skipTest("Sample PDF not available")

        custom_config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 5,
                "TRIGGER_OCR_PAGE_RATIO": 0.8,
                "MAX_CHARS_PER_PDF_PAGE": 50000,
                "DISABLE_PROGRESS_BAR": True,
                "DISABLE_OCR": True,  # Disable OCR for this test
            }
        )

        pdfs = [self.deid_epic_pdf_bytes]
        batch = PdfExtractBatch(pdfs, config=custom_config)

        # Config should be passed to batch
        self.assertEqual(batch.config.MIN_LINES_OCR_TRIGGER, 5)
        self.assertEqual(batch.config.TRIGGER_OCR_PAGE_RATIO, 0.8)

        # Config should be passed to PdfExtract instances
        for pdf_extract in batch.pdf_extracts.values():
            self.assertEqual(pdf_extract.config.MIN_LINES_OCR_TRIGGER, 5)
            self.assertEqual(pdf_extract.config.TRIGGER_OCR_PAGE_RATIO, 0.8)
            self.assertEqual(pdf_extract.config.MAX_CHARS_PER_PDF_PAGE, 50000)


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

    def _make_raw(self, extract):
        """Build a populated AnalyzeResult for the pages this extract needs."""
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

    def test_perform_batch_ocr_uses_azure_read_no_threadpool(self):
        """Batch dispatches via AZURE_READ.submit + module-level await_all;
        does NOT instantiate a ThreadPoolExecutor for OCR."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"a": self.pdf_bytes, "b": self.pdf_bytes}, config=self.cfg)
        fake_poller = MagicMock(name="poller")

        def fake_results(pollers, integrator, *, config=None):
            return {
                name: OCRResult(
                    pdf_name=name,
                    raw=self._make_raw(batch.pdf_extracts[name]),
                    pages=["OCR_TEXT"] * len(batch.pdf_extracts[name].ocr_page_idxs),
                    error=None,
                )
                for name in pollers
            }

        with patch.object(AZURE_READ, "submit", return_value=fake_poller) as mock_submit, \
             patch("pypdftotext.batch.iter_results", side_effect=_streamed(fake_results)) as mock_iter_results, \
             patch("pypdftotext.batch.ThreadPoolExecutor") as mock_pool:
            batch.extract_all()
        # Submit was called once per PDF.
        self.assertEqual(mock_submit.call_count, 2)
        # iter_results was called once with both pollers, their submit times, and no batch budget.
        self.assertEqual(mock_iter_results.call_count, 1)
        args, kwargs = mock_iter_results.call_args
        pollers_arg = args[0] if args else kwargs["pollers"]
        self.assertEqual(set(pollers_arg.keys()), {"a", "b"})
        self.assertIsNone(kwargs["batch_timeout"])
        self.assertEqual(set(kwargs["submitted_at"].keys()), {"a", "b"})
        # ThreadPoolExecutor must NOT be constructed inside _perform_batch_ocr.
        self.assertEqual(mock_pool.call_count, 0)

    def test_partial_failure_does_not_crash_batch(self):
        """One PDF fails OCR (error result); the other succeeds; batch returns both."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"good": self.pdf_bytes, "bad": self.pdf_bytes}, config=self.cfg)
        fake_poller = MagicMock(name="poller")

        def fake_results(pollers, integrator, *, config=None):
            good_extract = batch.pdf_extracts["good"]
            good_pages = ["OCR_GOOD"] * len(good_extract.ocr_page_idxs)
            return {
                "good": OCRResult(
                    pdf_name="good",
                    raw=self._make_raw(good_extract),
                    pages=good_pages, error=None,
                ),
                "bad": OCRResult(
                    pdf_name="bad", raw=None,
                    pages=[], error="OCR timeout: simulated",
                ),
            }

        with patch.object(AZURE_READ, "submit", return_value=fake_poller), \
             patch("pypdftotext.batch.iter_results", side_effect=_streamed(fake_results)):
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


    def test_results_are_applied_as_they_stream(self):
        """Each result is applied before the next one is pulled from the stream."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"a": self.pdf_bytes, "b": self.pdf_bytes}, config=self.cfg)
        applied_before_second_yield = {}

        def fake_iter_results(pollers, integrator, *, config=None, submitted_at=None,
                              batch_timeout=None):
            ext_a = batch.pdf_extracts["a"]
            yield "a", OCRResult(pdf_name="a", raw=self._make_raw(ext_a),
                                 pages=["OCR_A"] * len(ext_a.ocr_page_idxs))
            # By the time the consumer asks for the next item, "a" must be applied.
            applied_before_second_yield["a"] = ext_a.extracted_pages[ext_a.ocr_page_idxs[0]].text
            ext_b = batch.pdf_extracts["b"]
            yield "b", OCRResult(pdf_name="b", raw=self._make_raw(ext_b),
                                 pages=["OCR_B"] * len(ext_b.ocr_page_idxs))

        with patch.object(AZURE_READ, "submit", return_value=MagicMock(name="poller")), \
             patch("pypdftotext.batch.iter_results", side_effect=fake_iter_results):
            batch.extract_all()
        self.assertEqual(applied_before_second_yield["a"], "OCR_A")

    def test_apply_exception_for_one_pdf_does_not_stop_stream(self):
        """An exception applying one PDF's result does not stop the remaining PDFs."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"boom": self.pdf_bytes, "ok": self.pdf_bytes}, config=self.cfg)

        def fake_iter_results(pollers, integrator, *, config=None, submitted_at=None,
                              batch_timeout=None):
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
        """A PDF whose submit() returns None gets the no-client error inline and is
        excluded from the pollers handed to iter_results."""
        from pypdftotext import AZURE_READ
        from pypdftotext.batch import PdfExtractBatch
        from pypdftotext.ocr_result import OCRResult
        from unittest.mock import patch

        batch = PdfExtractBatch({"a": self.pdf_bytes, "b": self.pdf_bytes}, config=self.cfg)
        submit_results = iter([None, MagicMock(name="poller")])  # "a": no client; "b": ok

        def fake_iter_results(pollers, integrator, *, config=None, submitted_at=None,
                              batch_timeout=None):
            self.assertEqual(set(pollers), {"b"})
            self.assertEqual(set(submitted_at), {"b"})
            ext = batch.pdf_extracts["b"]
            yield "b", OCRResult(pdf_name="b", raw=self._make_raw(ext),
                                 pages=["OCR_B"] * len(ext.ocr_page_idxs))

        with patch.object(AZURE_READ, "submit", side_effect=lambda *a, **k: next(submit_results)), \
             patch("pypdftotext.batch.iter_results", side_effect=fake_iter_results):
            results = batch.extract_all()
        a = results["a"]
        self.assertTrue(
            a.extracted_pages[a.ocr_page_idxs[0]].ocr_error.startswith(
                "OCR failed: no client available"
            )
        )
        b = results["b"]
        self.assertEqual(b.extracted_pages[b.ocr_page_idxs[0]].text, "OCR_B")


class TestInvalidPdfs(unittest.TestCase):
    """PDFs that raise pypdf errors during embedded extraction are quarantined."""

    GARBAGE = b"%PDF-1.4\nthis is not a pdf"
    NO_XREF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"

    def setUp(self):
        self.samples_dir = Path("samples")
        if not (self.samples_dir / "deid_epic.pdf").exists():
            self.skipTest("Sample PDF not available")
        # deid_epic.pdf has embedded text, so it extracts fully with OCR disabled.
        self.good_bytes = (self.samples_dir / "deid_epic.pdf").read_bytes()
        self.cfg = PyPdfToTextConfig(
            overrides={"DISABLE_OCR": True, "DISABLE_PROGRESS_BAR": True}
        )

    def test_invalid_pdfs_attr_initialized_empty(self):
        batch = PdfExtractBatch({"good": self.good_bytes}, config=self.cfg)
        self.assertEqual(batch.invalid_pdfs, {})

    def test_pypdf_errors_are_captured_and_removed(self):
        from pypdf.errors import PdfReadError, PdfStreamError, PyPdfError

        batch = PdfExtractBatch(
            {"good": self.good_bytes, "garbage": self.GARBAGE, "no_xref": self.NO_XREF},
            config=self.cfg,
        )
        with self.assertLogs("pypdftotext.batch", level="WARNING") as cm:
            results = batch.extract_all()

        self.assertEqual(set(results), {"good"})
        self.assertIs(results, batch.pdf_extracts)
        self.assertEqual(set(batch.invalid_pdfs), {"garbage", "no_xref"})
        self.assertIsInstance(batch.invalid_pdfs["garbage"], PdfStreamError)
        self.assertIsInstance(batch.invalid_pdfs["no_xref"], PdfReadError)
        for exc in batch.invalid_pdfs.values():
            self.assertIsInstance(exc, PyPdfError)
        self.assertTrue(results["good"].text.strip())

        warnings = [r for r in cm.records if r.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("garbage" in r.getMessage() for r in warnings))
        self.assertTrue(any("no_xref" in r.getMessage() for r in warnings))

    def test_all_invalid_returns_empty(self):
        batch = PdfExtractBatch({"garbage": self.GARBAGE}, config=self.cfg)
        results = batch.extract_all()
        self.assertEqual(results, {})
        self.assertEqual(set(batch.invalid_pdfs), {"garbage"})

    def test_non_pypdf_errors_still_propagate(self):
        from unittest.mock import PropertyMock

        batch = PdfExtractBatch({"good": self.good_bytes}, config=self.cfg)
        with patch.object(
            PdfExtract, "extracted_pages", new_callable=PropertyMock, side_effect=RuntimeError("x")
        ):
            with self.assertRaises(RuntimeError):
                batch.extract_all()
        self.assertEqual(batch.invalid_pdfs, {})


if __name__ == "__main__":
    unittest.main()
