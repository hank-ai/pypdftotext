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
- 📄 **Child PDF creation** with preserved text and corrections
- ⚙️ **Highly configurable** with extensive options

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Azure OCR Setup](#azure-ocr-setup)
- [Advanced Features](#advanced-features)
- [Configuration](#configuration)
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
from pathlib import Path
import pypdftotext

# Extract text from all pages (legacy API - not thread-safe)
pdf_bytes = Path("document.pdf").read_bytes()
pages = pypdftotext.pdf_text_pages(pdf_bytes)
text = "\n".join(pages)
print(text)

# Extract as lines per page
lines_per_page = pypdftotext.pdf_text_page_lines(pdf_bytes)
for page_num, lines in enumerate(lines_per_page, 1):
    print(f"Page {page_num}:")
    for line in lines:
        print(f"  {line}")
```

### Thread-Safe Text Extraction (Recommended)

```python
from pypdftotext import PdfExtract

# Create a thread-safe extractor
pdf = PdfExtract("document.pdf")

# Get all text
full_text = pdf.text

# Get text by page
for i, page in enumerate(pdf.text_pages):
    print(f"Page {i + 1}:\n{page}\n")

# Access detailed page information
for page in pdf.extracted_pages:
    print(f"Source: {page.source}")  # 'embedded' or 'OCR'
    print(f"Handwritten ratio: {page.handwritten_ratio:.2%}")
    print(f"Text: {page.text[:100]}...")
```

### Working with S3 PDFs

```python
from pypdftotext import PdfExtract

# Requires boto3 to be installed
pdf = PdfExtract("s3://my-bucket/path/to/document.pdf")
text = pdf.text
```

### Configuring OCR

```python
import os
from pypdftotext import PdfExtract, PyPdfToTextConfig

# Set Azure credentials via environment variables
os.environ["AZURE_DOCINTEL_ENDPOINT"] = "https://your-endpoint.cognitiveservices.azure.com/"
os.environ["AZURE_DOCINTEL_SUBSCRIPTION_KEY"] = "your-key-here"

# Or configure programmatically
from pypdftotext import constants
constants.AZURE_DOCINTEL_ENDPOINT = "https://your-endpoint.cognitiveservices.azure.com/"
constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "your-key-here"

# Extract with OCR fallback
pdf = PdfExtract("scan.pdf")
text = pdf.text  # Automatically uses OCR if needed
```

## API Reference

### PdfExtract Class (Thread-Safe, Recommended)

The main class for PDF text extraction with thread-safe operations.

```python
class PdfExtract:
    def __init__(
        self,
        pdf: str | Path | bytes | io.BytesIO | PdfReader,
        config: PyPdfToTextConfig | None = None,
        **kwargs
    )
```

**Properties:**
- `text`: Complete text from all pages as a single string
- `text_pages`: List of text strings, one per page
- `extracted_pages`: List of `_ExtractedPage` objects with metadata
- `reader`: The underlying PdfReader instance
- `writer`: PdfWriter for modifications

**Methods:**

#### `child_pdf(page_indices, config_overrides)`
Create a new PdfExtract instance for selected pages.

```python
# Extract pages 2-5
child = pdf.child_pdf(page_indices=[1, 2, 3, 4])

# Or using a range
child = pdf.child_pdf(page_indices=(1, 4))  # Pages 2-5 (inclusive)
```

#### `compress_images(white_point, max_overscale, aspect_tolerance, force)`
Reduce PDF size by compressing images.

```python
pdf.compress_images(white_point=220, max_overscale=2)
compressed_bytes = pdf.body
```

#### `clip_pages(page_indices)`
Extract specific pages as a new PDF.

```python
# Get pages 1-3 as bytes
pages_1_to_3 = pdf.clip_pages([0, 1, 2])
```

#### `handwritten_ratio(page_index)`
Get the ratio of handwritten to total text on an OCR'd page.

```python
ratio = pdf.handwritten_ratio(0)  # First page
print(f"Page 1 is {ratio:.1%} handwritten")
```

### Legacy Functions (Not Thread-Safe)

#### `pdf_text_pages()`

```python
def pdf_text_pages(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    replace_byte_codes: dict[bytes, bytes] | None = None,
    **kwargs
) -> list[str]
```

Extract text from PDF pages. Returns a list of strings (one per page).

**Note:** Not thread-safe. Use `PdfExtract` for thread-safe operations.

#### `pdf_text_page_lines()`

```python
def pdf_text_page_lines(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    replace_byte_codes: dict[bytes, bytes] | None = None,
    **kwargs
) -> list[list[str]]
```

Extract text as lines per page. Returns a list of lists of strings.

**Note:** Not thread-safe. Use `PdfExtract` for thread-safe operations.

## Azure OCR Setup

### Environment Variables

Set these environment variables before importing pypdftotext:

```bash
export AZURE_DOCINTEL_ENDPOINT="https://your-resource.cognitiveservices.azure.com/"
export AZURE_DOCINTEL_SUBSCRIPTION_KEY="your-subscription-key"
```

### Programmatic Configuration

```python
from pypdftotext import constants

# Configure Azure OCR
constants.AZURE_DOCINTEL_ENDPOINT = "https://your-resource.cognitiveservices.azure.com/"
constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "your-subscription-key"

# Enable automatic client creation
constants.AZURE_DOCINTEL_AUTO_CLIENT = True  # Default

# Set OCR timeout (seconds)
constants.AZURE_DOCINTEL_TIMEOUT = 120  # Default
```

### OCR Triggering Logic

OCR is automatically triggered when:
1. The ratio of low-text pages exceeds `TRIGGER_OCR_PAGE_RATIO` (default: 0.99)
2. A page is considered "low-text" if it has fewer than `MIN_LINES_OCR_TRIGGER` lines (default: 1)

```python
from pypdftotext import PyPdfToTextConfig, PdfExtract

# Custom OCR triggering
config = PyPdfToTextConfig(
    MIN_LINES_OCR_TRIGGER=5,  # Trigger OCR if < 5 lines
    TRIGGER_OCR_PAGE_RATIO=0.5  # Trigger if 50% of pages need OCR
)

pdf = PdfExtract("document.pdf", config=config)
```

## Advanced Features

### Custom Glyph Replacement

Replace custom PDF glyphs with Unicode equivalents:

```python
# Replace custom checkbox glyphs
replacements = {
    b'\x00\x01': b'☐',  # Empty checkbox
    b'\x00\x02': b'☑',  # Checked checkbox
}

pages = pypdftotext.pdf_text_pages(
    pdf_bytes,
    replace_byte_codes=replacements
)
```

### Image Compression

Reduce PDF file size by compressing images:

**NOTE: If `compress_images` is called *before* OCR operations, the compressed version will be used for OCR.**

```python
from pypdftotext import PdfExtract

pdf = PdfExtract("large_document.pdf")

# Compress images (converts to grayscale, downsamples large images)
pdf.compress_images(
    white_point=220,  # Values > 220 become white
    max_overscale=2,  # Downsample images > 2x page size
    aspect_tolerance=0.001  # Tolerance for full-page detection
)

# Save compressed PDF
Path("compressed.pdf").write_bytes(pdf.body)
```

### Working with Page Subsets

```python
from pypdftotext import PdfExtract

pdf = PdfExtract("document.pdf")

# Create child PDF with specific pages
child = pdf.child_pdf(
    page_indices=[0, 2, 4],  # Pages 1, 3, 5
    config_overrides={"MIN_LINES_OCR_TRIGGER": 10}
)

# Extract specific pages as new PDF bytes
pages_1_to_5 = pdf.clip_pages((0, 4))  # Inclusive range
```

### S3 Integration

```python
from pypdftotext import PdfExtract, PyPdfToTextConfig
import os

# Configure AWS credentials
os.environ["AWS_ACCESS_KEY_ID"] = "your-key"
os.environ["AWS_SECRET_ACCESS_KEY"] = "your-secret"

# Or via config
config = PyPdfToTextConfig(
    AWS_ACCESS_KEY_ID="your-key",
    AWS_SECRET_ACCESS_KEY="your-secret"
)

# Read directly from S3
pdf = PdfExtract("s3://bucket/path/to/file.pdf", config=config)
text = pdf.text
```

## Configuration

### PyPdfToTextConfig Class

All configuration options can be set via the `PyPdfToTextConfig` class:

```python
from pypdftotext import PyPdfToTextConfig, PdfExtract

config = PyPdfToTextConfig(
    # OCR Triggering
    MIN_LINES_OCR_TRIGGER=1,  # Min lines before OCR consideration
    TRIGGER_OCR_PAGE_RATIO=0.99,  # Ratio of pages needing OCR to trigger

    # Text Extraction
    PRESERVE_VERTICAL_WHITESPACE=True,  # Keep vertical spacing
    SCALE_WEIGHT=1.25,  # Weight for char width calculation
    FONT_HEIGHT_WEIGHT=2.5,  # Factor for line splitting
    SUPPRESS_EMBEDDED_TEXT=False,  # Force OCR on all pages

    # Azure OCR
    AZURE_DOCINTEL_ENDPOINT=None,  # Azure endpoint URL
    AZURE_DOCINTEL_SUBSCRIPTION_KEY=None,  # Azure API key
    AZURE_DOCINTEL_AUTO_CLIENT=True,  # Auto-create client
    AZURE_DOCINTEL_TIMEOUT=120,  # OCR timeout in seconds
    DISABLE_OCR=False,  # Disable OCR entirely

    # OCR Processing
    OCR_HANDWRITTEN_CONFIDENCE_LIMIT=0.9,  # Handwriting confidence
    MIN_OCR_ROTATION_DEGREES=5,  # Min rotation to correct
    OCR_POSITIONING_SCALE=100,  # Coordinate scaling
    OCR_LINE_HEIGHT_SCALE=50,  # Line height scaling

    # Limits
    MAX_CHARS_PER_PDF_PAGE=25000,  # Corruption detection threshold

    # UI
    DISABLE_PROGRESS_BAR=False,  # Hide progress bars
    PROGRESS_BAR_POSITION=None,  # tqdm position parameter

    # AWS S3
    AWS_ACCESS_KEY_ID=None,
    AWS_SECRET_ACCESS_KEY=None,
    AWS_SESSION_TOKEN=None
)

pdf = PdfExtract("document.pdf", config=config)
```

### Configuration Inheritance

```python
from pypdftotext import PyPdfToTextConfig, constants

# Create base config from global constants
base_config = PyPdfToTextConfig(base=constants)

# Create derived config with overrides
custom_config = PyPdfToTextConfig(
    base=base_config,
    overrides={"MIN_LINES_OCR_TRIGGER": 5}
)
```

### Legacy Function Configuration

The legacy functions accept configuration via kwargs:

```python
pages = pypdftotext.pdf_text_pages(
    pdf_bytes,
    min_lines_ocr_trigger=5,
    trigger_ocr_page_ratio=0.5,
    preserve_vertical_whitespace=False,
    disable_progress_bar=True
)
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
├── __init__.py           # Main API functions
├── _config.py            # Configuration management
├── pdf_extract.py        # PdfExtract class
├── azure_docintel_integrator.py  # Azure OCR integration
└── layout.py             # Text layout processing

tests/
├── test_config.py        # Configuration tests
└── test_docstrings.py    # Doctest runner
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
