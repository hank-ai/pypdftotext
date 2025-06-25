"""Global constants for pypdftotext package"""

import os
from collections.abc import Callable
from typing import Any

AZURE_DOCINTEL_ENDPOINT: str = os.getenv("AZURE_DOCINTEL_ENDPOINT", "")
"""The API endpoint of your Azure Document Intelligence instance. Defaults to
the value of the Env Var of the same name or an empty string."""
AZURE_DOCINTEL_SUBSCRIPTION_KEY: str = os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY", "")
"""The API key for your Azure Document Intelligence instance. Defaults to
the value of the Env Var of the same name or an empty string."""
AZURE_DOCINTEL_AUTO_CLIENT: bool = True
"""If True (default), the Azure Read OCR client is created automatically
upon first use."""
DISABLE_OCR: bool = False
"""Set to True to disable all OCR operations and return 'code behind' text
only."""
DISABLE_PROGRESS_BAR: bool = False
"""Set to True to disable the per page text extraction progress bar (e.g.
when logging to CloudWatch)."""
FONT_HEIGHT_WEIGHT: float = 1.0
"""Factor for adjusting line splitting behaviors
and preserved vertical whitespace in fixed width embedded text output.
NOTE: Higher values result in fewer blank lines but increase the
likelihood of triggering a split due to font height based y offsets."""
OCR_FONT_SIZE_MULT: int = 50
"""Factor between 0 and 100 for adjusting line splitting behaviors
and preserved vertical whitespace in fixed width OCR text output.
NOTE: Higher values result in fewer blank lines but increase the
likelihood of triggering a split due to font height based y offsets."""
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
total PDF pages have been marked (see `constants.TRIGGER_OCR_PAGE_RATIO`)."""
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


log: Callable[[Any], Any] = print
"""Allow callers to override simple `print` logging with a custom
logging function."""
