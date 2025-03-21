"""Global constants for pypdftotext package"""

import os

AZURE_DOCINTEL_ENDPOINT = os.getenv("AZURE_DOCINTEL_ENDPOINT", "")
"""The API endpoint of your Azure Document Intelligence instance. Defaults to
the value of the Env Var of the same name or an empty string."""
AZURE_DOCINTEL_SUBSCRIPTION_KEY = os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY", "")
"""The API key for your Azure Document Intelligence instance. Defaults to
the value of the Env Var of the same name or an empty string."""
AZURE_DOCINTEL_AUTO_CLIENT = True
"""If True (default), the Azure Read OCR client is created automatically
upon first use."""
DISABLE_OCR = False
"""Set to True to disable all OCR operations and return 'code behind' text
only."""
DISABLE_PROGRESS_BAR = False
"""Set to True to disable the per page text extraction progress bar (e.g.
when logging to CloudWatch)."""
FONT_HEIGHT_WEIGHT = 1.0
"""Factor for adjusting preserved vertical whitespace in the fixed width
output. If `PRESERVE_VERTICAL_WHITESPACE` is set to False, this setting
will have no effect. NOTE: Higher values result in fewer blank lines."""
PRESERVE_VERTICAL_WHITESPACE = False
"""If False (default), no blank lines will be present in the extracted
text. If True, blank lines are inserted whenever the nominal font height
is less than or equal to the y coord displacement."""
MAX_CHARS_PER_PDF_PAGE = 25000
"""The maximum number of characters that can conceivably appear on a single
PDF page. An 8.5inx11in page packed with nothing 6pt text would contain
~17K chars. Some malformed PDFs result in millions of extracted nonsense
characters which can lead to memory overruns (not to mention bad text).
If a page contains more characters than this, something is wrong. Clear
the value and report an empty string."""
MIN_LINES_OCR_TRIGGER = 1
"""A page is marked for OCR if it contains fewer lines in its extracted
code behind text. OCR only proceeds if a sufficient fraction of the
total PDF pages have been marked (see `constants.TRIGGER_OCR_PAGE_RATIO`)."""
TRIGGER_OCR_PAGE_RATIO = 0.99
"""OCR will proceed if and only if the fraction of pages with fewer than
`MIN_LINES_OCR_TRIGGER` lines is greater than this value. Default is 0.99,
i.e. OCR only occurs if ALL pages hit the minimum lines trigger."""
SCALE_WEIGHT = 1.25
"""Adds priority to contiguously rendered strings when calculating the
fixed char width."""


log = print
"""Allow callers to override simple `print` logging with a custom
logging function."""
