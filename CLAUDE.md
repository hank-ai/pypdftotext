# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pypdftotext is a Python package that provides OCR-enabled structured text extraction for PDF files. It's an extension for pypdf that:
- Extracts text from PDFs using pypdf's "layout mode"
- Falls back to Azure Document Intelligence OCR when no text is found
- Handles various PDF complexities like custom glyphs and page corruptions

## Development Commands

### Build and Package
```bash
# Build the package using flit
python -m build

# Install in development mode
pip install -e .
```

### Testing
No test framework is currently configured. When implementing tests, check for test files or ask the user for the preferred testing approach.

## Architecture

### Core Components

1. **Main API** (`pypdftotext/__init__.py`):
   - `pdf_text_pages()`: Primary function that extracts text from PDF pages
   - `pdf_text_page_lines()`: Returns text as list of lines per page
   - Handles PDF reading from bytes, BytesIO, or PdfReader objects
   - Implements intelligent OCR triggering based on extracted text quality

2. **Azure OCR Integration** (`azure_docintel_integrator.py`):
   - `AzureDocIntelIntegrator` class manages Azure Document Intelligence API calls
   - Handles client creation, PDF submission, and result processing
   - Supports handwritten text detection and confidence scoring

3. **Configuration** (`constants.py`):
   - All configurable parameters as module-level constants
   - Can be set via environment variables or programmatically
   - Key settings: OCR thresholds, text formatting, API credentials

4. **Layout Processing** (`layout.py`):
   - Handles fixed-width text layout generation from Azure OCR results
   - Manages text positioning, line breaks, and whitespace preservation

### Key Design Patterns

- **Fallback Strategy**: Attempts embedded text extraction first, falls back to OCR based on configurable thresholds
- **Corruption Detection**: Validates extracted text length against `MAX_CHARS_PER_PDF_PAGE` to detect malformed PDFs
- **Batch OCR**: Collects all pages needing OCR and processes them in a single API call for efficiency
- **Progress Tracking**: Uses tqdm for visual progress feedback (can be disabled for logging environments)

## Environment Variables

- `AZURE_DOCINTEL_ENDPOINT`: Azure Document Intelligence API endpoint
- `AZURE_DOCINTEL_SUBSCRIPTION_KEY`: Azure API subscription key

These can also be set programmatically via the `constants` module after import.

## Important Implementation Details

- Memory efficiency: The codebase avoids using `splitlines()` excessively, using `count('\n')` for line counting instead
- Page indices are 0-based throughout the codebase
- OCR is triggered when the ratio of low-text pages exceeds `TRIGGER_OCR_PAGE_RATIO` (default 0.99)
- Custom glyph replacement is supported via `replace_byte_codes` parameter
- Maximum 25,000 characters per page as corruption detection threshold