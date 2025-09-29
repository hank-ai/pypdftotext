"""Test module for batch processing functionality."""

import json
import pickle
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pypdftotext import PyPdfToTextConfig
from pypdftotext.batch import PdfExtractBatch
from pypdftotext.pdf_extract import PdfExtract
from pypdftotext.azure_docintel_integrator import AzureDocIntelIntegrator


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

    @patch.object(AzureDocIntelIntegrator, 'ocr_pages')
    @patch.object(AzureDocIntelIntegrator, 'create_client')
    def test_extract_all_with_mock_azure(self, mock_create_client, mock_ocr_pages):
        """Test extraction with mocked Azure OCR using real sample data."""
        if not self.all70th_pdf_bytes or not self.all70th_expected_text:
            self.skipTest("Sample data not available")

        # Mock Azure to return real expected text
        mock_create_client.return_value = True
        mock_ocr_pages.return_value = ["\n".join(page) for page in self.all70th_expected_text]

        # Use config that triggers OCR
        ocr_config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 1000,  # High threshold to force OCR
                "TRIGGER_OCR_PAGE_RATIO": 0.01,  # Low ratio to trigger easily
                "DISABLE_PROGRESS_BAR": True,
                "AZURE_DOCINTEL_ENDPOINT": "https://test.azure.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test_key",
            }
        )

        pdfs = {"all70th": self.all70th_pdf_bytes}
        batch = PdfExtractBatch(pdfs, config=ocr_config)

        result = batch.extract_all()

        # Should have results
        self.assertIn("all70th", result)
        pdf_extract = result["all70th"]

        # Should have extracted pages
        self.assertGreater(len(pdf_extract.extracted_pages), 0)

        # If OCR was triggered, verify it was called
        if any(page.source == "OCR" for page in pdf_extract.extracted_pages):
            mock_create_client.assert_called()
            mock_ocr_pages.assert_called()

    def test_parallel_ocr_with_multiple_pdfs(self):
        """Test that parallel OCR works with multiple PDFs."""
        if not self.all70th_pdf_bytes or not self.all70th_ocr_result:
            self.skipTest("Sample data not available")

        # Mock the Azure OCR to return quickly
        with patch.object(AzureDocIntelIntegrator, 'ocr_pages') as mock_ocr:
            with patch.object(AzureDocIntelIntegrator, 'create_client') as mock_create:
                mock_create.return_value = True
                mock_ocr.return_value = ["Page 1 text", "Page 2 text"]

                # Create batch with multiple PDFs
                pdfs = {
                    "pdf1": self.all70th_pdf_bytes,
                    "pdf2": self.all70th_pdf_bytes,
                }

                # Use config that will trigger OCR
                ocr_config = PyPdfToTextConfig(
                    overrides={
                        "MIN_LINES_OCR_TRIGGER": 1000,  # Force OCR
                        "TRIGGER_OCR_PAGE_RATIO": 0.01,
                        "DISABLE_PROGRESS_BAR": True,
                        "AZURE_DOCINTEL_ENDPOINT": "https://test.azure.com",
                        "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test_key",
                    }
                )

                batch = PdfExtractBatch(pdfs, config=ocr_config, max_workers=2)
                result = batch.extract_all()

                # Should have results for both PDFs
                self.assertEqual(len(result), 2)
                self.assertIn("pdf1", result)
                self.assertIn("pdf2", result)

    @patch.object(AzureDocIntelIntegrator, 'ocr_pages')
    @patch.object(AzureDocIntelIntegrator, 'create_client')
    def test_ocr_error_handling(self, mock_create_client, mock_ocr_pages):
        """Test that OCR errors are handled gracefully."""
        if not self.deid_epic_pdf_bytes:
            self.skipTest("Sample PDF not available")

        # Mock Azure to raise an error
        mock_create_client.return_value = True
        mock_ocr_pages.side_effect = Exception("Azure API Error")

        # Use config that triggers OCR
        ocr_config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 1000,  # Force OCR
                "TRIGGER_OCR_PAGE_RATIO": 0.01,
                "DISABLE_PROGRESS_BAR": True,
                "AZURE_DOCINTEL_ENDPOINT": "https://test.azure.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test_key",
            }
        )

        pdfs = {"failing_pdf": self.deid_epic_pdf_bytes}
        batch = PdfExtractBatch(pdfs, config=ocr_config)

        # Should not raise exception
        result = batch.extract_all()

        # Should still return the PdfExtract with embedded text
        self.assertIn("failing_pdf", result)
        self.assertIsInstance(result["failing_pdf"], PdfExtract)
        # Should have extracted embedded text even though OCR failed
        self.assertGreater(len(result["failing_pdf"].extracted_pages), 0)

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


if __name__ == "__main__":
    unittest.main()