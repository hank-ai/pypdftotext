# pypdftotext

*An OCR enabled structured text extraction extension for pypdf.*

Returns the text of a pdf in from pypdf's "layout mode". If no text is found, optionally submit the PDF for OCR via Azure Document Intelligence.

## Dependencies

- Python 3.10+
- pypdf 5.2+
- azure-ai-documentintelligence 1.0.1
- tqdm

## Installation

```cmd
pip install pypdftotext
```

## Usage

```python
from pathlib import Path
import pypdftotext
pdf = Path("some_pdf.pdf").read_bytes()  # can be PdfReader, bytes, or io.BytesIO
pdf_text = "\n".join(pypdftotext.pdf_text_pages(pdf))
print(pdf_text)
```

## Creating the OCR Client

### Automatic (via Environment Variables)

Set enviornment variables `AZURE_DOCINTEL_ENDPOINT` and `AZURE_DOCINTEL_SUBSCRIPTION_KEY` to the values for your organization. The constants below will inherit from their epynomous env var *on import*. The constants module (see below) must be used to adjust these values after import.

### Manual (via constants)

```python
import pypdftotext
pypdftotext.constants.AZURE_DOCINTEL_ENDPOINT = "https://your.document-intelligence.endpoint/"
pypdftotext.constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY = "https://your.document-intelligence.endpoint/"
```

## The `constants` Module

`pypdftotext` can be tailored for your use case by setting the variables in the `constants.py` module, e.g. `pypdftotext.constants.<CONSTANT NAME> = <CONSTANT VALUE>`. See the module contents for a detailed description of adjustable parameters.
