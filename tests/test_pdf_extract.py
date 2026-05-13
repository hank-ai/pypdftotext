"""Tests for PdfExtract class using sample PDFs."""

import json
import pickle
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pypdftotext import PdfExtract, constants
from pypdftotext._config import PyPdfToTextConfig
from pypdftotext.pdf_extract import AllPagesRemovedError


class TestPdfExtract(unittest.TestCase):
    """Test cases for PdfExtract class."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.samples_dir = Path("samples")
        cls.deid_epic_pdf = cls.samples_dir / "deid_epic.pdf"
        cls.deid_epic_txt = cls.samples_dir / "deid_epic.txt"

        # all70th series files for OCR testing
        cls.all70th_pdf = cls.samples_dir / "all70th.pdf"
        cls.all70th_bin = cls.samples_dir / "all70th.bin"
        cls.all70th_json = cls.samples_dir / "all70th.json"
        cls.all70th_corrected = cls.samples_dir / "all70th_corrected_rotation.pdf"
        cls.all70th_compressed = cls.samples_dir / "all70th_compressed.pdf"

        # Minimal PDF containing a JBIG2-encoded image — used to exercise the
        # DependencyError path in compress_images() since JBIG2 decoding requires
        # the jbig2dec external binary which is typically not installed.
        cls.jbig2_pdf = cls.samples_dir / "jbig2_image.pdf"

        # Ensure sample files exist
        if not cls.deid_epic_pdf.exists():
            raise FileNotFoundError(f"Sample PDF not found: {cls.deid_epic_pdf}")
        if not cls.deid_epic_txt.exists():
            raise FileNotFoundError(f"Reference text not found: {cls.deid_epic_txt}")

        # Load OCR test fixtures if available
        if cls.all70th_bin.exists():
            with open(cls.all70th_bin, "rb") as f:
                cls.all70th_ocr_result = pickle.load(f)
        if cls.all70th_json.exists():
            with open(cls.all70th_json, "r") as f:
                cls.all70th_expected_text = json.load(f)

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

    def test_child_creation(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_page_count = len(pdf.extracted_pages)

        # Test with list of indices
        child1 = pdf.child(page_indices=[0, original_page_count - 1])
        self.assertEqual(len(child1.extracted_pages), 2)
        self.assertEqual(child1.extracted_pages[0].text, pdf.extracted_pages[0].text)

        # Test with range tuple
        child2 = pdf.child(page_indices=(0, 2))
        self.assertEqual(len(child2.extracted_pages), 3)

        # Test with config overrides
        child3 = pdf.child(
            page_indices=[0],
            config_overrides={"MIN_LINES_OCR_TRIGGER": 15},
            remove_from_parent=True,
        )
        self.assertEqual(child3.config.MIN_LINES_OCR_TRIGGER, 15)
        self.assertNotEqual(pdf.config.MIN_LINES_OCR_TRIGGER, 15)
        self.assertEqual(len(pdf.extracted_pages), 4, "Removal unsuccessful.")

    def test_functional_child_creation_w_removal(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(self.deid_epic_pdf)

        # Test with list of indices
        child = pdf.child(
            page_indices=lambda pg: pg.text.strip().startswith(
                "Intraprocedure Flowsheet Data (continued)"
            ),
            remove_from_parent=True,
        )
        self.assertEqual(len(child.extracted_pages), 3)
        self.assertEqual(len(pdf.extracted_pages), 2)

    def test_landscape_property(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(
            self.all70th_pdf,
            PyPdfToTextConfig(overrides={"DISABLE_OCR": True}),
        )

        # Test with list of indices
        self.assertFalse(pdf.extracted_pages[0].landscape)
        self.assertTrue(pdf.extracted_pages[1].landscape)
        self.assertFalse(pdf.extracted_pages[2].landscape)
        self.assertTrue(pdf.extracted_pages[3].landscape)

    def test_functional_page_removal(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(self.deid_epic_pdf)

        # Test with functional remove
        pdf.remove_pages(
            remove=lambda pg: pg.text.strip().startswith(
                "Intraprocedure Flowsheet Data (continued)"
            )
        )
        self.assertEqual(len(pdf.extracted_pages), 2)

    def test_tuple_page_removal(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(self.deid_epic_pdf)

        # Test with tuple remove
        pdf.remove_pages(remove=(0, 2))
        self.assertEqual(len(pdf.extracted_pages), 2)

    def test_named_destinations(self):
        """Test child PDF creation with various page selections."""
        pdf = PdfExtract(self.deid_epic_pdf)
        og_bytes = pdf.body
        # Test with tuple remove
        pdf.add_named_destinations([("test", 1), ("test", 2)])
        self.assertNotEqual(og_bytes, pdf.body)

    def test_handwritten_ratio_without_ocr(self):
        """Test handwritten_ratio returns 0 for non-OCR'd pages."""
        pdf = PdfExtract(self.deid_epic_pdf)
        for i in range(len(pdf.extracted_pages)):
            ratio = pdf.handwritten_ratio(i)
            self.assertEqual(ratio, 0.0)

    def test_batch_mode(self):
        """Test PdfExtract in batch mode skips individual OCR."""
        config = PyPdfToTextConfig(
            overrides={
                "DISABLE_PROGRESS_BAR": True,
                "MIN_LINES_OCR_TRIGGER": 1,
                "TRIGGER_OCR_PAGE_RATIO": 0.5,
            }
        )

        # Create PdfExtract in batch mode
        pdf = PdfExtract(self.deid_epic_pdf, config=config, _batch_mode=True)

        # Access extracted_pages to trigger extraction
        pages = pdf.extracted_pages

        # Should have extracted embedded text but not performed OCR
        self.assertGreater(len(pages), 0)
        self.assertTrue(hasattr(pdf, "ocr_page_idxs"))

        # Verify batch mode flag is set
        self.assertTrue(pdf._batch_mode)

    @patch("pypdftotext.pdf_extract.AzureDocIntelIntegrator")
    def test_ocr_method(self, mock_azure_class):
        """Test the public ocr() method."""
        config = PyPdfToTextConfig(
            overrides={
                "DISABLE_PROGRESS_BAR": True,
                "MIN_LINES_OCR_TRIGGER": 1,
                "TRIGGER_OCR_PAGE_RATIO": 0.5,
                "MAX_CHARS_PER_PDF_PAGE": 25000,
            }
        )

        # Create PdfExtract in batch mode to prevent automatic OCR
        pdf = PdfExtract(self.deid_epic_pdf, config=config, _batch_mode=True)

        # Trigger extraction without OCR
        _ = pdf.extracted_pages

        # Mock Azure integrator
        mock_azure = MagicMock()
        mock_azure.ocr_pages.return_value = ["OCR Page 1"] * len(pdf.ocr_page_idxs)
        mock_azure.rotation_degrees.return_value = 0.0
        mock_azure.handwritten_ratio.return_value = 0.0
        mock_azure.page_at_index.return_value = None

        # Manually call OCR
        if pdf.ocr_page_idxs:  # Only test if there are pages marked for OCR
            pdf.ocr(mock_azure)

            # Verify OCR was called if ratio threshold was met
            if len(pdf.ocr_page_idxs) / len(pdf.extracted_pages) >= config.TRIGGER_OCR_PAGE_RATIO:
                mock_azure.ocr_pages.assert_called_once()

    def test_ocr_page_idxs_populated(self):
        """Test that ocr_page_idxs is populated correctly."""
        config = PyPdfToTextConfig(
            overrides={
                "DISABLE_PROGRESS_BAR": True,
                "MIN_LINES_OCR_TRIGGER": 1000,  # High threshold to mark pages for OCR
                "DISABLE_OCR": False,
            }
        )

        # Use a PDF that we know has text
        pdf = PdfExtract(self.deid_epic_pdf, config=config, _batch_mode=True)
        _ = pdf.extracted_pages

        # ocr_page_idxs should be populated based on MIN_LINES_OCR_TRIGGER
        self.assertIsInstance(pdf.ocr_page_idxs, list)
        # With high MIN_LINES_OCR_TRIGGER, some pages should be marked for OCR
        # (exact count depends on the PDF content)
        assert bool(pdf.ocr_page_idxs), "No pages marked for OCR with 1000 line threshold."

    def test_replace_byte_codes(self):
        """Test custom glyph replacement parameter."""
        replacements = {b"\x00\x01": b"\xe2\x98\x90"}
        pdf = PdfExtract(
            self.deid_epic_pdf, PyPdfToTextConfig(overrides={"REPLACE_BYTE_CODES": replacements})
        )
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

    def test_config_inheritance(self):
        """Test that changes to constants propagate to new PdfExtract instances."""
        # Store original values to restore later
        original_disable_ocr = constants.DISABLE_OCR
        original_max_chars = constants.MAX_CHARS_PER_PDF_PAGE
        original_min_lines = constants.MIN_LINES_OCR_TRIGGER

        try:
            # Change constants values
            constants.DISABLE_OCR = True
            constants.MAX_CHARS_PER_PDF_PAGE = 50000
            constants.MIN_LINES_OCR_TRIGGER = 10

            # Create new PdfExtract instance
            pdf = PdfExtract(self.deid_epic_pdf)

            # Verify config inherited the changed constants
            self.assertTrue(pdf.config.DISABLE_OCR)
            self.assertEqual(pdf.config.MAX_CHARS_PER_PDF_PAGE, 50000)
            self.assertEqual(pdf.config.MIN_LINES_OCR_TRIGGER, 10)

            # Change constants again
            constants.DISABLE_OCR = False
            constants.MAX_CHARS_PER_PDF_PAGE = 75000

            # Create another PdfExtract instance
            pdf2 = PdfExtract(self.deid_epic_pdf)

            # Verify new instance got the updated constants
            self.assertFalse(pdf2.config.DISABLE_OCR)
            self.assertEqual(pdf2.config.MAX_CHARS_PER_PDF_PAGE, 75000)
            self.assertEqual(pdf2.config.MIN_LINES_OCR_TRIGGER, 10)

            # Verify first instance wasn't affected
            self.assertTrue(pdf.config.DISABLE_OCR)
            self.assertEqual(pdf.config.MAX_CHARS_PER_PDF_PAGE, 50000)

        finally:
            # Restore original constants
            constants.DISABLE_OCR = original_disable_ocr
            constants.MAX_CHARS_PER_PDF_PAGE = original_max_chars
            constants.MIN_LINES_OCR_TRIGGER = original_min_lines

    def test_ocr_page_creation(self):
        """Test OCR page creation with mocked Azure client only."""
        # Create config that will trigger OCR for all pages
        config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 100,  # High threshold to force OCR
                "TRIGGER_OCR_PAGE_RATIO": 0.5,  # Trigger OCR if >50% pages need it
                "DISABLE_PROGRESS_BAR": True,
                "AZURE_DOCINTEL_AUTO_CLIENT": False,  # Prevent auto client creation
            }
        )

        # Create a real AzureDocIntelIntegrator instance
        from pypdftotext.azure_docintel_integrator import AzureDocIntelIntegrator

        # Mock only the DocumentIntelligenceClient
        with patch(
            "pypdftotext.azure_docintel_integrator.DocumentIntelligenceClient"
        ) as MockClient:
            # Create mock client instance
            mock_client = MagicMock()

            # Create mock poller that will return our pre-saved OCR results
            mock_poller = MagicMock()
            mock_poller.result.return_value = self.all70th_ocr_result

            # Configure mock client to return the poller when begin_analyze_document is called
            mock_client.begin_analyze_document.return_value = mock_poller

            # Create the Azure integrator and set its client to our mock
            azure_integrator = AzureDocIntelIntegrator(config=config)
            azure_integrator.client = mock_client

            # Patch the PdfExtract to use our configured integrator
            with patch("pypdftotext.pdf_extract.AzureDocIntelIntegrator") as MockAzureClass:
                MockAzureClass.return_value = azure_integrator

                # Create PdfExtract which will use our mocked client
                pdf = PdfExtract(self.all70th_pdf, config=config)

                # Access text_page_lines to trigger extraction
                extracted_lines = pdf.text_page_lines

                # Verify we got 4 pages
                self.assertEqual(len(extracted_lines), 4)

                # Verify the mock client was called correctly
                mock_client.begin_analyze_document.assert_called_once()
                call_args = mock_client.begin_analyze_document.call_args
                self.assertEqual(call_args.kwargs["model_id"], "prebuilt-read")
                self.assertEqual(
                    call_args.kwargs["pages"], "1,2,3,4"
                )  # 1-based indices for Azure API

                # Verify the poller result was called with timeout
                mock_poller.result.assert_called_once_with(config.AZURE_DOCINTEL_TIMEOUT)

                # Verify the extracted text matches expected (first 3 pages from JSON)
                for i in range(min(3, len(extracted_lines))):
                    expected_lines = self.all70th_expected_text[i]
                    actual_lines = extracted_lines[i]

                    # Check that we have roughly the same number of lines
                    self.assertAlmostEqual(len(actual_lines), len(expected_lines), delta=2)

                    # Verify key phrases are present
                    if i == 0:  # First page
                        self.assertTrue(
                            any("Just 3 and one half score" in line for line in actual_lines)
                        )
                        self.assertTrue(
                            any(
                                "7 decades" in line or "I decades" in line for line in actual_lines
                            )
                        )
                    elif i == 1:  # Second page
                        self.assertTrue(
                            any("So Much of Me is You" in line for line in actual_lines)
                        )
                        self.assertTrue(
                            any("How did" in line and "Sam" in line for line in actual_lines)
                        )

                # Verify all pages are marked as OCR source
                for page in pdf.extracted_pages:
                    self.assertEqual(page.source, "OCR")
                    # Azure page should be set for OCR'd pages
                    self.assertIsNotNone(page.azure_page)

                # Test that the integrator's methods work correctly with the results
                # This provides coverage for handwritten_ratio and rotation_degrees
                for i in range(4):
                    # Test handwritten_ratio method
                    ratio = azure_integrator.handwritten_ratio(i)
                    self.assertIsInstance(ratio, float)
                    self.assertGreaterEqual(ratio, 0.0)
                    self.assertLessEqual(ratio, 1.0)

                    # Test rotation_degrees method
                    rotation = azure_integrator.rotation_degrees(i)
                    self.assertIsInstance(rotation, float)
                    expected_angle = self.all70th_ocr_result.pages[i].angle or 0.0
                    if abs(expected_angle) > config.MIN_OCR_ROTATION_DEGREES:
                        self.assertEqual(rotation, expected_angle)
                    else:
                        self.assertEqual(rotation, 0.0)

                    # Test page_at_index method
                    page = azure_integrator.page_at_index(i)
                    self.assertIsNotNone(page)
                    self.assertEqual(page.page_number, i + 1)

    def test_rotation_correction(self):
        """Test that rotation correction modifies the PDF body correctly."""
        # Create config that will trigger OCR
        config = PyPdfToTextConfig(
            overrides={
                "MIN_LINES_OCR_TRIGGER": 100,
                "TRIGGER_OCR_PAGE_RATIO": 0.5,
                "DISABLE_PROGRESS_BAR": True,
                "MIN_OCR_ROTATION_DEGREES": 1.0,  # Apply rotations > 1 degree
                "AZURE_DOCINTEL_AUTO_CLIENT": False,  # Prevent auto client creation
            }
        )

        # Create a real AzureDocIntelIntegrator instance
        from pypdftotext.azure_docintel_integrator import AzureDocIntelIntegrator

        # Mock only the DocumentIntelligenceClient
        with patch(
            "pypdftotext.azure_docintel_integrator.DocumentIntelligenceClient"
        ) as MockClient:
            # Create mock client instance
            mock_client = MagicMock()

            # Create mock poller that will return our pre-saved OCR results
            mock_poller = MagicMock()
            mock_poller.result.return_value = self.all70th_ocr_result

            # Configure mock client to return the poller when begin_analyze_document is called
            mock_client.begin_analyze_document.return_value = mock_poller

            # Create the Azure integrator and set its client to our mock
            azure_integrator = AzureDocIntelIntegrator(config=config)
            azure_integrator.client = mock_client

            # Patch the PdfExtract to use our configured integrator
            with patch("pypdftotext.pdf_extract.AzureDocIntelIntegrator") as MockAzureClass:
                MockAzureClass.return_value = azure_integrator

                # Create PdfExtract
                pdf = PdfExtract(self.all70th_pdf, config=config)

                # Trigger extraction which should detect and correct rotations
                _ = pdf.text_pages

                # Verify rotations were detected and corrected
                # Page 0: ~0.2° (no correction), Page 1: ~90° (rotate -90°)
                # Page 2: ~-180° (rotate 180°), Page 3: ~-90° (rotate 90°)

                # The body should have been regenerated with corrected rotations
                original_pdf_size = len(self.all70th_pdf.read_bytes())
                corrected_pdf_size = len(pdf.body)

                # The sizes should be similar but not identical due to rotation corrections
                self.assertNotEqual(original_pdf_size, corrected_pdf_size)

                # If we have the reference corrected file, compare
                if self.all70th_corrected.exists():
                    reference_size = len(self.all70th_corrected.read_bytes())
                    # The corrected PDF should be roughly the same size as our reference
                    # Allow some variation due to PDF generation differences
                    size_ratio = corrected_pdf_size / reference_size
                    self.assertAlmostEqual(size_ratio, 1.0, delta=0.1)

                # Test that the rotation_degrees method was used correctly
                rotations_found = []
                for i in range(4):
                    rotation = azure_integrator.rotation_degrees(i)
                    rotations_found.append(rotation)
                    expected_angle = self.all70th_ocr_result.pages[i].angle or 0.0
                    if abs(expected_angle) > config.MIN_OCR_ROTATION_DEGREES:
                        self.assertEqual(rotation, expected_angle)
                        # Verify significant rotations were detected
                        self.assertGreater(abs(rotation), 1.0)

                # Verify that we found the expected rotations
                # Page 1 should have ~90°, Page 2 ~-180°, Page 3 ~-90°
                self.assertAlmostEqual(abs(rotations_found[1]), 90, delta=1)
                self.assertAlmostEqual(abs(rotations_found[2]), 180, delta=1)
                self.assertAlmostEqual(abs(rotations_found[3]), 90, delta=1)

                # Verify the extracted pages have correct rotation info and source
                for page in pdf.extracted_pages:
                    self.assertEqual(page.source, "OCR")
                    self.assertIsNotNone(page.azure_page)

    def test_compress_images(self):
        """Test image compression reduces PDF size appropriately."""
        # Load the original PDF
        pdf = PdfExtract(self.all70th_pdf)

        # Get original size
        original_size = len(pdf.body)

        # Compress images
        pdf.compress_images(
            white_point=220,  # Standard denoising threshold
            max_overscale=2,  # Standard overscale factor
            force=False,
        )

        # Get compressed size
        compressed_size = len(pdf.body)

        # The compressed PDF should be smaller
        self.assertLess(compressed_size, original_size)

        # Verify compression flag is set
        self.assertTrue(pdf.compressed)

        # If we have the reference compressed file, compare sizes
        if self.all70th_compressed.exists():
            reference_size = len(self.all70th_compressed.read_bytes())

            # The compressed size should be similar to our reference
            # Allow some variation (±10%) due to compression algorithm differences
            size_ratio = compressed_size / reference_size
            self.assertAlmostEqual(size_ratio, 1.0, delta=0.1)

            # Verify significant size reduction occurred
            # The compressed file should be substantially smaller than the original
            reduction_ratio = compressed_size / original_size
            self.assertLess(reduction_ratio, 0.8)  # Expect at least 20% reduction

        # Test that calling compress_images again without force does nothing
        pdf.compress_images(force=False)
        size_after_second_call = len(pdf.body)
        self.assertEqual(compressed_size, size_after_second_call)

        # Test that force=True allows recompression
        pdf.compress_images(force=True, white_point=200)  # Different white_point
        recompressed_size = len(pdf.body)
        # Size might be slightly smaller due to different white_point
        # But should still be compressed
        self.assertLessEqual(recompressed_size, compressed_size)

    def test_compress_images_uses_config_image_white_point(self):
        """compress_images() falls back to config.IMAGE_WHITE_POINT when white_point=None."""
        from PIL import Image

        captured_luts = []
        original_point = Image.Image.point

        def recording_point(self_img, lut, *args, **kwargs):
            captured_luts.append(lut)
            return original_point(self_img, lut, *args, **kwargs)

        pdf = PdfExtract(self.all70th_pdf, config={"IMAGE_WHITE_POINT": 180})

        with patch.object(Image.Image, "point", recording_point):
            pdf.compress_images()

        self.assertGreater(len(captured_luts), 0)
        for lut in captured_luts:
            # Below the configured threshold, value passes through unchanged.
            self.assertEqual(lut(179), 179)
            # At/above the configured threshold (which is below the 220 default),
            # the lambda returns 256 — confirming the config value drove the threshold.
            self.assertEqual(lut(180), 256)
            self.assertEqual(lut(219), 256)

    def test_compress_images_explicit_white_point_overrides_config(self):
        """An explicit white_point kwarg supersedes config.IMAGE_WHITE_POINT."""
        from PIL import Image

        captured_luts = []
        original_point = Image.Image.point

        def recording_point(self_img, lut, *args, **kwargs):
            captured_luts.append(lut)
            return original_point(self_img, lut, *args, **kwargs)

        pdf = PdfExtract(self.all70th_pdf, config={"IMAGE_WHITE_POINT": 100})

        with patch.object(Image.Image, "point", recording_point):
            pdf.compress_images(white_point=210)

        self.assertGreater(len(captured_luts), 0)
        for lut in captured_luts:
            self.assertEqual(lut(209), 209)
            self.assertEqual(lut(210), 256)

    def test_compress_images_handles_jbig2_dependency_error(self):
        """compress_images() logs and swallows DependencyError raised by JBIG2 images."""
        if not self.jbig2_pdf.exists():
            self.skipTest(f"JBIG2 sample PDF not found: {self.jbig2_pdf}")

        pdf = PdfExtract(self.jbig2_pdf)

        with self.assertLogs("pypdftotext.pdf_extract", level="ERROR") as logs:
            pdf.compress_images()

        # The error path is taken without raising, and the compressed flag still flips.
        self.assertTrue(pdf.compressed)
        self.assertTrue(
            any("jbig2dec" in msg.lower() for msg in logs.output),
            f"expected a jbig2dec DependencyError log, got: {logs.output}",
        )

    def test_compress_images_skips_pages_with_smask(self):
        """compress_images() skips whole pages whose XObjects carry an /SMask.

        deid_epic.pdf has 51 soft-masked images on each of its 5 pages. Without
        the skip, rewriting those image streams while leaving /SMask untouched
        produces redacted-looking output (see PdfExtract._page_has_smask).
        """
        from PIL import Image

        point_calls = []
        original_point = Image.Image.point

        def recording_point(self_img, lut, *args, **kwargs):
            point_calls.append(lut)
            return original_point(self_img, lut, *args, **kwargs)

        pdf = PdfExtract(self.deid_epic_pdf)
        page_count = len(pdf.extracted_pages)
        self.assertGreater(page_count, 0)

        with patch.object(Image.Image, "point", recording_point):
            with self.assertLogs("pypdftotext.pdf_extract", level="WARNING") as logs:
                pdf.compress_images()

        # Every page is skipped before reaching the per-image processing loop,
        # so PIL's per-pixel transform must never run.
        self.assertEqual(len(point_calls), 0)

        # One WARNING per skipped page; each names /SMask and the page index.
        smask_warnings = [m for m in logs.output if "/SMask" in m]
        self.assertEqual(len(smask_warnings), page_count)
        for i in range(page_count):
            self.assertTrue(
                any(f"pg_idx={i}" in m for m in smask_warnings),
                f"missing SMask skip warning for pg_idx={i}: {smask_warnings}",
            )

        # Flag still flips so subsequent calls short-circuit (matches JBIG2 contract).
        self.assertTrue(pdf.compressed)

    def test_page_has_smask_detects_xobject_smask(self):
        """_page_has_smask returns True for pages with /SMask images, False otherwise."""
        smask_pdf = PdfExtract(self.deid_epic_pdf)
        self.assertTrue(PdfExtract._page_has_smask(smask_pdf.reader.pages[0]))

        no_smask_pdf = PdfExtract(self.all70th_pdf)
        self.assertFalse(PdfExtract._page_has_smask(no_smask_pdf.reader.pages[0]))

    def test_compress_images_skips_image_masks(self):
        """compress_images() skips /ImageMask stencils to preserve value overlays.

        The sample is a 1-page form with 1 RGB JPEG underlay and 15 /ImageMask
        1-bit CCITTFax stencils (typed/handwritten field values painted with the
        content stream's current fill color). Without the skip, the JPEG
        recompresses fine but the stencils get rewritten as grayscale rasters,
        which strips /ImageMask True and the value overlays disappear.
        """
        from PIL import Image

        sample = self.samples_dir / "visual_elements_lost_during_image_compression.pdf"
        if not sample.exists():
            self.skipTest(f"Sample PDF not found: {sample}")

        point_calls = []
        original_point = Image.Image.point

        def recording_point(self_img, lut, *args, **kwargs):
            point_calls.append(self_img.mode)
            return original_point(self_img, lut, *args, **kwargs)

        pdf = PdfExtract(sample)

        with patch.object(Image.Image, "point", recording_point):
            with self.assertLogs("pypdftotext.pdf_extract", level="DEBUG") as logs:
                pdf.compress_images()

        # Exactly 15 DEBUG skip lines (one per /ImageMask XObject) and the JPEG
        # underlay is the only image that actually reaches the .point() transform.
        mask_skips = [m for m in logs.output if m.startswith("DEBUG") and "/ImageMask" in m]
        self.assertEqual(len(mask_skips), 15, f"unexpected skip count: {mask_skips}")
        self.assertEqual(point_calls, ["L"])

        # Flag flips as usual; subsequent calls short-circuit per the existing contract.
        self.assertTrue(pdf.compressed)

    def test_is_image_mask_helper(self):
        """_is_image_mask detects /ImageMask True XObjects via the underlying XObject dict."""
        sample = self.samples_dir / "visual_elements_lost_during_image_compression.pdf"
        if not sample.exists():
            self.skipTest(f"Sample PDF not found: {sample}")

        pdf = PdfExtract(sample)
        masks = 0
        non_masks = 0
        for img in pdf.reader.pages[0].images:
            if PdfExtract._is_image_mask(img):
                masks += 1
            else:
                non_masks += 1
        self.assertEqual(masks, 15)
        self.assertEqual(non_masks, 1)

        # Negative case: all70th.pdf has regular raster images, no /ImageMask stencils.
        plain_pdf = PdfExtract(self.all70th_pdf)
        for img in plain_pdf.reader.pages[0].images:
            self.assertFalse(PdfExtract._is_image_mask(img))

    def test_remove_pages_raises_by_default_when_all_removed(self):
        """remove_pages() raises AllPagesRemovedError when all pages are removed."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_pages = list(pdf.extracted_pages)
        original_body = pdf.body

        with self.assertRaises(AllPagesRemovedError):
            pdf.remove_pages(remove=lambda _: True)

        # Pages and body must be unchanged after the exception
        self.assertEqual(pdf.extracted_pages, original_pages)
        self.assertEqual(pdf.body, original_body)

    def test_remove_pages_noop_with_raise_on_empty_false(self):
        """remove_pages(raise_on_empty=False) is a no-op when all pages would be removed."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_pages = list(pdf.extracted_pages)
        original_body = pdf.body

        pdf.remove_pages(remove=lambda _: True, raise_on_empty=False)

        self.assertEqual(pdf.extracted_pages, original_pages)
        self.assertEqual(pdf.body, original_body)

    def test_remove_pages_list_raises_when_all_removed(self):
        """remove_pages() raises AllPagesRemovedError when all pages removed via list."""
        pdf = PdfExtract(self.deid_epic_pdf)
        all_indices = list(range(len(pdf.extracted_pages)))

        with self.assertRaises(AllPagesRemovedError):
            pdf.remove_pages(remove=all_indices)

    def test_remove_pages_tuple_raises_when_all_removed(self):
        """remove_pages() raises AllPagesRemovedError when all pages removed via tuple."""
        pdf = PdfExtract(self.deid_epic_pdf)
        last = len(pdf.extracted_pages) - 1

        with self.assertRaises(AllPagesRemovedError):
            pdf.remove_pages(remove=(0, last))

    def test_child_remove_from_parent_raises_when_all_removed(self):
        """child(remove_from_parent=True) raises AllPagesRemovedError when parent is emptied."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_page_count = len(pdf.extracted_pages)
        all_indices = list(range(original_page_count))

        with self.assertRaises(AllPagesRemovedError):
            pdf.child(page_indices=all_indices, remove_from_parent=True)

        # Parent pages must be unchanged
        self.assertEqual(len(pdf.extracted_pages), original_page_count)

    def test_child_remove_from_parent_noop_with_raise_on_empty_false(self):
        """child(remove_from_parent=True, raise_on_empty=False) leaves parent intact."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_page_count = len(pdf.extracted_pages)
        all_indices = list(range(original_page_count))

        child = pdf.child(page_indices=all_indices, remove_from_parent=True, raise_on_empty=False)

        # Child was created successfully
        self.assertEqual(len(child.extracted_pages), original_page_count)
        # Parent is unchanged (noop)
        self.assertEqual(len(pdf.extracted_pages), original_page_count)

    def test_all_pages_removed_error_importable_from_package(self):
        """AllPagesRemovedError is exported from the pypdftotext package."""
        import pypdftotext

        self.assertTrue(hasattr(pypdftotext, "AllPagesRemovedError"))
        self.assertTrue(issubclass(pypdftotext.AllPagesRemovedError, ValueError))

    def test_partial_removal_still_works_normally(self):
        """Partial page removal is unaffected by the new raise_on_empty param."""
        pdf = PdfExtract(self.deid_epic_pdf)
        original_count = len(pdf.extracted_pages)

        pdf.remove_pages(remove=[0])  # remove just one page

        self.assertEqual(len(pdf.extracted_pages), original_count - 1)


class TestExtractedPageOcrError(unittest.TestCase):
    def test_ocr_error_defaults_to_none(self):
        """ExtractedPage.ocr_error is None by default."""
        from unittest.mock import MagicMock
        from pypdftotext.extracted_page import ExtractedPage
        # Use any page from any sample PDF; we don't need OCR to test the field.
        # Construct a minimal mock PageObject via MagicMock.
        page = ExtractedPage(page_obj=MagicMock(), handwritten_ratio=0.0, text="hello")
        self.assertIsNone(page.ocr_error)

    def test_ocr_error_can_be_set(self):
        """ExtractedPage.ocr_error accepts string assignment."""
        from unittest.mock import MagicMock
        from pypdftotext.extracted_page import ExtractedPage
        page = ExtractedPage(page_obj=MagicMock(), handwritten_ratio=0.0, text="")
        page.ocr_error = "OCR timeout: simulated"
        self.assertEqual(page.ocr_error, "OCR timeout: simulated")


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

    def test_ocr_result_publicly_importable(self):
        """OCRResult is importable from the top-level package."""
        import pypdftotext
        self.assertTrue(hasattr(pypdftotext, "OCRResult"))
        from pypdftotext.ocr_result import OCRResult as _Direct
        self.assertIs(pypdftotext.OCRResult, _Direct)
        self.assertIn("OCRResult", pypdftotext.__all__)


if __name__ == "__main__":
    unittest.main()
