# pypdftotext

*An OCR-enabled structured text extraction extension for pypdf.*

Returns the text of a PDF from pypdf's "layout mode". If no text is found, optionally submit the PDF for OCR via Azure Document Intelligence.

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
  - [pdf_text_pages](#pdf_text_pages)
  - [pdf_text_page_lines](#pdf_text_page_lines)
  - [handwritten_ratio](#handwritten_ratio)
- [Azure OCR Configuration](#azure-ocr-configuration)
  - [Environment Variables](#environment-variables)
  - [Creating the OCR Client](#creating-the-ocr-client)
- [Configuration Constants](#configuration-constants)
- [OCR Triggering Behavior](#ocr-triggering-behavior)
- [Processing Outputs](#processing-outputs)

## Requirements

- **Python**: 3.10, 3.11, or 3.12
- **Dependencies**:
  - `pypdf==5.2` - PDF parsing and text extraction
  - `azure-ai-documentintelligence==1.0.1` - Azure Document Intelligence OCR integration
  - `tqdm` - Progress bar for processing feedback

## Installation

```bash
pip install pypdftotext
```

## Quick Start

```python
from pathlib import Path
import pypdftotext

# Basic usage - extract text from all pages
pdf = Path("document.pdf").read_bytes()  # can be PdfReader, bytes, or io.BytesIO
pdf_text = "\n".join(pypdftotext.pdf_text_pages(pdf))
print(pdf_text)

# Extract text as lines per page
pdf_lines = pypdftotext.pdf_text_page_lines(pdf)
for page_num, lines in enumerate(pdf_lines, 1):
    print(f"Page {page_num} has {len(lines)} lines")

# Check handwritten content ratio (for OCR'd pages)
ratio = pypdftotext.handwritten_ratio(page_index=0)
print(f"Page 1 is {ratio:.1%} handwritten")
```

## API Reference

### pdf_text_pages

Extract text from PDF pages and return as a list of multiline strings.

```python
pypdftotext.pdf_text_pages(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    replace_byte_codes: dict[bytes, bytes] | None = None,
    **kwargs
) -> list[str]
```

**Parameters:**
- `pdf_reader`: PDF input as PdfReader, bytes, or BytesIO
- `debug_path`: Optional path to write pypdf debug files
- `page_indices`: List of specific page indices to extract (0-based), None for all pages
- `replace_byte_codes`: Dictionary for replacing custom glyphs with Unicode equivalents

**Keyword Arguments:**
- `min_lines_ocr_trigger` (int): Minimum lines threshold for OCR consideration (default: from constants)
- `trigger_ocr_page_ratio` (float): Fraction of pages needing OCR to trigger batch OCR (default: from constants)
- `preserve_vertical_whitespace` (bool): Preserve blank lines in output (default: from constants)
- `scale_weight` (float): Weight for calculating fixed char width (default: from constants)
- `font_height_weight` (float): Factor for line splitting behavior (default: from constants)
- `suppress_embedded_text` (bool): Skip embedded text extraction, OCR all pages (default: from constants)
- `pbar_position` (int): Position for tqdm progress bar in parallel processing

**Returns:** List of strings, one per page

**Example:**
```python
import pypdftotext
from pathlib import Path

# Extract specific pages with custom OCR triggering
pdf_bytes = Path("document.pdf").read_bytes()
pages_text = pypdftotext.pdf_text_pages(
    pdf_bytes,
    page_indices=[0, 2, 4],  # Extract pages 1, 3, and 5
    min_lines_ocr_trigger=3,  # OCR if less than 3 lines found
    trigger_ocr_page_ratio=0.5,  # OCR if 50% of pages need it
    preserve_vertical_whitespace=True  # Keep blank lines
)

for i, page_text in enumerate(pages_text):
    print(f"Page {i+1}:\n{page_text}\n")
```

### pdf_text_page_lines

Extract text from PDF pages and return as a list of lines for each page.

```python
pypdftotext.pdf_text_page_lines(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    replace_byte_codes: dict[bytes, bytes] | None = None,
    **kwargs
) -> list[list[str]]
```

**Parameters:** Same as `pdf_text_pages`

**Returns:** List of lists, where each inner list contains lines for a page

**Example:**
```python
import pypdftotext

# Get lines for analysis
pdf_lines = pypdftotext.pdf_text_page_lines(pdf_bytes)

# Process lines individually
for page_idx, page_lines in enumerate(pdf_lines):
    print(f"Page {page_idx + 1}:")
    for line_num, line in enumerate(page_lines, 1):
        if line.strip():  # Skip empty lines
            print(f"  Line {line_num}: {line}")
```

### handwritten_ratio

Calculate the ratio of handwritten to total characters on an OCR'd page.

```python
pypdftotext.handwritten_ratio(
    page_index: int,
    handwritten_confidence_limit: float | None = None
) -> float
```

**Parameters:**
- `page_index`: 0-based index of the page to analyze
- `handwritten_confidence_limit`: Minimum confidence for handwritten detection (default: from constants)

**Returns:** Float between 0.0 and 1.0 representing the handwritten content ratio

**Note:** Returns 0.0 if the page was not OCR'd or has no content

**Example:**
```python
import pypdftotext

# Process PDF with potential handwritten content
pdf_text = pypdftotext.pdf_text_pages(pdf_bytes)

# Check each page for handwritten content
for page_idx in range(len(pdf_text)):
    ratio = pypdftotext.handwritten_ratio(page_idx)
    if ratio > 0.5:
        print(f"Page {page_idx + 1} is mostly handwritten ({ratio:.1%})")
```

## Azure OCR Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `AZURE_DOCINTEL_ENDPOINT` | No* | Azure Document Intelligence API endpoint URL | `""` |
| `AZURE_DOCINTEL_SUBSCRIPTION_KEY` | No* | Azure Document Intelligence API subscription key | `""` |

*Required for OCR functionality. Without these, only embedded PDF text extraction is available.

### Creating the OCR Client

The OCR client can be configured in three ways:

#### 1. Automatic via Environment Variables (Recommended)

Set environment variables before importing pypdftotext:

```bash
export AZURE_DOCINTEL_ENDPOINT="https://your-instance.cognitiveservices.azure.com/"
export AZURE_DOCINTEL_SUBSCRIPTION_KEY="your-api-key"
```

```python
import pypdftotext
# Client will be created automatically when OCR is needed
```

#### 2. Manual via Constants Module

```python
import pypdftotext

# Configure before processing
pypdftotext.constants.AZURE_DOCINTEL_ENDPOINT = "https://your-instance.cognitiveservices.azure.com/"
pypdftotext.constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "your-api-key"
```

#### 3. Direct Client Creation

```python
import pypdftotext

# Manually trigger client creation
pypdftotext.AZURE_READ.create_client()
```

## Configuration Constants

All constants can be modified via `pypdftotext.constants.<CONSTANT_NAME>`:

### OCR Control

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `AZURE_DOCINTEL_AUTO_CLIENT` | bool | `True` | Auto-create OCR client on first use |
| `DISABLE_OCR` | bool | `False` | Disable all OCR operations |
| `SUPPRESS_EMBEDDED_TEXT` | bool | `False` | Skip embedded text extraction, OCR all pages |
| `MIN_LINES_OCR_TRIGGER` | int | `1` | Pages with fewer lines trigger OCR consideration |
| `TRIGGER_OCR_PAGE_RATIO` | float | `0.99` | Fraction of pages needing OCR to trigger batch processing |
| `OCR_HANDWRITTEN_CONFIDENCE_LIMIT` | float | `0.8` | Minimum confidence for handwritten text detection |

### Text Extraction

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `PRESERVE_VERTICAL_WHITESPACE` | bool | `False` | Insert blank lines for vertical spacing |
| `FONT_HEIGHT_WEIGHT` | float | `1.0` | Factor for line splitting behavior |
| `SCALE_WEIGHT` | float | `1.25` | Weight for fixed char width calculation |
| `MAX_CHARS_PER_PDF_PAGE` | int | `25000` | Maximum characters per page (corruption detection) |

### OCR Processing

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `OCR_LINE_HEIGHT_SCALE` | int | `50` | Line splitting factor for OCR (0-100) |
| `OCR_POSITIONING_SCALE` | int | `100` | Coordinate upscaling factor for OCR layout |
| `MIN_OCR_ROTATION_DEGREES` | float | `1e-5` | Minimum rotation to apply from OCR results |

### UI Control

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `DISABLE_PROGRESS_BAR` | bool | `False` | Disable tqdm progress bars |

**Example Configuration:**

```python
import pypdftotext

# Configure for aggressive OCR with preserved formatting
pypdftotext.constants.MIN_LINES_OCR_TRIGGER = 5  # OCR if less than 5 lines
pypdftotext.constants.TRIGGER_OCR_PAGE_RATIO = 0.5  # OCR if 50% need it
pypdftotext.constants.PRESERVE_VERTICAL_WHITESPACE = True  # Keep formatting
pypdftotext.constants.DISABLE_PROGRESS_BAR = True  # No progress bars (for logging)

# Process PDF with custom settings
pdf_text = pypdftotext.pdf_text_pages(pdf_bytes)
```

## OCR Triggering Behavior

The library uses a two-stage decision process for OCR:

1. **Page-level detection**: Each page is checked for embedded text. If a page has fewer lines than `MIN_LINES_OCR_TRIGGER`, it's marked for potential OCR.

2. **Document-level decision**: OCR is triggered only if the ratio of marked pages to total pages meets or exceeds `TRIGGER_OCR_PAGE_RATIO`.

This prevents unnecessary OCR for documents with mostly extractable text and a few image-only pages (e.g., charts or diagrams).

**Example Scenarios:**

```python
# Scenario 1: OCR everything (scanned documents)
pypdftotext.constants.MIN_LINES_OCR_TRIGGER = 999999  # Always trigger
pypdftotext.constants.TRIGGER_OCR_PAGE_RATIO = 0.0  # Any page triggers OCR

# Scenario 2: OCR only fully scanned documents (default)
pypdftotext.constants.MIN_LINES_OCR_TRIGGER = 1  # Empty pages
pypdftotext.constants.TRIGGER_OCR_PAGE_RATIO = 0.99  # All pages must be empty

# Scenario 3: Mixed documents with some scanned pages
pypdftotext.constants.MIN_LINES_OCR_TRIGGER = 3  # Very little text
pypdftotext.constants.TRIGGER_OCR_PAGE_RATIO = 0.3  # 30% threshold
```

## Processing Outputs

### Text Output Format

- **Embedded text**: Extracted using pypdf's layout mode, preserving spatial relationships
- **OCR text**: Processed through Azure Document Intelligence, reconstructed in fixed-width format
- **Corruption handling**: Pages exceeding `MAX_CHARS_PER_PDF_PAGE` return empty strings with warnings

### Debug Output

When `debug_path` is provided:
- pypdf layout debug files are written to the specified directory
- OCR results are saved as `ocr_pages.json` for analysis

```python
from pathlib import Path

debug_dir = Path("./debug_output")
debug_dir.mkdir(exist_ok=True)

pdf_text = pypdftotext.pdf_text_pages(
    pdf_bytes,
    debug_path=debug_dir
)
# Check debug_dir for diagnostic files
```

### Return Values

- **Empty pages**: Return empty strings (`""`)
- **Failed OCR**: Returns empty strings with logged warnings
- **Corrupted pages**: Returns empty strings after logging character count violations

### Error Handling

The library logs errors and warnings using Python's `logging` module:

```python
import logging

# Enable detailed logging
logging.basicConfig(level=logging.INFO)

# Process with logging
pdf_text = pypdftotext.pdf_text_pages(pdf_bytes)
```

Common log messages:
- `"Azure OCR Client Created"` - Successful client initialization
- `"Corruption detected"` - Page exceeds character limits
- `"Failed to create Azure OCR Client"` - Missing credentials
- `"X pages OCR'd successfully"` - OCR completion status
