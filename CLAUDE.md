# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pypdftotext is a Python package that provides OCR-enabled structured text extraction for PDF files. It's an extension for pypdf that:

- Extracts text from PDFs using pypdf's "layout mode"
- Falls back to Azure Document Intelligence OCR when no text is found
- Handles various PDF complexities like custom glyphs and page corruptions
- Supports batch OCR processing for efficiency

## Python Environment

Claude Code's Bash tool starts a fresh shell for every command. Python environments must be activated inline.

### Activation Protocol

1. **Check auto memory** (`MEMORY.md`) for a stored `PYTHON_CMD_PREFIX`. If present, prefix all `python`, `pip`, `pytest`, `ruff`, and `pyright` commands with it (e.g., `<PREFIX> python -m pytest tests/ -v`, `<PREFIX> pip install -e .`).
2. **If no prefix is stored** (new contributor), detect the environment:
   - **Linux/Mac**: Check for `.venv/` in the project root → prefix: `source .venv/bin/activate &&`
   - **Windows (conda)**: Source conda's bash integration, then activate the env → prefix: `source /c/<user>/anaconda3/etc/profile.d/conda.sh && conda activate <env> &&`
3. **Verify** by running: `<PREFIX> python -c "import pypdftotext; print('OK')"`
4. **If verification fails**, ask the user:
   - What Python environment manager do you use? (conda / venv / system)
   - What is the environment name or path?
   - What is the activation command for your shell?
   Then persist the answers to auto memory (`MEMORY.md`) as `PYTHON_CMD_PREFIX`.

## Development Commands

### Build and Package

```bash
# Build the package using flit
python -m build

# Install in development mode
pip install -e .

# Install with optional dependencies
pip install -e ".[s3]"   # S3 support for reading PDFs from AWS
pip install -e ".[image]" # Image processing capabilities
pip install -e ".[full]"  # All optional dependencies
pip install -e ".[dev]"  # All of the above + pytest, pytest-cov, and type stubs
```

## Architecture

### Core Components

1. **Main API** (`pypdftotext/__init__.py`):
   - `pdf_text_pages()`: Primary function that extracts text from PDF pages
   - `pdf_text_page_lines()`: Returns text as list of lines per page
   - Re-exports `PdfExtract`, `PdfExtractBatch`, `ExtractedPage`, `AllPagesRemovedError` for convenience
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
   - Accepts `str | Path | bytes | io.BytesIO | PdfReader`; a `str` may be an `s3://` URI
   - `config` param accepts `PyPdfToTextConfig`, a dict of overrides, or `None`
   - `pdf_name` keyword-only param: human-readable identifier for logging in parallel scenarios. Auto-derived from input (path stem, PdfReader metadata title, or SHA-256 hash fallback) if not supplied.
   - Key methods: `remove_pages()`, `child()`, `clip_pages()`, `compress_images()`, `add_named_destinations()`
   - Key properties: `text`, `text_pages`, `text_page_lines`, `extracted_pages`, `reader`, `writer`
   - `handwritten_ratio(page_index)` returns the handwritten ratio for a given page (method, not module-level function)
   - Implements corruption detection and recovery
   - Coordinates batch OCR submission for efficiency

4. **Extracted Page** (`extracted_page.py`):
   - `ExtractedPage` dataclass tracks page metadata and source (embedded/OCR)

5. **Azure OCR Integration** (`azure_docintel_integrator.py`):
   - `AzureDocIntelIntegrator` class manages Azure Document Intelligence API
   - Singleton pattern with lazy client initialization
   - Handles client creation, PDF submission, and result processing
   - Supports handwritten text detection and confidence scoring
   - Manages OCR result caching and page mapping

6. **Layout Processing** (`layout.py`):
   - Handles fixed-width text layout generation from Azure OCR results
   - Manages text positioning, line breaks, and whitespace preservation
   - Applies rotation corrections from OCR results
   - Implements configurable scaling for coordinate systems

7. **Batch Processing** (`batch.py`):
   - `PdfExtractBatch` for submitting multiple PDFs to Azure OCR in a single API call
   - `config` param accepts `PyPdfToTextConfig`, a dict of overrides, or `None` (same as `PdfExtract`)

8. **Header/Footer Detection** (`header_footer_detection.py`, `page_fingerprint.py`):
   - `assign_headers_and_footers()`: heuristically marks repeated page elements across documents
   - `page_fingerprint.py` groups pages by common ancestor document to isolate header/footer patterns

### Key Design Patterns

- **Keyword-Only Parameters**: `PdfExtract.__init__` uses `*` separator — params like `pdf_name` are keyword-only, followed by `**kwargs` for internal params (`debug_path`, `compressed`, `init_extracted_pages`, `_batch_mode`)
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
- `AllPagesRemovedError(ValueError)`: raised by `remove_pages()` and `child(remove_from_parent=True)` when all pages would be removed; pass `raise_on_empty=False` to suppress with a no-op

### Thread Safety

- The current implementation uses a singleton Azure client - consider thread safety when implementing concurrent processing
- `PdfReader` (and the `reader` property) is NOT thread safe — avoid triggering lazy reader init from multiple threads
- `pdf_name` derivation avoids triggering lazy reader init for bytes/BytesIO inputs for this reason
- Progress bars support positioning for multi-threaded scenarios via `pbar_position`

### Image Compression Limitations

`PdfExtract.compress_images()` does not preserve the original `/Filter` chain when calling pypdf's `img.replace()` — the replacement is re-serialized via `PIL.Image.save()`'s default encoder (Flate over raw 8-bpp pixels for mode `"L"`). For source images originally encoded with `/DCTDecode` (JPEG), this can roughly double the per-image stream size, since raw grayscale Flate-compresses much worse than JPEG. Re-applying DCT by forwarding `format="JPEG"`-style kwargs through `img.replace()` to `PIL.Image.save()` is feasible but stacks JPEG artifacts on top of the white-point-thresholded grayscale; treat as opt-in (e.g. behind a `prefer_original_filter` flag) if pursued later.

## Development Guardrails

### Testing

Provide test coverage for all new public functions, classes, and methods. Prefer doctest for self-contained methods where a docstring `Example:` section makes sense. Use `tests/` modules for tests requiring fixtures, mocks, or multi-step setup.

`PdfReader.metadata` is a read-only property — use `unittest.mock.patch.object(type(reader), "metadata", new_callable=PropertyMock)` to mock it in tests.

### Type Checking

Use proper typing for all new and modified code. Run `pyright` in standard mode before finalizing edits. Config in `pyrightconfig.json` (intentionally relaxes `reportTypedDictNotRequiredAccess` and `reportPossiblyUnboundVariable`). Suppress with `# pyright:ignore[<flag>]` only with user approval and a justifying comment.

### Linting / Formatting

**Linter/formatter**: `ruff` — config in `ruff.toml`. Line length: 99. isort first-party packages listed in `ruff.toml` under `[lint.isort]`.

Run `ruff check pypdftotext/` and `ruff format --check pypdftotext/` before finalizing edits (tests and scripts excluded from linting). Suppress lint warnings with `# noqa: <FLAG>` only with user approval and a justifying comment.

## Code Style Guidelines

- Use type hints for all public APIs
- Follow existing patterns for dataclasses and configuration
- Log errors and warnings using Python's `logging` module
- Maintain backward compatibility when modifying public APIs
- Document complex logic with inline comments
