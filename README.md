# pypdftotext

[![PyPI version](https://badge.fury.io/py/pypdftotext.svg)](https://badge.fury.io/py/pypdftotext)
[![Python Support](https://img.shields.io/pypi/pyversions/pypdftotext)](https://pypi.org/project/pypdftotext/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**OCR-enabled PDF text extraction built on pypdf and Azure Document Intelligence**

pypdftotext is a Python package that intelligently extracts text from PDF files. It uses pypdf's advanced layout mode for embedded text extraction and seamlessly falls back to Azure Document Intelligence OCR when no embedded text is found.

## Key Features

- 🚀 **Fast embedded text extraction** using pypdf's layout mode
- 🔄 **Automatic OCR fallback** via Azure Document Intelligence when needed
- 🧵 **Thread-safe operations** with the `PdfExtract` class
- 📦 **S3 support** for reading PDFs directly from AWS S3
- 🖼️ **Image compression** to reduce PDF file sizes
- ✍️ **Handwritten text detection** with confidence scoring
- 📄 **Page manipulation** - create child PDFs and extract page subsets
- ⚙️ **Highly configurable** with extensive options

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Advanced Usage](#advanced-usage)
- [Development](#development)
- [License](#license)

## Installation

### Basic Installation

```bash
pip install pypdftotext
```

### Optional Dependencies

```bash
# For S3 support
pip install "pypdftotext[s3]"

# For image processing capabilities
pip install "pypdftotext[image]"

# For all optional features
pip install "pypdftotext[full]"

# For development (includes testing and type stubs)
pip install "pypdftotext[dev]"
```

### Requirements

- Python 3.10, 3.11, or 3.12
- pypdf 6.0
- azure-ai-documentintelligence >= 1.0.0
- tqdm (for progress bars)

## Quick Start

### Basic Text Extraction

```python
from pypdftotext import PdfExtract

# Extract text from a PDF (thread-safe)
pdf = PdfExtract("document.pdf")
text = pdf.text
print(text)

# Get text by page
for i, page_text in enumerate(pdf.text_pages):
    print(f"Page {i + 1}: {page_text[:100]}...")
```

### Legacy API (Not Thread-Safe)

```python
import pypdftotext

# Extract all pages
pdf_bytes = open("document.pdf", "rb").read()
pages = pypdftotext.pdf_text_pages(pdf_bytes)
full_text = "\n".join(pages)

# Extract as lines per page
lines_per_page = pypdftotext.pdf_text_page_lines(pdf_bytes)
```

**Note:** Legacy functions are not thread-safe. Use `PdfExtract` for concurrent operations.

### Working with S3

```python
from pypdftotext import PdfExtract

# Requires boto3 and AWS credentials (see Configuration section)
pdf = PdfExtract("s3://my-bucket/path/to/document.pdf")
text = pdf.text
```

## Configuration

### Environment Variables

Set these before importing pypdftotext:

#### Azure OCR Configuration
```bash
export AZURE_DOCINTEL_ENDPOINT="https://your-resource.cognitiveservices.azure.com/"
export AZURE_DOCINTEL_SUBSCRIPTION_KEY="your-subscription-key"
```

#### AWS S3 Configuration
```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_SESSION_TOKEN="optional-session-token"
```

### Programmatic Configuration

#### Using Global Constants
```python
from pypdftotext import constants

# Azure OCR setup
constants.AZURE_DOCINTEL_ENDPOINT = "https://your-resource.cognitiveservices.azure.com/"
constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "your-key"

# AWS S3 setup
constants.AWS_ACCESS_KEY_ID = "your-key"
constants.AWS_SECRET_ACCESS_KEY = "your-secret"
```

#### Using PyPdfToTextConfig
```python
from pypdftotext import PyPdfToTextConfig, PdfExtract

config = PyPdfToTextConfig(
    # NOTE: *LOTS* of options, *NONE* of them mandatory if you've set the necessary env vars.

    # Azure OCR Settings
    # NOTE: Endpoint and subscription key are defaulted from env vars *on instance creation*
    AZURE_DOCINTEL_ENDPOINT="https://your-endpoint.cognitiveservices.azure.com/",
    AZURE_DOCINTEL_SUBSCRIPTION_KEY="your-key",

    AZURE_DOCINTEL_AUTO_CLIENT=True,  # Auto-create client (default: True)
    AZURE_DOCINTEL_TIMEOUT=120,  # Timeout in seconds (default: 60)
    DISABLE_OCR=False,  # Disable OCR entirely (default: False)

    # AWS S3 Settings
    # NOTE: AWS credentials are defaulted from corresponding env vars *on instance creation*
    AWS_ACCESS_KEY_ID="your-key",
    AWS_SECRET_ACCESS_KEY="your-secret",
    AWS_SESSION_TOKEN=None,

    # OCR Triggering
    MIN_LINES_OCR_TRIGGER=1,  # Min lines before considering OCR (default: 1)
    TRIGGER_OCR_PAGE_RATIO=0.99,  # Ratio of pages needing OCR to trigger (default: 0.99)

    # Text Extraction
    PRESERVE_VERTICAL_WHITESPACE=True,  # Keep vertical spacing (default: False)
    SCALE_WEIGHT=1.25,  # Char width calculation weight (default: 1.25)
    FONT_HEIGHT_WEIGHT=2.5,  # Line splitting factor (default: 2.5)
    SUPPRESS_EMBEDDED_TEXT=False,  # Force OCR on all pages (default: False)

    # OCR Processing
    OCR_HANDWRITTEN_CONFIDENCE_LIMIT=0.9,  # Handwriting confidence threshold (default: 0.9)
    MIN_OCR_ROTATION_DEGREES=1,  # Min rotation to correct (default: 1e-5)
    OCR_POSITIONING_SCALE=100,  # Coordinate scaling (default: 100)
    OCR_LINE_HEIGHT_SCALE=50,  # Line height scaling (default: 50)

    # Limits & UI
    MAX_CHARS_PER_PDF_PAGE=25000,  # Corruption detection threshold (default: 25000)
    DISABLE_PROGRESS_BAR=False,  # Hide progress bars (default: False)
    PROGRESS_BAR_POSITION=None,  # tqdm position parameter (default: None)
)

pdf = PdfExtract("document.pdf", config=config)
```

### OCR Triggering Logic

OCR is automatically triggered when:
1. The ratio of low-text pages exceeds `TRIGGER_OCR_PAGE_RATIO` (default: 99% of pages)
2. A page is considered "low-text" if it has ≤ `MIN_LINES_OCR_TRIGGER` lines (default: 1)

Example: OCR only when 50% of pages have fewer than 5 lines:
```python
config = PyPdfToTextConfig(
    MIN_LINES_OCR_TRIGGER=5,
    TRIGGER_OCR_PAGE_RATIO=0.5
)
```

### Configuration Inheritance

```python
from pypdftotext import PyPdfToTextConfig, constants

# Inherit from global constants
base_config = PyPdfToTextConfig(base=constants)

# Create derived config with overrides
custom_config = PyPdfToTextConfig(
    base=base_config,
    overrides={"MIN_LINES_OCR_TRIGGER": 5}
)
```

### Legacy Function Configuration

Legacy functions accept lowercase kwargs:
```python
pages = pypdftotext.pdf_text_pages(
    pdf_bytes,
    min_lines_ocr_trigger=5,
    trigger_ocr_page_ratio=0.5,
    preserve_vertical_whitespace=False
)
```

## API Reference

### PdfExtract Class

Thread-safe class for PDF text extraction.

```python
class PdfExtract:
    def __init__(
        pdf: str | Path | bytes | io.BytesIO | PdfReader,
        config: PyPdfToTextConfig | None = None,
        **kwargs
    )
```

#### Properties
- `text` - Complete text from all pages as a single string
- `text_pages` - List of text strings, one per page
- `extracted_pages` - List of `_ExtractedPage` objects with metadata
- `reader` - Underlying PdfReader instance
- `writer` - PdfWriter for modifications
- `body` - PDF content as bytes

#### Methods

**`child_pdf(page_indices, config_overrides)`**
- Creates new PdfExtract instance for selected pages
- `page_indices`: List of indices or tuple (start, stop) inclusive
- `config_overrides`: Dict of config parameters to override

**`clip_pages(page_indices)`**
- Extract specific pages as new PDF bytes
- `page_indices`: List of indices or tuple (start, stop) inclusive
- Returns: PDF bytes

**`compress_images(white_point=220, max_overscale=2, aspect_tolerance=0.001, force=False)`**
- Compress images in PDF to reduce file size
- Converts to grayscale and downsamples large images
- **Note:** If called before OCR, compressed version is used for OCR

**`handwritten_ratio(page_index)`**
- Get ratio of handwritten to total text on OCR'd page
- Returns: Float between 0.0 and 1.0

### Legacy Functions

**`pdf_text_pages(pdf_reader, debug_path=None, page_indices=None, replace_byte_codes=None, **kwargs)`**
- Extract text from PDF pages
- Returns: List of strings (one per page)
- **Not thread-safe**

**`pdf_text_page_lines(pdf_reader, debug_path=None, page_indices=None, replace_byte_codes=None, **kwargs)`**
- Extract text as lines per page
- Returns: List of lists of strings
- **Not thread-safe**

## Advanced Usage

### Page Manipulation

```python
from pypdftotext import PdfExtract

pdf = PdfExtract("document.pdf")

# Create child PDF with specific pages
child = pdf.child_pdf(
    page_indices=[0, 2, 4],  # Pages 1, 3, 5
    config_overrides={"MIN_LINES_OCR_TRIGGER": 10}
)

# Extract pages 2-5 (inclusive)
child2 = pdf.child_pdf(page_indices=(1, 4))

# Get specific pages as new PDF bytes
pages_bytes = pdf.clip_pages([0, 1, 2])  # First 3 pages
with open("extracted_pages.pdf", "wb") as f:
    f.write(pages_bytes)
```

### Image Compression

```python
from pathlib import Path
from pypdftotext import PdfExtract

pdf = PdfExtract("large_document.pdf")

# Compress images before OCR for smaller file size
pdf.compress_images(
    white_point=220,  # Values > 220 become white (reduces noise)
    max_overscale=2,  # Downsample images > 2x page size
    aspect_tolerance=0.001  # Tolerance for full-page detection
)

# Save compressed PDF
Path("compressed.pdf").write_bytes(pdf.body)

# Now extract text (will use compressed images if OCR needed)
text = pdf.text
```

### Custom Glyph Replacement

Replace custom PDF glyphs with Unicode equivalents:

```python
# For PdfExtract
pdf = PdfExtract(
    "document.pdf",
    replace_byte_codes={
        b'\x00\x01': b'☐',  # Empty checkbox
        b'\x00\x02': b'☑',  # Checked checkbox
    }
)

# For legacy functions
pages = pypdftotext.pdf_text_pages(
    pdf_bytes,
    replace_byte_codes={
        b'\x00\x01': b'☐',
        b'\x00\x02': b'☑',
    }
)
```

### Detailed Page Information

```python
pdf = PdfExtract("document.pdf")

for i, page in enumerate(pdf.extracted_pages):
    print(f"Page {i + 1}:")
    print(f"  Source: {page.source}")  # 'embedded' or 'OCR'
    print(f"  Handwritten ratio: {page.handwritten_ratio:.1%}")
    print(f"  Text length: {len(page.text)} chars")
    print(f"  First 100 chars: {page.text[:100]}")
```

## Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/hank-ai/pypdftotext.git
cd pypdftotext

# Install in development mode
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests with coverage
pytest

# Run specific test file
pytest tests/test_config.py

# Run with verbose output
pytest -v

# Run only unit tests
pytest -m unit

# Run doctests
pytest tests/test_docstrings.py
```

### Code Quality

The project uses:
- **Black** for code formatting (required before committing)
- **Pylint** for linting
- **Pyright/Pylance** for type checking

```bash
# Format code with Black
black pypdftotext tests

# Run pylint
pylint pypdftotext

# Type check with pyright
pyright pypdftotext
```

### Project Structure

```
pypdftotext/
├── __init__.py                   # Main API functions
├── _config.py                    # Configuration management
├── pdf_extract.py                # PdfExtract class
├── azure_docintel_integrator.py  # Azure OCR integration
└── layout.py                     # Text layout processing

tests/
├── test_config.py                # Configuration tests
├── test_pdf_extract.py           # PdfExtract tests
└── test_docstrings.py            # Doctest runner
```

### Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Ensure all tests pass (`pytest`)
5. Format code with Black (`black pypdftotext tests`)
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Links

- [GitHub Repository](https://github.com/hank-ai/pypdftotext)
- [Issue Tracker](https://github.com/hank-ai/pypdftotext/issues)
- [PyPI Package](https://pypi.org/project/pypdftotext/)

## Acknowledgments

Built on top of:
- [pypdf](https://github.com/py-pdf/pypdf) for PDF parsing
- [Azure Document Intelligence](https://azure.microsoft.com/en-us/services/cognitive-services/form-recognizer/) for OCR capabilities
