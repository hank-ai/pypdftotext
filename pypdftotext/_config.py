"""Manage settings for text extraction and OCR operations"""

from __future__ import annotations

import os
from dataclasses import dataclass, InitVar, field, fields
from typing import Any, TypedDict


class PyPdfToTextConfigOverrides(TypedDict, total=False):
    """
    Enumerate settings available to override via the PyPdfToTextConfig
    `overrides` InitVar.
    """

    AZURE_DOCINTEL_ENDPOINT: str
    AZURE_DOCINTEL_SUBSCRIPTION_KEY: str
    AZURE_DOCINTEL_AUTO_CLIENT: bool
    AZURE_DOCINTEL_TIMEOUT: int
    DISABLE_OCR: bool
    DISABLE_PROGRESS_BAR: bool
    PROGRESS_BAR_POSITION: int | None
    FONT_HEIGHT_WEIGHT: float
    OCR_LINE_HEIGHT_SCALE: int
    OCR_POSITIONING_SCALE: int
    PRESERVE_VERTICAL_WHITESPACE: bool
    MAX_CHARS_PER_PDF_PAGE: int
    MIN_LINES_OCR_TRIGGER: int
    TRIGGER_OCR_PAGE_RATIO: float
    SCALE_WEIGHT: float
    MIN_OCR_ROTATION_DEGREES: float
    SUPPRESS_EMBEDDED_TEXT: bool
    OCR_HANDWRITTEN_CONFIDENCE_LIMIT: float
    AWS_ACCESS_KEY_ID: str | None
    AWS_SECRET_ACCESS_KEY: str | None
    AWS_SESSION_TOKEN: str | None


@dataclass(kw_only=True)
class PyPdfToTextConfig:
    """Package wide constants for pypdftotext"""

    # pylint: disable=invalid-name
    base: InitVar[PyPdfToTextConfig | None] = None
    overrides: InitVar[PyPdfToTextConfigOverrides | None] = None
    """A base instance to use as a starting point for initialization. Optional."""
    AZURE_DOCINTEL_ENDPOINT: str = field(
        default_factory=lambda: os.getenv("AZURE_DOCINTEL_ENDPOINT", "")
    )
    """The API endpoint of your Azure Document Intelligence instance. Defaults to
    the value of the Env Var of the same name or an empty string."""
    AZURE_DOCINTEL_SUBSCRIPTION_KEY: str = field(
        default_factory=lambda: os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY", "")
    )
    """The API key for your Azure Document Intelligence instance. Defaults to
    the value of the Env Var of the same name or an empty string."""
    AZURE_DOCINTEL_AUTO_CLIENT: bool = True
    """If True (default), the Azure Read OCR client is created automatically
    upon first use."""
    AZURE_DOCINTEL_TIMEOUT: int = 60
    """How long to wait for Azure OCR results before timing out. Default is 60."""
    DISABLE_OCR: bool = False
    """Set to True to disable all OCR operations and return 'code behind' text
    only."""
    DISABLE_PROGRESS_BAR: bool = False
    """Set to True to disable the per page text extraction progress bar (e.g.
    when logging to CloudWatch)."""
    PROGRESS_BAR_POSITION: int | None = None
    """Control position if nesting progress bars from multiple threads."""
    FONT_HEIGHT_WEIGHT: float = 1.0
    """Factor for adjusting line splitting behaviors
    and preserved vertical whitespace in fixed width embedded text output.
    NOTE: Higher values result in fewer blank lines but increase the
    likelihood of triggering a split due to font height based y offsets."""
    OCR_LINE_HEIGHT_SCALE: int = 50
    """Factor between 0 and 100 for adjusting line splitting behaviors
    and preserved vertical whitespace in fixed width OCR text output.
    NOTE: Higher values result in fewer blank lines but increase the
    likelihood of triggering a split due to font height based y offsets."""
    OCR_POSITIONING_SCALE: int = 100
    """The factor by which to upscale the coordinates reported in the
    Azure OCR response when constructing the fixed width layout. Lower
    values result in less spacing and increase the likelihood of combining
    independently reported text fragments onto a single line. Tread with
    caution when messing with this one. Also impacts OCR_LINE_HEIGHT_SCALE
    behavior."""
    PRESERVE_VERTICAL_WHITESPACE: bool = False
    """If False (default), no blank lines will be present in the extracted
    text. If True, blank lines are inserted whenever the nominal font height
    is less than or equal to the y coord displacement."""
    MAX_CHARS_PER_PDF_PAGE: int = 25000
    """The maximum number of characters that can conceivably appear on a single
    PDF page. An 8.5inx11in page packed with nothing 6pt text would contain
    ~17K chars. Some malformed PDFs result in millions of extracted nonsense
    characters which can lead to memory overruns (not to mention bad text).
    If a page contains more characters than this, something is wrong. Clear
    the value and report an empty string."""
    MIN_LINES_OCR_TRIGGER: int = 1
    """A page is marked for OCR if it contains fewer lines in its extracted
    code behind text. OCR only proceeds if a sufficient fraction of the
    total PDF pages have been marked (see `TRIGGER_OCR_PAGE_RATIO`)."""
    TRIGGER_OCR_PAGE_RATIO: float = 0.99
    """OCR will proceed if and only if the fraction of pages with fewer than
    `MIN_LINES_OCR_TRIGGER` lines is greater than this value. Default is 0.99,
    i.e. OCR only occurs if ALL pages hit the minimum lines trigger."""
    SCALE_WEIGHT: float = 1.25
    """Adds priority to contiguously rendered strings when calculating the
    fixed char width."""
    MIN_OCR_ROTATION_DEGREES: float = 1e-5
    """Rotations greater than this value reported by Azure OCR will be applied
    prior to compiling fixed width output."""
    SUPPRESS_EMBEDDED_TEXT: bool = False
    """if true, embedded text extraction will not be attempted. Assuming OCR
    is available, all pages will be OCR'd by default."""
    OCR_HANDWRITTEN_CONFIDENCE_LIMIT: float = 0.8
    """Azure must be at least this confident that a given span is handwritten
    in order for it to count when determining handwritten character percentage."""
    AWS_ACCESS_KEY_ID: str | None = field(default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID"))
    """AWS access key ID for the credentials that will be used to pull source
    PDFs from S3 if installed with "s3" extra (`pip install pypdftotext["s3"]`)"""
    AWS_SECRET_ACCESS_KEY: str | None = field(
        default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY")
    )
    """AWS secret access key for the credentials that will be used to pull source
    PDFs from S3 if installed with "s3" extra (`pip install pypdftotext["s3"]`)"""
    AWS_SESSION_TOKEN: str | None = field(default_factory=lambda: os.getenv("AWS_SESSION_TOKEN"))
    """AWS session token for the credentials that will be used to pull source
    PDFs from S3 if installed with "s3" extra (`pip install pypdftotext["s3"]`)"""

    def __post_init__(
        self,
        base: PyPdfToTextConfig | None = None,
        overrides: PyPdfToTextConfigOverrides | dict[str, Any] | None = None,
    ):
        """
        If base is supplied, merge fields by overwriting default values in the
        new instance with custom values from the base.
        """
        if base:
            for fld in fields(PyPdfToTextConfig):
                if callable(fld.default_factory):
                    _default = fld.default_factory()
                else:
                    _default = fld.default
                if _default == getattr(self, fld.name) != (_base := getattr(base, fld.name)):
                    setattr(self, fld.name, _base)
        for field_name, val in (overrides or {}).items():
            if hasattr(self, field_name):
                setattr(self, field_name, val)


constants = PyPdfToTextConfig()
