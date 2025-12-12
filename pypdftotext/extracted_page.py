"""Define the ExtractedPage dataclass used by PdfExtract"""

from dataclasses import dataclass
from typing import Literal

from azure.ai.documentintelligence.models import DocumentPage
from pypdf import PageObject

from .page_fingerprint import PageFingerprint


@dataclass
class ExtractedPage:
    """
    Represents a single extracted page from a PDF with its metadata.

    This dataclass encapsulates all information about a page after text extraction,
    including the source of the text (embedded or OCR), handwritten content ratio,
    and references to the underlying page objects.

    Attributes:
        page_obj: The pypdf PageObject instance for this page.
        handwritten_ratio: Ratio of handwritten to total characters (0.0 to 1.0).
            Always 0.0 for embedded text pages.
        text: The extracted text content from the page. Excludes header and footer.
        source: Indicates whether text was extracted from embedded PDF content
            ("embedded") or via OCR ("OCR").
        azure_page: The Azure DocumentPage instance if this page was OCR'd,
            None for embedded text pages.
        document_idx: An integer representing the ancestry of the page. Pages
            with a common document_idx likely originated from the same source.
            Used for header/footer detection. Default is 0. Calling the
            PdfExtract().assign_document_indices() will dynamically set this value.
        header: Text detected as a header on this page.
        footer: Text detected as a footer on this page.
        fingerprint: A PageFingerprint used for discovering page groupings that
            share a common ancestor PDF.
    """

    page_obj: PageObject
    handwritten_ratio: float
    text: str
    source: Literal["embedded", "OCR"] = "embedded"
    azure_page: DocumentPage | None = None
    document_idx: int = 0
    header: str = ""
    footer: str = ""

    def __post_init__(self):
        self.fingerprint = PageFingerprint.from_page(self.page_obj)
