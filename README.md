# pypdftotext

[![PyPI version](https://badge.fury.io/py/pypdftotext.svg)](https://badge.fury.io/py/pypdftotext)
[![Python Support](https://img.shields.io/pypi/pyversions/pypdftotext)](https://pypi.org/project/pypdftotext/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**OCR-enabled PDF text extraction built on pypdf and Azure Document Intelligence**

pypdftotext is a Python package that intelligently extracts text from PDF files. It uses pypdf's advanced layout mode for embedded text extraction and seamlessly falls back to Azure Document Intelligence OCR when no embedded text is found.

## Key Features

- 🚀 **Fast embedded text extraction** using pypdf's layout mode
- 🔄 **Automatic OCR fallback** via Azure Document Intelligence when needed
- 🚛 **Batch processing** with parallel OCR for multiple PDFs
- 🧵 **Thread-safe operations** with the `PdfExtract` class
- 📦 **S3 support** for reading PDFs directly from AWS S3
- 🖼️ **Image compression** to reduce PDF file sizes
- ✍️ **Handwritten text detection** with confidence scoring
- 📄 **Page manipulation** - create child PDFs and extract page subsets
- ⚙️ **Flexible Configuration** with built in env support multiple inheritance options

## Installation

### Basic Installation

```bash
pip install pypdftotext
```

### Optional Dependencies

```bash
# Install with boto3 for S3 support
pip install "pypdftotext[s3]"

# Install with pillow for scanned pdf compression support
pip install "pypdftotext[image]"

# For all optional features (s3 and pillow)
pip install "pypdftotext[full]"

# For development (full + boto3-types[s3], pytest, pytest-cov)
pip install "pypdftotext[dev]"
```

### Requirements

- Python 3.10, 3.11, or 3.12
- pypdf 6.0
- azure-ai-documentintelligence >= 1.0.0
- tqdm (for progress bars)
- boto3 (optional)
- pillow (optional)

## Quick Start

### Enable Azure OCR (optional)

> NOTE: If OCR has not been configured, only the text embedded directly in the pdf will be returned (using [pypdf's](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) layout mode).

#### OCR Prerequisites
- An Azure Subscription ([create one for free](https://azure.microsoft.com/free/cognitive-services/))
- An Azure Document Intelligence resource ([create one](https://portal.azure.com/#create/Microsoft.CognitiveServicesFormRecognizer))

#### OCR Configuration

> NOTE: The same behaviors apply to the AWS_* settings for pulling PDFs from S3.

##### You can set your Endpoint and Subscription Key globally via env vars:

```bash
export AZURE_DOCINTEL_ENDPOINT="https://your-resource.cognitiveservices.azure.com/"
export AZURE_DOCINTEL_SUBSCRIPTION_KEY="your-subscription-key"
```

##### Or via the `constants` module:

```python
from pypdftotext import constants
constants.AZURE_DOCINTEL_ENDPOINT = "https://your-resource.cognitiveservices.azure.com/"
constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "your-subscription-key"
```

You can also set these values for individual instances of the PyPdfToTextConfig class, instances of which are exposed by the `config` attribute of `PdfExtract` and `AzureDocIntelIntegrator` classes. See [below](#optional-customize-the-config).

### Basic Usage

#### Create a PdfExtract Instance
```python
from pypdftotext import PdfExtract

extract = PdfExtract("document.pdf")
```

#### Optional: Customize the Config

> NOTE: if you've [set env vars or constants](#ocr-configuration), setting the endpoint and subscription key is optional. However, it is still acceptable to set them (and any other config options) on the instance itself after creating it.

```python
extract.config.AZURE_DOCINTEL_ENDPOINT = "https://your-resource.cognitiveservices.azure.com/"
extract.config.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "your-subscription-key"
extract.config.PRESERVE_VERTICAL_WHITESPACE = True
```

#### Extract Text with OCR Fallback

```python
text = extract.text
print(text)

# Get text by page
for i, page_text in enumerate(extract.text_pages):
    print(f"Page {i + 1}: {page_text[:100]}...")
```

#### Compress Images in Scanned PDFs to Reduce File Size or Improve OCR

> NOTE: Requires the optional `pypdftotext[images]` installation.

> NOTE: Perform this step _before_ accessing text/text_pages to use the compressed PDF for OCR. Otherwise, text will already be extracted from the original version and will not be re-extracted.

```python
extract.compress_images(  # always converts images to greyscale
    white_point = 200,  # pixels with values from 201 to 255 are set to 256 (aka white) to remove scanner artifacts
    aspect_tolerance=0.01,  # resizes images whose aspect ratios (width/height) are within 0.01 of the page aspect ratio
    max_overscale = 1.5,  # images having a width more than 1.5x the displayed width of the PDF page are downsampled to 1.5x
)
```

#### Saving a Corrected or Compressed Pdf Version

> NOTE: If a scanned PDF contains upside down or rotated pages, these pages will be reoriented automatically during text extraction.

```python
from pathlib import Path
Path("compressed_corrected_document.pdf").write_bytes(extract.body)
```

#### PDF Splitting

```python
# create a new PdfExtract instance containing the first 10 pages of the original PDF.
extract_child = extract.child((0, 9))  # useful for passing config and metadata forward.
# get the bytes of a PDF containing pages 1, 3, and 5 without creating a new PdfExtract instance.
clipped_pages_pdf_bytes = extract_child.clip_pages([0, 2, 4])  # useful for quick splitting.
```

### Batch Processing

Process multiple PDFs efficiently with parallel OCR:

```python
from pypdftotext.batch import PdfExtractBatch

# Process multiple PDFs (list or dict)
pdfs = ["file1.pdf", "file2.pdf", "file3.pdf"]
# or
pdfs = {"report": "report.pdf", "invoice": "invoice.pdf"}

batch = PdfExtractBatch(pdfs)
results = batch.extract_all()  # Returns dict[str, PdfExtract]

# Access results
for name, pdf_extract in results.items():
    print(f"{name}: {len(pdf_extract.text)} characters extracted")
```

Batch processing extracts embedded text sequentially, then performs OCR in parallel for all PDFs that need it.

### S3 Support
If an S3 URI (e.g. `s3://my-bucket/path/to/document.pdf`) is supplied as the `pdf` parameter, `PdfExtract` will attempt to pull the bytes from the supplied bucket/key. AWS credentials with proper permissions must be supplied as env vars or set programmatically [as described for Azure OCR above](#ocr-configuration) or an error will result.

## Implementation Details

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

### Configuration (Optional)

The PyPdfToTextConfig and PyPdfToTextConfigOverrides (optional) classes can be used to customize the operation of individual PdfExtract instances if desired.

1. New PdfToTextConfig instances will first reinitialize all relevant settings from the [env](#you-can-set-your-endpoint-and-subscription-key-globally-via-env-vars) and then inherit any settings that have been [set programmatically](#or-via-the-constants-module) via `constants`. This allows users to globally set API keys (via env OR `constants`) and other desired behaviors (via `constants` only) eliminating the need to supply the `config` parameter to every `PdfExtract` instance.
2. Inheritance from the global constants can be disabled globally by setting `constants.INHERIT_CONSTANTS` to False or for a single PyPdfToTextConfig instance using the `overrides` parameter (e.g. `PyPdfToTextConfig(overrides={"INHERIT_CONSTANTS": False})`). The `PdfToTextConfigOverrides` TypedDict is available for IDE and typing support.
3. An alternate `base` can be supplied to the PyPdfToTextConfig constructor. If supplied, its values supersede those in the global `constants`.
4. If both a `base` and `overrides` are supplied, overlapping settings in `overrides` will supersede those in `base` (or `constants`).

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
