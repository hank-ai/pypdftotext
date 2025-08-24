"""Tests for PdfExtract class using sample PDFs."""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pypdftotext import PdfExtract, constants


class TestPdfExtract(unittest.TestCase):
    """Test cases for PdfExtract class."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.samples_dir = Path("samples")
        cls.deid_epic_pdf = cls.samples_dir / "deid_epic.pdf"
        cls.deid_epic_txt = cls.samples_dir / "deid_epic.txt"

        # Ensure sample files exist
        if not cls.deid_epic_pdf.exists():
            raise FileNotFoundError(f"Sample PDF not found: {cls.deid_epic_pdf}")
        if not cls.deid_epic_txt.exists():
            raise FileNotFoundError(f"Reference text not found: {cls.deid_epic_txt}")

    def test_properties(self):
        """Test all major properties of PdfExtract."""
        pdf = PdfExtract(self.deid_epic_pdf)

        # text_pages property
        pages = pdf.text_pages
        self.assertIsInstance(pages, list)
        self.assertGreater(len(pages), 0)
        for page in pages:
            self.assertIsInstance(page, str)

        # extracted_pages property
        extracted_pages = pdf.extracted_pages
        self.assertIsInstance(extracted_pages, list)
        for page in extracted_pages:
            self.assertTrue(hasattr(page, "text"))
            self.assertTrue(hasattr(page, "source"))
            self.assertTrue(hasattr(page, "handwritten_ratio"))
            self.assertIn(page.source, ["embedded", "OCR"])

        # reader property
        from pypdf import PdfReader

        self.assertIsInstance(pdf.reader, PdfReader)

        # writer property
        from pypdf import PdfWriter

        self.assertIsInstance(pdf.writer, PdfWriter)

        # text property
        full_text = pdf.text
        self.assertIsInstance(full_text, str)
        self.assertGreater(len(full_text), 0)

    def test_child_pdf_creation(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_page_count = len(pdf.extracted_pages)

        # Test with list of indices
        child1 = pdf.child_pdf(page_indices=[0, original_page_count - 1])
        self.assertEqual(len(child1.extracted_pages), 2)
        self.assertEqual(child1.extracted_pages[0].text, pdf.extracted_pages[0].text)

        # Test with range tuple
        child2 = pdf.child_pdf(page_indices=(0, 2))
        self.assertEqual(len(child2.extracted_pages), 3)

        # Test with config overrides
        child3 = pdf.child_pdf(page_indices=[0], config_overrides={"MIN_LINES_OCR_TRIGGER": 15})
        self.assertEqual(child3.config.MIN_LINES_OCR_TRIGGER, 15)
        self.assertNotEqual(pdf.config.MIN_LINES_OCR_TRIGGER, 15)

    def test_handwritten_ratio_without_ocr(self):
        """Test handwritten_ratio returns 0 for non-OCR'd pages."""
        pdf = PdfExtract(self.deid_epic_pdf)
        for i in range(len(pdf.extracted_pages)):
            ratio = pdf.handwritten_ratio(i)
            self.assertEqual(ratio, 0.0)

    def test_replace_byte_codes(self):
        """Test custom glyph replacement parameter."""
        replacements = {b"\x00\x01": b"\xe2\x98\x90"}
        pdf = PdfExtract(self.deid_epic_pdf, replace_byte_codes=replacements)
        # Just verify it doesn't crash
        self.assertIsInstance(pdf.text, str)

    @patch("pypdftotext.pdf_extract.boto3")
    def test_s3_initialization(self, mock_boto3):
        """Test S3 URI handling with mocked boto3."""
        mock_s3_client = MagicMock()
        mock_boto3.client.return_value = mock_s3_client
        mock_s3_client.get_object.return_value = {
            "Body": MagicMock(read=lambda: self.deid_epic_pdf.read_bytes())
        }

        pdf = PdfExtract("s3://test-bucket/test-file.pdf")

        mock_boto3.client.assert_called_once_with(
            service_name="s3",
            aws_access_key_id=constants.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=constants.AWS_SECRET_ACCESS_KEY,
            aws_session_token=constants.AWS_SESSION_TOKEN,
        )
        mock_s3_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="test-file.pdf"
        )
        self.assertIsInstance(pdf.body, bytes)


if __name__ == "__main__":
    unittest.main()
