"""Extract text from pdf pages from codebehind or Azure OCR as required"""

__version__ = "0.0.9"

import io
import json
import logging
from pathlib import Path

from pypdf import PdfReader, PageObject
from tqdm import tqdm

from . import constants, layout
from .azure_docintel_integrator import AZURE_READ

logger = logging.getLogger(__name__)


def pdf_text_pages(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    replace_byte_codes: dict[bytes, bytes] | None = None,
    **kwargs,  # prevent errors due to bad args in upstream config dicts
) -> list[str]:
    """
    Extract text from PDF pages and return as a list of multiline strings.

    Uses PDF code-behind by default. Triggers Azure OCR if the fraction
    of pages having fewer than `min_lines_ocr_trigger` lines is greater than
    or equal to `trigger_ocr_page_ratio`.

    Args:
        pdf_reader (PdfReader | io.BytesIO | bytes): Pdf with pages to extract
            as an already instantiated PdfReader or the raw pdf bytes or BytesIO.
        debug_path (Path | None, optional): Path to write pypdf debug files to.
            Defaults to None.
        page_indices (list[int] | None): if provided, only extract text from
            the listed page indices. Default is None (extract all pages).
        replace_byte_codes (dict[bytes, bytes] | None): if supplied, raw
            text is cast to bytes, the dict keys are replaced with values,
            and resulting bytes are cast back to text. Used to replace custom
            glyphs defined in PDFs w/ (roughtly) equivalent unicode charcters,
            e.g. 'anesthesia billing print set' checkbox handling.

    KwArgs:
        min_lines_ocr_trigger (int): Mark for Azure OCR if enabled
            and the line count of the text extracted from the codebehind is
            less than this value. Defaults to 1 (aka '') via
            `constants.MIN_LINES_OCR_TRIGGER`.
        trigger_ocr_page_ratio (float): Ratio of pages marked for OCR
            to total pages must be greater than this for OCR to occur. Prevents
            OCR of 'chart only' anesthesia record pages in an otherwise
            extractable PDF. Defaults to 0.99 (aka _all_ pages must require
            OCR) via `constants.TRIGGER_OCR_PAGE_RATIO`.
        preserve_vertical_whitespace (bool): If True, preserve vertical
            whitespace by inserting empty lines when a Y coord delta exceeds the
            font height. Defaults to `constants.PRESERVE_VERTICAL_WHITESPACE`.
        scale_weight (float): Weights char width averages of longer
            contiguous strings when calculating overall average fixed char
            width. Adjust as needed to control for excessive whitespace.
            Defaults to `constants.SCALE_WEIGHT`.
        font_height_weight (float): Factor for adjusting line splitting behaviors
            and preserved vertical whitespace in fixed width embedded text output.
            NOTE: Higher values result in fewer blank lines but increase the
            likelihood of triggering a split due to font height based y offsets.
            Defaults to `constants.FONT_HEIGHT_WEIGHT`.
        suppress_embedded_text (bool): if true, embedded text extraction will not
            be attempted. Assuming OCR is available, all pages will be OCR'd by
            default. Defaults to `constants.SUPPRESS_EMBEDDED_TEXT`, aka False.
        pbar_position (int): if supplied and constants.DISABLE_PROGRESS_BAR is
            False, set the postition argument of all tqdm objects to this value.

    Returns:
        list[str]: a string of text extracted for each page
    """
    if isinstance(pdf_reader, io.BytesIO):
        pdf_reader = pdf_reader.getvalue()
    if isinstance(pdf_reader, bytes):
        pdf_reader = PdfReader(io.BytesIO(pdf_reader))
    assert isinstance(pdf_reader, PdfReader)
    min_lines_ocr_trigger = kwargs.pop("min_lines_ocr_trigger", constants.MIN_LINES_OCR_TRIGGER)
    trigger_ocr_page_ratio = kwargs.pop("trigger_ocr_page_ratio", constants.TRIGGER_OCR_PAGE_RATIO)
    preserve_vertical_whitespace = AZURE_READ.preserve_vertical_whitespace = kwargs.pop(
        "preserve_vertical_whitespace", constants.PRESERVE_VERTICAL_WHITESPACE
    )
    font_height_weight = AZURE_READ.font_height_weight = kwargs.pop(
        "font_height_weight", constants.FONT_HEIGHT_WEIGHT
    )
    scale_weight = kwargs.pop("scale_weight", constants.SCALE_WEIGHT)
    suppress_embedded_text = kwargs.pop("suppress_embedded_text", constants.SUPPRESS_EMBEDDED_TEXT)
    pbar_position = AZURE_READ.pbar_position = kwargs.pop("pbar_position", None)
    if pbar_position and not isinstance(pbar_position, int):
        pbar_position = None
        AZURE_READ.pbar_position = None
    if kwargs:
        logger.warning("Unrecognized extract text kwargs: %s", list(kwargs))
    assert isinstance(pdf_reader.stream, io.BytesIO)
    AZURE_READ.reset()
    pdf_pbar = tqdm(
        (
            (i, pg)
            for i, pg in enumerate(pdf_reader.pages)
            if page_indices is None or i in page_indices
        ),
        desc="Extracting text",
        total=len(page_indices or pdf_reader.pages),
        disable=constants.DISABLE_PROGRESS_BAR,
        position=pbar_position,
    )
    corruption_detected = False

    def _page_text(pg: PageObject, pg_idx: int) -> str | int:
        nonlocal corruption_detected
        if corruption_detected or suppress_embedded_text:
            txt = ""
        else:
            try:
                txt = pg.extract_text(
                    extraction_mode="layout",
                    layout_mode_space_vertically=preserve_vertical_whitespace,
                    layout_mode_scale_weight=scale_weight,
                    layout_mode_debug_path=debug_path,
                    layout_mode_font_height_weight=font_height_weight,
                )
            except (ZeroDivisionError, TypeError):
                txt = "\n".join(
                    line
                    for line in pg.extract_text().splitlines()
                    if line.strip() or preserve_vertical_whitespace
                )
        if len(txt) > constants.MAX_CHARS_PER_PDF_PAGE:
            corruption_detected = True
            pdf_pbar.set_postfix_str("!!! CORRUPTION DETECTED !!!")
            logger.warning(
                "Clearing corrupt pdf text pg_idx=%s; len(txt)=%s > %s char limit.",
                pg_idx,
                len(txt),
                constants.MAX_CHARS_PER_PDF_PAGE,
            )
            txt = ""
        # this originally compared `len(txt.splitlines())` which was
        # VERY inefficient. The + 1 below preserves the original
        # behavior for the `min_lines_ocr_trigger` parameter
        line_count_less_than_ocr_trigger = txt.count("\n") + 1 <= min_lines_ocr_trigger
        if (  # auto client is enabled and this one needs to OCR, so...
            AZURE_READ.client is None
            and constants.AZURE_DOCINTEL_AUTO_CLIENT
            and line_count_less_than_ocr_trigger
        ):
            AZURE_READ.create_client()  # ... create the client.
        if constants.DISABLE_OCR or AZURE_READ.client is None:
            return txt
        # add as an OCR candidate if page has too few lines. See + 1 comment above.
        if line_count_less_than_ocr_trigger:
            return pg_idx
        return txt

    pre_ocr = [_page_text(page, page_index) for page_index, page in pdf_pbar]
    result = [v if isinstance(v, str) else "" for v in pre_ocr]
    ocr_page_idxs = [itm for itm in pre_ocr if isinstance(itm, int)]
    # do not OCR unless the number of pages requiring OCR / total pages exceeds a target ratio.
    if len(ocr_page_idxs) / len(result) >= trigger_ocr_page_ratio:
        ocr_pages = AZURE_READ.ocr_pages(pdf_reader.stream.getvalue(), ocr_page_idxs)
        if debug_path:
            debug_path.joinpath("ocr_pages.json").write_text(
                json.dumps(ocr_pages, indent=2, default=str), "utf-8"
            )
        for ocr_idx, og_pg_idx in enumerate(ocr_page_idxs):
            txt = ocr_pages[ocr_idx]
            if len(txt) > constants.MAX_CHARS_PER_PDF_PAGE:
                logger.warning(
                    "Clearing corrupt OCR text pg_idx=%s; len(txt)=%s > %s char limit."
                    " Does page contain multiple text orientations?",
                    og_pg_idx,
                    len(txt),
                    constants.MAX_CHARS_PER_PDF_PAGE,
                )
                txt = ""
            repl_idx = og_pg_idx if page_indices is None else page_indices.index(og_pg_idx)
            result[repl_idx] = txt

    # perform byte code substitutions per 'replace_byte_codes' arg
    if replace_byte_codes:
        for idx, txt in enumerate(result):
            for old_bytes, new_bytes in replace_byte_codes.items():
                txt = txt.replace(old_bytes.decode(), new_bytes.decode())
            result[idx] = txt

    logger.info("Text extraction complete.")
    return result


def pdf_text_page_lines(
    pdf_reader: PdfReader | io.BytesIO | bytes,
    debug_path: Path | None = None,
    page_indices: list[int] | None = None,
    replace_byte_codes: dict[bytes, bytes] | None = None,
    **kwargs,  # prevent errors due to bad args in upstream config dicts
) -> list[list[str]]:
    """
    Extract text from PDF pages and return as a list of lines for each page.

    Uses PDF code-behind by default. Triggers Azure OCR if the fraction
    of pages having fewer than `min_lines_ocr_trigger` lines is greater than
    or equal to `trigger_ocr_page_ratio`.

    Args:
        pdf_reader (PdfReader | io.BytesIO | bytes): Pdf with pages to extract
            as an already instantiated PdfReader or the raw pdf bytes or BytesIO.
        debug_path (Path | None, optional): Path to write pypdf debug files to.
            Defaults to None.
        page_indices (list[int] | None): if provided, only extract text from
            the listed page indices. Default is None (extract all pages).
        replace_byte_codes (dict[bytes, bytes] | None): if supplied, raw
            text is cast to bytes, the dict keys are replaced with values,
            and resulting bytes are cast back to text. Used to replace custom
            glyphs defined in PDFs w/ (roughtly) equivalent unicode charcters,
            e.g. 'anesthesia billing print set' checkbox handling.

    KwArgs:
        min_lines_ocr_trigger (int): Mark for Azure OCR if enabled
            and the line count of the text extracted from the codebehind is
            less than this value. Defaults to 1 (aka '') via
            `constants.MIN_LINES_OCR_TRIGGER`.
        trigger_ocr_page_ratio (float): Ratio of pages marked for OCR
            to total pages must be greater than this for OCR to occur. Prevents
            OCR of 'chart only' anesthesia record pages in an otherwise
            extractable PDF. Defaults to 0.99 (aka _all_ pages must require
            OCR) via `constants.TRIGGER_OCR_PAGE_RATIO`.
        preserve_vertical_whitespace (bool): If True, preserve vertical
            whitespace by inserting empty lines when a Y coord delta exceeds the
            font height. Defaults to `constants.PRESERVE_VERTICAL_WHITESPACE`.
        scale_weight (float): Weights char width averages of longer
            contiguous strings when calculating overall average fixed char
            width. Adjust as needed to control for excessive whitespace.
            Defaults to `constants.SCALE_WEIGHT`.
        font_height_weight (float): Factor for adjusting line splitting behaviors
            and preserved vertical whitespace in fixed width embedded text output.
            NOTE: Higher values result in fewer blank lines but increase the
            likelihood of triggering a split due to font height based y offsets.
            Defaults to `constants.FONT_HEIGHT_WEIGHT`.
        suppress_embedded_text (bool): if true, embedded text extraction will not
            be attempted. Assuming OCR is available, all pages will be OCR'd by
            default. Defaults to `constants.SUPPRESS_EMBEDDED_TEXT`, aka False.

    Returns:
        list[list[str]]: a list of lines of text extracted for each page
    """
    return [
        pg.splitlines()
        for pg in pdf_text_pages(
            pdf_reader, debug_path, page_indices, replace_byte_codes, **kwargs
        )
    ]


def handwritten_ratio(
    page_index: int,
    handwritten_confidence_limit: float | None = None,
) -> float:
    """
    Given a page *index*, returns the ratio of handwritten to total characters on the page.

    Args:
        page_index: the 0-based index of the page to analyze
        handwritten_confidence_limit: the spans of handwritten styles with confidences
            less than this limit will not be considered. Defaults to
            constants.OCR_HANDWRITTEN_CONFIDENCE_LIMIT.

    Returns:
        float: 0.0 if the supplied page index was not OCR'd or of length 0.0. Otherwise
        the ratio of the sum of all handwritten spans on the page to the total page span.
    """
    return AZURE_READ.handwritten_ratio(page_index, handwritten_confidence_limit)


__all__ = [
    "constants",
    "layout",
    "AZURE_READ",
    "handwritten_ratio",
    "pdf_text_pages",
    "pdf_text_page_lines",
]
