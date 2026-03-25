"""Tests for the pdf_name attribute on PdfExtract."""

import io
import re
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

from pypdf import PdfReader

from pypdftotext import PdfExtract
from pypdftotext._config import PyPdfToTextConfig
from pypdftotext.batch import PdfExtractBatch


class TestPdfNameDerivation(unittest.TestCase):
    """Test _derive_pdf_name across all input types."""

    @classmethod
    def setUpClass(cls):
        cls.samples_dir = Path("samples")
        cls.deid_epic_pdf = cls.samples_dir / "deid_epic.pdf"
        if not cls.deid_epic_pdf.exists():
            raise FileNotFoundError(f"Sample PDF not found: {cls.deid_epic_pdf}")
        cls.pdf_bytes = cls.deid_epic_pdf.read_bytes()
        cls.config = PyPdfToTextConfig(overrides={"DISABLE_OCR": True, "DISABLE_PROGRESS_BAR": True})

    def test_explicit_name(self):
        """Explicit pdf_name takes priority over all derivation."""
        extract = PdfExtract(self.pdf_bytes, config=self.config, pdf_name="my_doc")
        self.assertEqual(extract.pdf_name, "my_doc")

    def test_str_file_path(self):
        """String file path derives name from filename."""
        extract = PdfExtract(str(self.deid_epic_pdf), config=self.config)
        self.assertEqual(extract.pdf_name, "deid_epic.pdf")

    def test_path_object(self):
        """Path object derives name from filename."""
        extract = PdfExtract(self.deid_epic_pdf, config=self.config)
        self.assertEqual(extract.pdf_name, "deid_epic.pdf")

    def test_bytes_input(self):
        """Bytes input falls back to hash-based filename."""
        extract = PdfExtract(self.pdf_bytes, config=self.config)
        self.assertRegex(extract.pdf_name, r"^[0-9a-f]{8}\.pdf$")

    def test_bytesio_input(self):
        """BytesIO input falls back to hash-based filename."""
        extract = PdfExtract(io.BytesIO(self.pdf_bytes), config=self.config)
        self.assertRegex(extract.pdf_name, r"^[0-9a-f]{8}\.pdf$")

    def test_bytes_hash_is_deterministic(self):
        """Same bytes always produce the same hash-based filename."""
        ext1 = PdfExtract(self.pdf_bytes, config=self.config)
        ext2 = PdfExtract(self.pdf_bytes, config=self.config)
        self.assertEqual(ext1.pdf_name, ext2.pdf_name)

    def test_pdfreader_with_title(self):
        """PdfReader with metadata title uses that title."""
        reader = PdfReader(io.BytesIO(self.pdf_bytes))
        # Only test metadata path if the sample PDF has a title
        extract = PdfExtract(reader, config=self.config)
        if reader.metadata and reader.metadata.title:
            self.assertEqual(extract.pdf_name, reader.metadata.title)
        else:
            self.assertRegex(extract.pdf_name, r"^[0-9a-f]{8}\.pdf$")

    def test_pdfreader_without_title(self):
        """PdfReader without metadata title falls back to hash-based filename."""
        reader = PdfReader(io.BytesIO(self.pdf_bytes))
        with unittest.mock.patch.object(type(reader), "metadata", new_callable=PropertyMock) as m:
            m.return_value = MagicMock(title=None)
            extract = PdfExtract(reader, config=self.config)
            self.assertRegex(extract.pdf_name, r"^[0-9a-f]{8}\.pdf$")

    def test_explicit_name_overrides_path(self):
        """Explicit pdf_name overrides path-based derivation."""
        extract = PdfExtract(str(self.deid_epic_pdf), config=self.config, pdf_name="override")
        self.assertEqual(extract.pdf_name, "override")

    def test_s3_uri_derives_filename(self):
        """S3 URI string derives name from the object key filename."""
        extract = PdfExtract(self.pdf_bytes, config=self.config)
        name = extract._derive_pdf_name("s3://bucket/path/to/report.pdf", None)
        self.assertEqual(name, "report.pdf")

    def test_s3_uri_bucket_only(self):
        """S3 URI with just a bucket and no object key derives 'bucket'."""
        extract = PdfExtract(self.pdf_bytes, config=self.config)
        name = extract._derive_pdf_name("s3://bucket", None)
        self.assertEqual(name, "bucket")

    def test_s3_uri_bucket_root_falls_back(self):
        """S3 URI with just a bucket and trailing slash falls back to hash."""
        extract = PdfExtract(self.pdf_bytes, config=self.config)
        name = extract._derive_pdf_name("s3://bucket/", None)
        # trailing slash means filename portion is empty — falls through to hash
        self.assertRegex(name, r"^[0-9a-f]{8}\.pdf$")

    def test_s3_uri_no_key_falls_back(self):
        """S3 URI with no path falls back to hash."""
        extract = PdfExtract(self.pdf_bytes, config=self.config)
        name = extract._derive_pdf_name("s3://", None)
        self.assertRegex(name, r"^[0-9a-f]{8}\.pdf$")


class TestPdfNameChildPropagation(unittest.TestCase):
    """Test pdf_name inheritance through child()."""

    @classmethod
    def setUpClass(cls):
        cls.samples_dir = Path("samples")
        cls.deid_epic_pdf = cls.samples_dir / "deid_epic.pdf"
        if not cls.deid_epic_pdf.exists():
            raise FileNotFoundError(f"Sample PDF not found: {cls.deid_epic_pdf}")
        cls.config = PyPdfToTextConfig(overrides={"DISABLE_OCR": True, "DISABLE_PROGRESS_BAR": True})

    def test_child_inherits_parent_name(self):
        """Child inherits parent's pdf_name by default."""
        parent = PdfExtract(self.deid_epic_pdf, config=self.config, pdf_name="parent_doc")
        child = parent.child([0])
        self.assertEqual(child.pdf_name, "parent_doc")

    def test_child_explicit_name_override(self):
        """Child can override parent's pdf_name."""
        parent = PdfExtract(self.deid_epic_pdf, config=self.config, pdf_name="parent_doc")
        child = parent.child([0], pdf_name="child_doc")
        self.assertEqual(child.pdf_name, "child_doc")

    def test_child_callable_inherits_name(self):
        """Child created via callable filter inherits parent's pdf_name."""
        parent = PdfExtract(self.deid_epic_pdf, config=self.config, pdf_name="parent_doc")
        child = parent.child(lambda pg: True)
        self.assertIsNotNone(child)
        self.assertEqual(child.pdf_name, "parent_doc")

    def test_child_tuple_inherits_name(self):
        """Child created via tuple range inherits parent's pdf_name."""
        parent = PdfExtract(self.deid_epic_pdf, config=self.config, pdf_name="parent_doc")
        child = parent.child((0, 0))
        self.assertEqual(child.pdf_name, "parent_doc")


class TestPdfNameBatchIntegration(unittest.TestCase):
    """Test pdf_name integration with PdfExtractBatch."""

    @classmethod
    def setUpClass(cls):
        cls.samples_dir = Path("samples")
        cls.deid_epic_pdf = cls.samples_dir / "deid_epic.pdf"
        if not cls.deid_epic_pdf.exists():
            raise FileNotFoundError(f"Sample PDF not found: {cls.deid_epic_pdf}")
        cls.pdf_bytes = cls.deid_epic_pdf.read_bytes()
        cls.config = PyPdfToTextConfig(overrides={"DISABLE_OCR": True, "DISABLE_PROGRESS_BAR": True})

    def test_dict_input_names_match_keys(self):
        """Dict-based batch input: pdf_name matches dict keys."""
        batch = PdfExtractBatch(
            {"report_a": self.pdf_bytes, "report_b": self.pdf_bytes},
            config=self.config,
        )
        self.assertEqual(batch.pdf_extracts["report_a"].pdf_name, "report_a")
        self.assertEqual(batch.pdf_extracts["report_b"].pdf_name, "report_b")

    def test_list_input_names_match_auto_keys(self):
        """List-based batch input: pdf_name matches auto-generated keys."""
        batch = PdfExtractBatch(
            [self.pdf_bytes, self.pdf_bytes],
            config=self.config,
        )
        self.assertEqual(batch.pdf_extracts["PDF[0]"].pdf_name, "PDF[0]")
        self.assertEqual(batch.pdf_extracts["PDF[1]"].pdf_name, "PDF[1]")

    def test_path_input_batch_uses_dict_key(self):
        """Dict key overrides path-based derivation in batch."""
        batch = PdfExtractBatch(
            {"custom_name": self.deid_epic_pdf},
            config=self.config,
        )
        self.assertEqual(batch.pdf_extracts["custom_name"].pdf_name, "custom_name")
