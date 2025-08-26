# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pypdftotext is a Python package that provides OCR-enabled structured text extraction for PDF files. It's an extension for pypdf that:
- Extracts text from PDFs using pypdf's "layout mode"
- Falls back to Azure Document Intelligence OCR when no text is found
- Handles various PDF complexities like custom glyphs and page corruptions
- Supports batch OCR processing for efficiency

## Development Commands

### Build and Package
```bash
# Build the package using flit
python -m build

# Install in development mode
pip install -e .

# Install with optional dependencies
pip install -e ".[dev]"  # Development dependencies including type stubs
pip install -e ".[s3]"   # S3 support for reading PDFs from AWS
pip install -e ".[image]" # Image processing capabilities
pip install -e ".[full]"  # All optional dependencies
```

### Testing

The project uses **pytest** as the testing framework with the following configuration:

```bash
# Install test dependencies
pip install -e ".[test]"  # Just testing tools
pip install -e ".[dev]"   # All development dependencies including tests

# Run all tests
pytest

# Run tests with coverage
pytest --cov=pypdftotext

# Run specific test file
pytest tests/test_config.py

# Run tests by marker
pytest -m unit          # Run unit tests only
pytest -m integration   # Run integration tests only
pytest -m "not slow"    # Skip slow tests

# Run tests with verbose output
pytest -v

# Run specific test function
pytest tests/test_config.py::TestPyPdfToTextConfig::test_base_inheritance
```

**Test Structure:**
- Tests are located in the `tests/` directory
- Test files follow the pattern `test_*.py`
- Test classes start with `Test`
- Test functions start with `test_`

**pytest Configuration (in `pyproject.toml`):**
- Coverage reports generated for `pypdftotext` package
- HTML coverage report available in `htmlcov/`
- Markers available: `unit`, `integration`, `slow`

### Linting and Type Checking

The project uses the following tools via VS Code extensions (configured in `.devcontainer/devcontainer.json`):

- **Black**: Code formatter that runs automatically on save
  - Configured as the default formatter
  - Ensures consistent code style across the project

- **Pylint**: Linter that runs automatically on save
  - Configuration: `--load-plugins=pylint_pydantic --disable=W0311,R0903,C0301,C0302,W1203`
  - Disabled rules:
    - W0311: Bad indentation (handled by Black)
    - R0903: Too few public methods
    - C0301: Line too long
    - C0302: Too many lines in module
    - W1203: Use % formatting in logging functions

- **Pyright/Pylance**: Type checker that runs in real-time
  - Mode: `standard` type checking
  - Provides immediate feedback on type issues as you code
  - Note: boto3-stubs[s3] included in dev dependencies for S3 type hints

When writing code, ensure it passes all three tools:
1. Black formatting (automatic on save)
2. Pylint checks (automatic on save)
3. Pyright type checking (real-time feedback)

## Architecture

### Core Components

1. **Main API** (`pypdftotext/__init__.py`):
   - `pdf_text_pages()`: Primary function that extracts text from PDF pages
   - `pdf_text_page_lines()`: Returns text as list of lines per page
   - `handwritten_ratio()`: Calculates ratio of handwritten to total characters on OCR'd pages
   - Handles PDF reading from bytes, BytesIO, or PdfReader objects
   - Implements intelligent OCR triggering based on extracted text quality

2. **Configuration System** (`_config.py`):
   - `PyPdfToTextConfig` dataclass manages all configuration settings
   - `PyPdfToTextConfigOverrides` TypedDict for type-safe overrides
   - `constants` singleton instance for package-wide settings
   - All settings can be overridden via environment variables or programmatically
   - Supports base configuration inheritance and field overrides

3. **PDF Extraction Engine** (`pdf_extract.py`):
   - `PdfExtract` class orchestrates the entire extraction workflow
   - `ExtractedPage` dataclass tracks page metadata and source (embedded/OCR)
   - Manages page-by-page extraction with progress tracking
   - Handles S3 PDF retrieval when boto3 is available
   - Implements corruption detection and recovery
   - Coordinates batch OCR submission for efficiency

4. **Azure OCR Integration** (`azure_docintel_integrator.py`):
   - `AzureDocIntelIntegrator` class manages Azure Document Intelligence API
   - Singleton pattern with lazy client initialization
   - Handles client creation, PDF submission, and result processing
   - Supports handwritten text detection and confidence scoring
   - Manages OCR result caching and page mapping

5. **Layout Processing** (`layout.py`):
   - Handles fixed-width text layout generation from Azure OCR results
   - Manages text positioning, line breaks, and whitespace preservation
   - Applies rotation corrections from OCR results
   - Implements configurable scaling for coordinate systems

### Key Design Patterns

- **Lazy Initialization**: Azure OCR client created only when needed
- **Fallback Strategy**: Attempts embedded text extraction first, falls back to OCR based on configurable thresholds
- **Corruption Detection**: Validates extracted text length against `MAX_CHARS_PER_PDF_PAGE` to detect malformed PDFs
- **Batch OCR**: Collects all pages needing OCR and processes them in a single API call for efficiency
- **Progress Tracking**: Uses tqdm for visual progress feedback (can be disabled for logging environments)
- **Configuration Inheritance**: Settings can be layered via base configs and overrides

### Data Flow

1. PDF input (bytes/BytesIO/PdfReader) → `PdfExtract` initialization
2. Page-by-page extraction with pypdf's layout mode
3. Text quality assessment (line count, character count)
4. OCR triggering decision based on page ratios
5. Batch OCR submission if needed
6. Result assembly with source tracking
7. Optional line-by-line formatting

## Environment Variables

### Azure OCR Configuration
- `AZURE_DOCINTEL_ENDPOINT`: Azure Document Intelligence API endpoint
- `AZURE_DOCINTEL_SUBSCRIPTION_KEY`: Azure API subscription key

### AWS Configuration (for S3 support)
- `AWS_ACCESS_KEY_ID`: AWS access key for S3 access
- `AWS_SECRET_ACCESS_KEY`: AWS secret key
- `AWS_SESSION_TOKEN`: Optional session token for temporary credentials

These can also be set programmatically after import via the `constants` global settings or a `PdfToTextConfig` instance.

## Important Implementation Details

### Memory Optimization
- The codebase avoids using `splitlines()` excessively, using `count('\n')` for line counting instead
- Text is processed in streaming fashion where possible
- OCR results are cached to avoid redundant API calls

### Indexing and Boundaries
- Page indices are 0-based throughout the codebase
- OCR page indices map to PDF page indices via internal tracking

### OCR Triggering Logic
- OCR is triggered when the ratio of low-text pages exceeds `TRIGGER_OCR_PAGE_RATIO` (default 0.99)
- A page is considered "low-text" if it has fewer than `MIN_LINES_OCR_TRIGGER` lines (default 1)
- Custom glyph replacement is supported via `replace_byte_codes` parameter

### Error Handling
- Maximum 25,000 characters per page as corruption detection threshold
- Failed OCR returns empty strings with logged warnings
- Corrupted pages return empty strings after logging violations

### Thread Safety
- The current implementation uses a singleton Azure client - consider thread safety when implementing concurrent processing
- Progress bars support positioning for multi-threaded scenarios via `pbar_position`

## Code Style Guidelines

- Use type hints for all public APIs
- Follow existing patterns for dataclasses and configuration
- Log errors and warnings using Python's `logging` module
- Maintain backward compatibility when modifying public APIs
- Document complex logic with inline comments
