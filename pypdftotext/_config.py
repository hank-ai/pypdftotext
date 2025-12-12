"""Manage settings for text extraction and OCR operations"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, InitVar, field
from typing import cast, Any, TypedDict


logger = logging.getLogger(__name__)


class PyPdfToTextConfigOverrides(TypedDict, total=False):
    """
    Enumerate settings available to override via the PyPdfToTextConfig
    `overrides` InitVar.
    """

    INHERIT_CONSTANTS: bool
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
    REPLACE_BYTE_CODES: dict[bytes, bytes]
    MAX_WORKERS: int
    OCR_HANDWRITTEN_CONFIDENCE_LIMIT: float
    AWS_ACCESS_KEY_ID: str | None
    AWS_SECRET_ACCESS_KEY: str | None
    AWS_SESSION_TOKEN: str | None
    MIN_HEADER_FOOTER_PAGE_MATCH_RATIO: float
    MIN_HEADER_FOOTER_LINE_MATCH_RATIO: float
    MAX_HEADER_LINES: int
    MAX_FOOTER_LINES: int
    RETAIN_CONTINUED_HEADINGS: bool


@dataclass(kw_only=True)
class _ConfigMixIn:
    """
    Package wide constants for pypdftotext.
    """

    # pylint: disable=invalid-name
    _setattrs_: set[str] = field(default_factory=set, init=False)
    _initialized_: bool = field(default=False, init=False)
    INHERIT_CONSTANTS: bool = True
    """If True (default), values set for the package-wide constants (set via
    pypdftotext.constants.<ATTRIBUTE> = <VALUE>) are inherited by all subsequent
    `PyPdfToTextConfig` instances *by default*."""
    AZURE_DOCINTEL_ENDPOINT: str = field(
        default_factory=lambda: os.getenv("AZURE_DOCINTEL_ENDPOINT", "")
    )
    """The API endpoint of your Azure Document Intelligence instance. Defaults to
    the value of the environment variable of the same name or an empty string."""
    AZURE_DOCINTEL_SUBSCRIPTION_KEY: str = field(
        default_factory=lambda: os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY", "")
    )
    """The API key for your Azure Document Intelligence instance. Defaults to
    the value of the environment variable of the same name or an empty string."""
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
    embedded text. OCR only proceeds if a sufficient fraction of the
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
    REPLACE_BYTE_CODES: dict[bytes, bytes] = field(default_factory=dict)
    """A series of byte code substitutions to make in the final extracted text,
    e.g. replacing pdf 'encoded font' checkbox representations with a standard
    unicode ☑ byte sequence."""
    MAX_WORKERS: int = 10
    """The maximum number of threads to initialize during PdfExtractBatch
    parallel operations."""
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
    MIN_HEADER_FOOTER_PAGE_MATCH_RATIO: float = 0.6
    """Minimum calculated match ratio across all pages to consider a line a header or footer."""
    MIN_HEADER_FOOTER_LINE_MATCH_RATIO: float = 0.95
    """Minimum match ratio between a specific line and a canonical header/footer example line
    to consider the specific line as belonging to a header/footer."""
    MAX_HEADER_LINES: int = 0
    """Maximum number of lines from the top of each page to consider when detecting headers."""
    MAX_FOOTER_LINES: int = 0
    """Maximum number of lines from the bottom of each page to consider when detecting footers."""
    RETAIN_CONTINUED_HEADINGS: bool = True
    """Set to False to remove '(continued)' section and table headings. Helpful for logical
    parsing operations."""

    def __post_init__(self):
        self._initialized_ = True

    def __setattr__(self, name: str, value: Any) -> None:
        if self._initialized_ and not name.startswith("_"):  # don't capture internal attrs
            self._setattrs_.add(name)
        return super().__setattr__(name, value)


class _PyPdfToTextConstants(_ConfigMixIn):
    """
    Package wide constants for pypdftotext.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, **kwargs):
        # Only initialize once
        if not hasattr(self, "_initialized_") or not self._initialized_:
            super().__init__(**kwargs)


constants = _PyPdfToTextConstants()


@dataclass(kw_only=True)
class PyPdfToTextConfig(_ConfigMixIn):
    """
    Allows behaviors driven by pypdftotext.constants members to be customized
    on a per PdfExtract basis with minimal hassle.

    Features:
     1. New instances will inherit any setting that has been set programmatically in
        `constants`. Environment variables are re-read on instance creation.
        This allows users to globally set API keys (via env OR `constants`) and other
        desired behaviors (via `constants` only) eliminating the need to supply the
        `config` parameter to every PdfExtract instantiation. I.e. if `config` is not
        defined, a new one that inherits from `constants` / env vars as described
        is created on the fly.
     2. Inheritance from the global constants can be disabled globally by setting
        `constants.INHERIT_CONSTANTS` to False or for a single PyPdfToTextConfig
        instance using the `overrides` parameter:
            `PyPdfToTextConfig(overrides={"INHERIT_CONSTANTS": False})`
        The TypedDict implementation `PdfToTextConfigOverrides` is defined above
        to provide IDE and typing support for the `overrides` parameter.
     3. If a `base` is supplied, its values supersede global constants.
     4. If both a 'base' and 'overrides' are supplied, `overrides` supersede `base`.
     5. You can also manipulate the configs of individual `PdfExtract` and
        `AzureDocIntelIntegrator` instances after creating them:
        ```
        extract = PdfExtract("file.pdf")
        extract.config.<SETTING> = <VALUE>
        # Anything set here ^^ (e.g. an API key) will be applied _before_
        # extraction operations (like OCR).
        for page_text in extract.text_pages:  # triggers extraction/OCR
            ...
        ```

    Examples:
        >>> import os
        >>> from pypdftotext._config import PyPdfToTextConfig, constants

        >>> # Example 1: New instances inherit from constants by default
        >>> constants.DISABLE_OCR = True
        >>> constants.MAX_CHARS_PER_PDF_PAGE = 50000
        >>> config1 = PyPdfToTextConfig()
        >>> config1.DISABLE_OCR
        True
        >>> config1.MAX_CHARS_PER_PDF_PAGE
        50000

        >>> # Example 2: Environment variables are re-read on each instantiation
        >>> os.environ['AZURE_DOCINTEL_ENDPOINT'] = 'https://test.cognitiveservices.azure.com/'
        >>> config2 = PyPdfToTextConfig()
        >>> config2.AZURE_DOCINTEL_ENDPOINT
        'https://test.cognitiveservices.azure.com/'
        >>> os.environ['AZURE_DOCINTEL_ENDPOINT'] = 'https://prod.cognitiveservices.azure.com/'
        >>> config3 = PyPdfToTextConfig()
        >>> config3.AZURE_DOCINTEL_ENDPOINT
        'https://prod.cognitiveservices.azure.com/'
        >>> del os.environ['AZURE_DOCINTEL_ENDPOINT']

        >>> # Example 3: Disable inheritance with INHERIT_CONSTANTS
        >>> constants.DISABLE_PROGRESS_BAR = True
        >>> config4 = PyPdfToTextConfig(overrides={"INHERIT_CONSTANTS": False})
        >>> config4.DISABLE_PROGRESS_BAR  # Not inherited from constants
        False
        >>> config4.INHERIT_CONSTANTS
        False

        >>> # Example 4: Overrides supersede base which supersedes constants
        >>> constants.FONT_HEIGHT_WEIGHT = 1.0
        >>> constants.MIN_OCR_ROTATION_DEGREES = 1e-2
        >>> base_config1 = PyPdfToTextConfig()
        >>> base_config1.MIN_OCR_ROTATION_DEGREES = 0.001
        >>> config5 = PyPdfToTextConfig(
        ...     base=base_config1,
        ...     overrides={"FONT_HEIGHT_WEIGHT": 3.0}
        ... )
        >>> config5.MIN_OCR_ROTATION_DEGREES
        0.001
        >>> config5.FONT_HEIGHT_WEIGHT
        3.0
        >>> # cleanup for doctest:
        >>> constants.DISABLE_OCR = False
        >>> constants.MAX_CHARS_PER_PDF_PAGE = 25000
        >>> constants.MIN_OCR_ROTATION_DEGREES = 1e-5
        >>> constants.DISABLE_PROGRESS_BAR = False
        >>> constants._setattrs_ = set()
    """

    overrides: InitVar[PyPdfToTextConfigOverrides | dict[str, Any] | None] = None
    base: InitVar[PyPdfToTextConfig | None] = None

    def __init__(
        self,
        overrides: PyPdfToTextConfigOverrides | dict[str, Any] | None = None,
        base: PyPdfToTextConfig | None = None,
    ):
        """
        If base is supplied, merge fields by overwriting default values in the
        new instance with non-default values from the base.
        """
        super().__init__()
        super().__post_init__()
        if base or constants.INHERIT_CONSTANTS:
            base = cast(PyPdfToTextConfig, base or constants)
            if base is constants and (overrides or {}).get("INHERIT_CONSTANTS") is False:
                pass  # if overrides disables constant inheritance, don't inherit
            else:
                for field_name in base._setattrs_:  # pylint: disable=protected-access
                    setattr(self, field_name, getattr(base, field_name))
        for field_name, val in (overrides or {}).items():
            if hasattr(self, field_name):
                setattr(self, field_name, val)
            else:
                logger.warning("Ignoring invalid override: %s=%s", field_name, val)
