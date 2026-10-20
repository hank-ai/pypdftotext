"""Tests for the OCRResult transport dataclass and the AnalyzeResult helper functions."""

import dataclasses
import unittest

from azure.ai.documentintelligence.models import AnalyzeResult

from pypdftotext import PyPdfToTextConfig
from pypdftotext.ocr_result import (
    OCRResult,
    handwritten_ratio,
    page_at_index,
    rotation_degrees,
)


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

    def test_succeeded_true_when_pages_and_no_error(self):
        result = OCRResult(pdf_name="x.pdf", pages=["page1 text"])
        self.assertTrue(result.succeeded)
        self.assertIsNone(result.error)

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
        d = self._fake_analyze_result_dict(num_pages=1)
        d["pages"][0]["angle"] = 90.0
        self.assertEqual(rotation_degrees(AnalyzeResult(d), 0, PyPdfToTextConfig()), 90.0)

    def test_rotation_degrees_zero_below_threshold(self):
        cfg = PyPdfToTextConfig(overrides={"MIN_OCR_ROTATION_DEGREES": 5.0})
        d = self._fake_analyze_result_dict(num_pages=1)
        d["pages"][0]["angle"] = 2.0
        self.assertEqual(rotation_degrees(AnalyzeResult(d), 0, cfg), 0.0)

    def test_handwritten_ratio_zero_when_no_styles(self):
        raw = AnalyzeResult(self._fake_analyze_result_dict(num_pages=1))
        self.assertEqual(handwritten_ratio(raw, 0, PyPdfToTextConfig()), 0.0)

    def test_handwritten_ratio_counts_confident_handwritten_spans(self):
        d = self._fake_analyze_result_dict(num_pages=1)  # page 0 span offset 0, length 10
        d["styles"] = [{"isHandwritten": True, "confidence": 0.95,
                        "spans": [{"offset": 0, "length": 5}]}]
        self.assertAlmostEqual(
            handwritten_ratio(AnalyzeResult(d), 0, PyPdfToTextConfig()), 0.5
        )

    def test_handwritten_ratio_ignores_low_confidence_styles(self):
        d = self._fake_analyze_result_dict(num_pages=1)
        d["styles"] = [{"isHandwritten": True, "confidence": 0.1,
                        "spans": [{"offset": 0, "length": 5}]}]
        self.assertEqual(handwritten_ratio(AnalyzeResult(d), 0, PyPdfToTextConfig()), 0.0)

    def test_ocr_result_publicly_importable(self):
        """OCRResult is importable from the top-level package."""
        import pypdftotext
        self.assertTrue(hasattr(pypdftotext, "OCRResult"))
        from pypdftotext.ocr_result import OCRResult as _Direct
        self.assertIs(pypdftotext.OCRResult, _Direct)
        self.assertIn("OCRResult", pypdftotext.__all__)


if __name__ == "__main__":
    unittest.main()
