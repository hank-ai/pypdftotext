"""PDF text extraction with OCR fallback and page manipulation utilities."""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pypdf import PdfReader, PdfWriter, PageObject
from pypdf.generic import DictionaryObject, NullObject
from tqdm import tqdm

from ._config import PyPdfToTextConfig, PyPdfToTextConfigOverrides
from .azure_docintel_integrator import AzureDocIntelIntegrator
from .header_footer_detection import assign_headers_and_footers
from .extracted_page import ExtractedPage


try:
    import boto3
except ImportError:
    boto3 = None


try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None


if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


logger = logging.getLogger(__name__)


class PdfExtract:
    """
    Turns PDF bytes into structured text pages with additional utilities for
    correcting page orientations, inspecting content quality, and producing child
    PDFs.

    Args:
        pdf (str | Path | bytes | io.BytesIO | PdfReader): if str, check for s3
            download if boto3 is installed and value is an s3 uri (i.e. prefixed 's3://').
            otherwise attempt read from disk.
        config: a PyPdfToTextConfig instance that inherits from the global base config
            `constants` by default. See PyPdfToTextConfig docstring for more info.

    KwArgs:
        debug_path (Path | None, optional): Path to write pypdf debug files to.
            Defaults to None.
        replace_byte_codes (dict[bytes, bytes] | None): if supplied, raw
            text is cast to bytes, the dict keys are replaced with values,
            and resulting bytes are cast back to text. Used to replace custom
            glyphs defined in PDFs w/ (roughly) equivalent unicode characters,
            e.g. 'anesthesia billing print set' checkbox handling.
        init_extracted_pages (list[ExtractedPage]): initializes the internal self._extracted_pages
            attribute to avoid re-extracting the same pages in child PDFs.
        compressed (bool): indicates that image compression has already been
            performed for the supplied Pdf.
    """

    def __init__(
        self,
        pdf: str | Path | bytes | io.BytesIO | PdfReader,
        config: PyPdfToTextConfig | None = None,
        **kwargs,
    ) -> None:
        self.config = config or PyPdfToTextConfig()
        self.corruption_detected: bool = False
        self.body: bytes
        self.debug_path: Path | None = kwargs.get("debug_path")
        self.compressed: bool = kwargs.get("compressed", False)
        self._extracted_pages: list[ExtractedPage] | None = kwargs.get("init_extracted_pages")
        self._azure: AzureDocIntelIntegrator | None = kwargs.get("azure")
        self._batch_mode: bool = kwargs.get("_batch_mode", False)
        self.ocr_page_idxs: list[int] = []
        self._reader: PdfReader | None = None
        self._writer: PdfWriter | None = None
        self._pbar: tqdm | None = None

        # set initial body value
        if isinstance(pdf, str):
            if pdf.startswith("s3://"):
                logger.info("Attempting to pull URI '%s' from s3", pdf)
                bucket, _, key = pdf[5:].partition("/")
                s3_object = self.s3.get_object(Bucket=bucket, Key=key)
                self.body = s3_object["Body"].read()
            else:
                self.body = Path(pdf).read_bytes()
        elif isinstance(pdf, Path):
            self.body = pdf.read_bytes()
        elif isinstance(pdf, bytes):
            self.body = pdf
        elif isinstance(pdf, io.BytesIO):
            self.body = pdf.getvalue()
        else:
            if not isinstance(pdf, PdfReader):
                raise TypeError(
                    f"`pdf` must be 'str | bytes | io.BytesIO | PdfReader': {type(pdf)=!r}"
                )
            self._reader = pdf
            assert isinstance(self._reader.stream, io.BytesIO)
            self.body = self._reader.stream.getvalue()

    @property
    def extracted_pages(self) -> list[ExtractedPage]:
        """
        A list of ExtractedPage objects containing text and metadata
        for each page in the source PDF.
        """
        if not self._extracted_pages:
            self._extracted_pages = self._extract_pages()
        return self._extracted_pages

    @property
    def text_pages(self) -> list[str]:
        """
        A list of multiline strings containing a structured text representation
        of each page in the source PDF.
        """
        return [ext_pg.text for ext_pg in self.extracted_pages]

    @property
    def text_page_lines(self) -> list[list[str]]:
        """
        A list of lists of strings with each sublist containing the lines
        in the structured text representation of each page from the source PDF.
        """
        return [ext_pg.text.splitlines() for ext_pg in self.extracted_pages]

    @property
    def text(self) -> str:
        """Text extracted from all pages in a single string."""
        return "\n".join(ext_pg.text for ext_pg in self.extracted_pages)

    @property
    def reader(self) -> PdfReader:
        """The PDF reader used for text extraction. **NOT THREAD SAFE.**"""
        if self._reader is None:
            logger.debug("Initializing PdfReader for PdfExtract")
            self._reader = PdfReader(io.BytesIO(self.body))
        return self._reader

    @property
    def writer(self) -> PdfWriter:
        """The PDF writer used for image compression, child creation,
        and page rotations. **NOT THREAD SAFE.**"""
        if self._writer is None:
            logger.debug("Initializing PdfWriter for PdfExtract")
            if self._extracted_pages:
                self._writer = PdfWriter()
                self._writer.append(
                    self.reader, pages=[ext_pg.page_obj for ext_pg in self._extracted_pages]
                )
            else:
                self._writer = PdfWriter(clone_from=self.reader)
        return self._writer

    @property
    def s3(self) -> "S3Client":
        """S3 client for this instance. Raises ImportError if boto3 is not installed."""
        if boto3 is None:
            raise ImportError('boto3 not found. Run `pip install pypdftotext["s3"]`.')
        if not hasattr(self, "_s3"):
            # lazy load this one...
            logger.debug("Initializing boto3 s3 client for PdfExtract")
            self._s3 = boto3.client(  # pylint: disable=attribute-defined-outside-init
                service_name="s3",
                aws_access_key_id=self.config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=self.config.AWS_SECRET_ACCESS_KEY,
                aws_session_token=self.config.AWS_SESSION_TOKEN,
            )
        return self._s3

    def _embedded_text(self, pg: PageObject, pg_idx: int) -> str | int:
        """Get embedded PDF text using pypdf's layout mode"""
        if self.corruption_detected or self.config.SUPPRESS_EMBEDDED_TEXT:
            txt = ""
        else:
            try:
                txt = pg.extract_text(
                    extraction_mode="layout",
                    layout_mode_space_vertically=self.config.PRESERVE_VERTICAL_WHITESPACE,
                    layout_mode_scale_weight=self.config.SCALE_WEIGHT,
                    layout_mode_debug_path=self.debug_path,
                    layout_mode_font_height_weight=self.config.FONT_HEIGHT_WEIGHT,
                )
            except (ZeroDivisionError, TypeError):
                txt = "\n".join(
                    line
                    for line in pg.extract_text().splitlines()
                    if line.strip() or self.config.PRESERVE_VERTICAL_WHITESPACE
                )
        if len(txt) > self.config.MAX_CHARS_PER_PDF_PAGE:
            self.corruption_detected = True
            if self._pbar:
                self._pbar.set_postfix_str("!!! CORRUPTION DETECTED !!!")
            logger.warning(
                "Clearing corrupt pdf text pg_idx=%s; len(txt)=%s > %s char limit.",
                pg_idx,
                len(txt),
                self.config.MAX_CHARS_PER_PDF_PAGE,
            )
            txt = ""
        # this originally compared `len(txt.splitlines())` which was
        # VERY inefficient. The + 1 below preserves the original
        # behavior for the `min_lines_ocr_trigger` parameter
        line_count_less_than_ocr_trigger = txt.count("\n") + 1 <= self.config.MIN_LINES_OCR_TRIGGER
        # add as an OCR candidate if page has too few lines.
        if not self.config.DISABLE_OCR and line_count_less_than_ocr_trigger:
            return pg_idx
        return txt

    def _extract_pages(self) -> list[ExtractedPage]:
        """
        Extract text from PDF pages and return as a list of ExtractedPage objects.

        Uses PDF code-behind by default. Triggers Azure OCR per config if the fraction
        of pages having fewer than `MIN_LINES_OCR_TRIGGER` lines is greater than
        or equal to `TRIGGER_OCR_PAGE_RATIO`.

        Returns:
            list[ExtractedPage]: a list of extracted pages
        """

        self._pbar = tqdm(
            enumerate(self.reader.pages),
            desc="Extracting text",
            disable=self.config.DISABLE_PROGRESS_BAR,
            position=self.config.PROGRESS_BAR_POSITION,
            leave=None,
        )
        pre_ocr = [self._embedded_text(page, page_index) for page_index, page in self._pbar]
        self._extracted_pages = [
            ExtractedPage(pg, 0.0, txt if isinstance(txt, str) else "")
            for txt, pg in zip(pre_ocr, self.reader.pages)
        ]
        self.ocr_page_idxs = [itm for itm in pre_ocr if isinstance(itm, int)]
        if self._azure:
            self._azure.config = self.config
        azure = AzureDocIntelIntegrator(self.config) if self._azure is None else self._azure

        # Parallel Azure OCR API calls will be made later if in batch mode.
        if not self._batch_mode:
            self.ocr(azure)

        # perform byte code substitutions per 'replace_byte_codes' arg
        if self.config.REPLACE_BYTE_CODES:
            replacements = [
                (old_bytes.decode(), new_bytes.decode())
                for old_bytes, new_bytes in self.config.REPLACE_BYTE_CODES.items()
            ]
            for ext_pg in self._extracted_pages:
                if not ext_pg.text:
                    continue
                for old_, new_ in replacements:
                    ext_pg.text = ext_pg.text.replace(old_, new_)
        self.assign_document_indices()
        if not self._batch_mode:
            assign_headers_and_footers(self.extracted_pages, self.config)
        return self._extracted_pages

    def ocr(self, azure: AzureDocIntelIntegrator):
        """
        Run OCR on identified indices if the fraction of pages having fewer
        than `self.config.MIN_LINES_OCR_TRIGGER` lines is greater than or equal to
        `self.config.TRIGGER_OCR_PAGE_RATIO`. Updates the proper self.extracted_pages
        entries with OCR'd text and handwritten ratios if OCR is triggered.
        """
        rotated_pages = False  # track whether we rotated any pages and regenerate self.body if so.
        # do not OCR unless the number of pages requiring OCR / total pages exceeds a target ratio.
        # Skip OCR if in batch mode (will be handled by batch processor)
        if (
            len(self.ocr_page_idxs) / len(self.extracted_pages)
            >= self.config.TRIGGER_OCR_PAGE_RATIO
        ):
            # if not in batch mode, replacements are handled globally in _extract_pages.
            if not self._batch_mode:
                replacements = []
            else:
                replacements = [
                    (old_bytes.decode(), new_bytes.decode())
                    for old_bytes, new_bytes in (self.config.REPLACE_BYTE_CODES or {}).items()
                ]

            ocr_pages = azure.ocr_pages(self.body, self.ocr_page_idxs)
            if self.debug_path:
                (self.debug_path / "ocr_pages.json").write_text(
                    json.dumps(ocr_pages, indent=2, default=str), "utf-8"
                )
            for ocr_idx, og_pg_idx in enumerate(self.ocr_page_idxs):
                ext_pg = self.extracted_pages[og_pg_idx]
                txt = ocr_pages[ocr_idx]
                if len(txt) > self.config.MAX_CHARS_PER_PDF_PAGE:
                    logger.warning(
                        "Clearing corrupt OCR text pg_idx=%s; len(txt)=%s > %s char limit."
                        " Does page contain multiple text orientations?",
                        og_pg_idx,
                        len(txt),
                        self.config.MAX_CHARS_PER_PDF_PAGE,
                    )
                    txt = ""
                elif rotation := azure.rotation_degrees(og_pg_idx):
                    # rotations can only be applied to pages in 90 degree increments.
                    # do not report rotated content if applied rotation is 0.
                    if applied_rotation := -90 * int(round(rotation / 90.0)):
                        rotated_pages = True
                        ext_pg.page_obj.rotation += applied_rotation

                # perform byte code substitutions per 'replace_byte_codes' arg if in batch mode
                if replacements and txt:
                    for old_, new_ in replacements:
                        txt = txt.replace(old_, new_)

                ext_pg.text = txt
                ext_pg.source = "OCR"
                ext_pg.handwritten_ratio = azure.handwritten_ratio(og_pg_idx)
                ext_pg.azure_page = azure.page_at_index(og_pg_idx)

        if rotated_pages:
            logger.debug("Regenerating PdfExtract body with corrected page orientations.")
            self._regenerate_body()

    def child(
        self,
        page_indices: list[int] | tuple[int, int] | None = None,
        config_overrides: PyPdfToTextConfigOverrides | None = None,
    ) -> PdfExtract:
        """
        Creates a child PdfExtract instance for the selected pages preserving
        extracted text, image compressions, and page reorientations from the parent.

        Args:
            page_indices (list[int] | tuple[int, int] | None): a list of 0-based page indices
                OR a tuple of start page index, stop page index (inclusive) to include. If
                None (default), all pages are included in the child.
            config_overrides (PyPdfToTextConfigOverrides | None): settings to override in the
                child instance.

        Returns:
            PdfExtract: a child instance containing the pages specified.
        """
        if isinstance(page_indices, tuple):
            page_indices = list(range(page_indices[0], page_indices[1] + 1))
        elif page_indices is None:
            page_indices = list(range(len(self.reader.pages)))
        return PdfExtract(
            pdf=self.clip_pages(page_indices),
            config=PyPdfToTextConfig(base=self.config, overrides=config_overrides),
            init_extracted_pages=[
                pg for idx, pg in enumerate(self.extracted_pages) if idx in page_indices
            ],
            compressed=self.compressed,
        )

    def compress_images(
        self,
        white_point: int = 220,
        max_overscale: float = 2,
        aspect_tolerance: float = 1e-3,
        force: bool = False,
    ):
        """
        Reduces the size of the pdf by converting all images to grey scale and
        downsampling full page images more than 2x larger than the displayed
        area of the PDF.

        Args:
            white_point: pixel values greater than this are set to white (255) after
                casting to grey scale. Reduces noise for a sharper image. Values >= 256
                effectively disable denoising.
            max_overscale: the width of the image must be > max_overscale * page width
                for downsampling. the scale factor of the downsampled image is set to
                (max_overscale * page width) / image width.
            aspect_tolerance: an image is considered to be a full page image and
                eligible for downsampling if and only if the aspect ratio (width/height)
                of the image is within this tolerance of the aspect ratio of the entire page.
            force (bool): if true, ignore `self.compressed` and re-run the algorithm.

        Returns:
            None: images are updated in place in the PDF.
        """
        if Image is None or ImageOps is None:
            raise ImportError("PIL not found. Run `pip install pypdftotext[image]`.")
        if self.compressed and not force:
            logger.info("PdfExtract images are already compressed. No action taken.")
            return  # we've already compressed these images
        for ip, page in enumerate(self.writer.pages):
            for ii, img in enumerate(page.images):
                if not isinstance(img.image, Image.Image):
                    logger.debug("Bad image: page index %s image index %s", ip, ii)
                    continue
                new_img = img.image.convert("L").point(
                    lambda x: x if x < white_point else 256  # type: ignore[reportOperatorIssue]
                )
                page_aspect = abs(page.mediabox.width / page.mediabox.height)
                img_aspect = abs(new_img.width / new_img.height)
                if (
                    new_img.width > max_overscale * page.mediabox.width
                    and abs(page_aspect - img_aspect) < aspect_tolerance
                ):
                    factor = (max_overscale * page.mediabox.width) / new_img.width
                    logger.debug("Scaling pg idx %s img idx %s with factor=%.2f", ip, ii, factor)
                    new_img = ImageOps.scale(new_img, factor, resample=Image.Resampling.LANCZOS)
                img.replace(new_img, resolution=300)
        self._regenerate_body()
        self.compressed = True

    def _regenerate_body(self):
        new_body_io = io.BytesIO()
        self.writer.write(new_body_io)
        self.body = new_body_io.getvalue()
        # force reader/writer properties to reinitialize from self.body
        self._reader = None
        self._writer = None

    def clip_pages(self, page_indices: list[int] | tuple[int, int]) -> bytes:
        """
        Clip specific pages from a source pdf into a new pdf document, removing any
        globally defined images that do not appear in the clipped pages. If text
        extraction has already occurred, page orientations will also be corrected.

        Args:
            page_indices (list[int] | tuple[int, int]): a list of 0-based page indices
                OR a tuple of start page index, stop page index (inclusive) to include.

        Returns:
            bytes: the new pdf
        """
        if isinstance(page_indices, tuple):
            page_indices = list(range(page_indices[0], page_indices[1] + 1))
        pdf_writer = PdfWriter()
        pdf_writer.append(self.reader, pages=page_indices)
        ootb_bytesio = io.BytesIO()
        pdf_writer.write(ootb_bytesio)
        # Optimize storage size and prevent possible 'bleed through' of xobject data.
        xobjs: list[DictionaryObject] = []
        xobj_pages: list[list[int]] = []

        for i, out_page in enumerate(pdf_writer.pages):
            # collect all /Resources XObject references and the list of page indices
            # that reference each of them
            rsrcs = out_page.get_inherited("/Resources", {})
            if "/XObject" in rsrcs:
                xobj = rsrcs["/XObject"]
                if xobj in xobjs:
                    xobj_pages[xobjs.index(xobj)].append(i)
                else:
                    xobjs.append(xobj)
                    xobj_pages.append([i])

        replaced_one = False  # don't waste time rerunning write operation if no xobjs are cleared

        for page_group, xobj in zip(xobj_pages, xobjs):
            # find the names of XObjects that are actually referenced by a page in the output.
            name_regex = re.compile(r"(" + r"|".join(xobj.keys()) + r")\s", re.MULTILINE)
            refd_names = set(
                _m
                for i in page_group
                if (_contents := pdf_writer.pages[i].get_contents()) is not None
                for _m in name_regex.findall(str(_contents.get_data(), "utf-8", "ignore"))
            )
            # add the names of any XObjects referenced by the XObjects captured via regex.
            refd_names.update(
                xobj[img]["/SMask"]["/Name"]  # pyright: ignore
                for img in refd_names.copy()
                if img in xobj
                and "/SMask" in xobj[img]  # pyright: ignore
                and "/Name" in xobj[img]["/SMask"]  # pyright: ignore
            )
            logger.debug(
                "page_group='%s' references refd_names=%s; clearing: %s",
                page_group,
                refd_names,
                xobj.keys() - refd_names,
            )
            for unrefd_img in xobj.keys() - refd_names:
                replaced_one = True
                pdf_writer._replace_object(  # pylint:disable=protected-access
                    xobj[unrefd_img].indirect_reference, NullObject()  # pyright: ignore
                )

        # if we nulled out any xobjs, regenerate output bytes.
        if replaced_one:
            optimized_bytesio = io.BytesIO()
            pdf_writer.write(optimized_bytesio)
            return optimized_bytesio.getvalue()

        # otherwise, return ootb bytes.
        return ootb_bytesio.getvalue()

    def handwritten_ratio(
        self,
        page_index: int,
        handwritten_confidence_limit: float | None = None,
    ) -> float:
        """
        Given a page *index*, returns the ratio of handwritten to total characters on the page.

        Args:
            page_index: the 0-based index of the page to analyze
            handwritten_confidence_limit: deprecated. use config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT

        Returns:
            float: 0.0 if the supplied page index was not OCR'd or has no text. Otherwise
            the ratio of the sum of all handwritten spans on the page to the total page span.
        """
        if handwritten_confidence_limit is not None:
            logger.warning(
                "Arg 'handwritten_confidence_limit' is no longer supported."
                " Supply the desired value via `self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT`."
                "\nrequested limit: %.2f (from arg)"
                "\neffective limit: %.2f (from self.config)",
                handwritten_confidence_limit,
                self.config.OCR_HANDWRITTEN_CONFIDENCE_LIMIT,
            )
        return self.extracted_pages[page_index].handwritten_ratio

    def assign_document_indices(self):
        """
        Dynamically sets the document_idx attribute of all extracted pages.

        Heuristically determines which pages share a common ancestor PDF. Used
        for header / footer stripping. See page_fingerprint.py.
        """
        document_idx = 0
        current_fp = self.extracted_pages[0].fingerprint
        self.extracted_pages[0].document_idx = document_idx
        for i in range(1, len(self.extracted_pages)):
            if self.extracted_pages[i].fingerprint != current_fp:
                # End of current document
                document_idx += 1
                current_fp = self.extracted_pages[i].fingerprint
            self.extracted_pages[i].document_idx = document_idx
