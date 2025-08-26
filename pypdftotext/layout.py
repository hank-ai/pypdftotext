"""Functions for converting bbox info to a fixed width page layout"""

import logging
import math
from itertools import groupby

from azure.ai.documentintelligence.models import DocumentLine, DocumentPage

from ._config import constants, PyPdfToTextConfig

logger = logging.getLogger(__name__)


def rotated_bbox(
    line: DocumentLine,
    page: DocumentPage,
    min_ocr_rotation_degrees: float = constants.MIN_OCR_ROTATION_DEGREES,
) -> list[float]:
    """
    Rotate the bounding box for the line according to the angle of rotation
    reported for the page.

    Args:
        line: an Azure DocumentLine instance
        page: an Azure DocumentPage instance
        min_ocr_rotation_degrees: min reported rotation to apply.

    Returns:
        list[float]: list of 8 floats corresponding to the coordinates of the
            corners of the bounded region.

    Examples:
        >>> from azure.ai.documentintelligence.models import DocumentLine, DocumentPage
        >>> # Create a line with a bounding box at corners (1,2), (3,2), (3,3), (1,3)
        >>> line = DocumentLine(polygon=[1.0, 2.0, 3.0, 2.0, 3.0, 3.0, 1.0, 3.0])
        >>> # Page with no rotation
        >>> page = DocumentPage(page_number=1, angle=0.0, width=8.5, height=11.0)
        >>> rotated_bbox(line, page, min_ocr_rotation_degrees=5.0)
        [1.0, 2.0, 3.0, 2.0, 3.0, 3.0, 1.0, 3.0]
        >>> # Page with small rotation (below threshold)
        >>> page_small = DocumentPage(page_number=1, angle=3.0, width=8.5, height=11.0)
        >>> rotated_bbox(line, page_small, min_ocr_rotation_degrees=5.0)
        [1.0, 2.0, 3.0, 2.0, 3.0, 3.0, 1.0, 3.0]
        >>> # Page rotated 90 degrees (simulating a page that needs correction)
        >>> page_rotated = DocumentPage(page_number=1, angle=90.0, width=8.5, height=11.0)
        >>> result = rotated_bbox(line, page_rotated, min_ocr_rotation_degrees=5.0)
        >>> # Verify rotation occurred (values will differ from original)
        >>> result != [1.0, 2.0, 3.0, 2.0, 3.0, 3.0, 1.0, 3.0]
        True
    """
    if line.polygon is None:
        raise ValueError(f"Bad Azure DocumentLine: polygon is None for {line=}")
    if page.angle is None or abs(page.angle) < min_ocr_rotation_degrees:
        return line.polygon
    # We're *reversing* the reported angle, so convert as `-angle`
    angle = math.radians(-page.angle)
    _sin = math.sin(angle)
    _cos = math.cos(angle)
    height = page.height or 11
    width = page.width or 8.5
    p = line.polygon

    def _rotate_point(x: float, y: float) -> tuple[float, float]:
        return (
            _cos * (x - width / 2) - _sin * (y - height / 2) + width / 2,
            _sin * (x - width / 2) + _cos * (y - height / 2) + height / 2,
        )

    return [
        *_rotate_point(p[0], p[1]),
        *_rotate_point(p[2], p[3]),
        *_rotate_point(p[4], p[5]),
        *_rotate_point(p[6], p[7]),
    ]


class CharGroup:
    """
    Describes the font size and bounding box for a rendered text element.
    If multiple CharGroup instances fall on the same line, the text will be
    combined with proper spacing added according to x coordinate offsets
    during the `y_coordinate_groups` operation.

    NOTE: This class scales coordinates using OCR_POSITIONING_SCALE (default: 100)
    for x/y positioning and OCR_LINE_HEIGHT_SCALE (default: 50) for height
    calculations. The different scaling factors help prevent improper line
    splitting when pages have rotation angles.

    Args:
        line: an Azure DocumentLine instance
        page: an Azure DocumentPage instance
        config: a PyPdfToTextConfig instance inherits from global base config
            `constants` by default. See PyPdfToTextConfig docstring for more info.

    Attributes:
        tx (float): x coordinate of first character in CharGroup
        ty (float): y coordinate of first character in CharGroup
        effective_height (float): effective bbox height
        text (str): rendered text
        displaced_tx (float): x coordinate of the right edge of the CharGroup's bounding box
    """

    def __init__(
        self,
        line: DocumentLine,
        page: DocumentPage,
        config: PyPdfToTextConfig | None = None,
    ) -> None:
        config = config or PyPdfToTextConfig()
        if line.polygon is None:
            raise ValueError(f"Bad Azure DocumentLine: {line.polygon=!r}")
        bbox = rotated_bbox(line, page, config.MIN_OCR_ROTATION_DEGREES)
        self.tx: float = bbox[0] * config.OCR_POSITIONING_SCALE
        self.ty: float = bbox[1] * config.OCR_POSITIONING_SCALE
        self.effective_height: float = (bbox[-1] - bbox[1]) * config.OCR_LINE_HEIGHT_SCALE
        self.text: str = line.content
        self.displaced_tx: float = bbox[2] * config.OCR_POSITIONING_SCALE

    def offset_x_coords(self, offset: float) -> "CharGroup":
        """Decrement self.tx and self.displaced_tx by the offset and return self."""
        self.tx -= offset
        self.displaced_tx -= offset
        return self

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__}:\n"
            f"  tx:               {self.tx:.2f}\n"
            f"  ty:               {self.ty:.2f}\n"
            f"  effective_height: {self.effective_height:.2f}\n"
            f"  text:             {self.text!r}\n"
            f"  displaced_tx:     {self.displaced_tx:.2f}"
        )


def dedented_groups(groups: list[CharGroup]) -> list[CharGroup]:
    """Finds the minimum x coord reported across all groups and decrements the
    x coords of all groups by this minimum. Ensures the rendered page output
    is aligned such that the leftmost rendered text appears in the first column
    of the page output.

    Returns:
        list[CharGroup]: sorted by y-coordinate (top to bottom) then x-coordinate (right to
            left), with x-coordinates adjusted to align leftmost text to position 0
    """
    min_x = min((x.tx for x in groups), default=0.0)
    dedented = [
        grp.offset_x_coords(min_x)
        # need to think about this sorting. Seems odd to negate
        # both and then reverse.
        for grp in sorted(groups, key=lambda x: (-x.ty, -x.tx), reverse=True)
        if grp.text
    ]
    if logger.getEffectiveLevel() == logging.DEBUG:
        logger.debug("**** DEDENTED GROUPS ****\n%s\n", "\n".join(str(grp) for grp in dedented))
    return dedented


def y_coordinate_groups(groups: list[CharGroup]) -> dict[int, list[CharGroup]]:
    """
    Group text operations by rendered y coordinate, i.e. the line number.

    Args:
        groups: list of CharGroup instances as returned by dedented_groups()

    Returns:
        dict[int, list[CharGroup]]: dict mapping y-coordinate positions to lists of
            CharGroup instances that appear on the same line
    """
    ty_groups = {
        ty: sorted(grp, key=lambda x: x.tx)
        for ty, grp in groupby(groups, key=lambda _grp: int(_grp.ty))
    }
    # combine groups whose y coordinates differ by less than the effective height
    # (accounts for mixed fonts and other minor oddities)
    last_ty = next(iter(ty_groups))
    last_txs = {int(_t.tx) for _t in ty_groups[last_ty] if _t.text.strip()}
    for ty in list(ty_groups)[1:]:
        fsz = min(ty_groups[_y][0].effective_height for _y in (ty, last_ty))
        txs = {int(_t.tx) for _t in ty_groups[ty] if _t.text.strip()}
        # prevent merge if both groups are rendering in the same x position.
        no_text_overlap = not txs & last_txs
        offset_less_than_font_height = abs(ty - last_ty) < fsz
        if no_text_overlap and offset_less_than_font_height:
            ty_groups[last_ty] = sorted(ty_groups.pop(ty) + ty_groups[last_ty], key=lambda x: x.tx)
            last_txs |= txs
        else:
            last_ty = ty
            last_txs = txs
    if logger.getEffectiveLevel() == logging.DEBUG:
        logger.debug(
            "**** Y COORDINATE GROUPS ****\n----- %s\n",
            "\n----- ".join(
                f"Y-Position: {ty} -----\n" + "\n".join(str(grp) for grp in grps)
                for ty, grps in ty_groups.items()
            ),
        )
    return ty_groups


def fixed_char_width(groups: list[CharGroup], scale_weight: float = 1.25) -> float:
    """
    Calculate average character width weighted by the length of the rendered
    text in each sample for conversion to fixed-width layout.

    Args:
        groups (list[CharGroup]): list of CharGroup instances created from
            the lines of an Azure OCR API response.
        scale_weight (float, optional): Weight factor applied to text length when
            calculating average character width. Default is 1.25.

    Returns:
        float: fixed character width

    """
    char_widths = []
    for _group in groups:
        _len = len(_group.text) * scale_weight
        char_widths.append(((_group.displaced_tx - _group.tx) / _len, _len))
    return sum(_w * _l for _w, _l in char_widths) / sum(_l for _, _l in char_widths)


def fixed_width_page(page: DocumentPage, config: PyPdfToTextConfig | None = None) -> str:
    """
    Generate structured page text from a DocumentPage object in an Azure Document
    Intelligence response.

    Args:
        page: an Azure DocumentPage instance
        config: a PyPdfToTextConfig instance that inherits from the global base config
            `constants` by default. See PyPdfToTextConfig docstring for more info.

    Returns:
        str: page text in a fixed width format that closely adheres to the rendered
            layout in the source pdf.

    """
    config = config or PyPdfToTextConfig()
    if not page.lines:
        return ""
    groups = dedented_groups(
        [CharGroup(_line, page, config) for _line in page.lines or [] if _line.polygon is not None]
    )
    ty_groups = y_coordinate_groups(groups)
    char_width = fixed_char_width(groups)
    lines: list[str] = []
    last_y_coord = 0
    for y_coord, line_data in ty_groups.items():
        if config.PRESERVE_VERTICAL_WHITESPACE and lines:
            fh = line_data[0].effective_height
            blank_lines = (
                0
                if fh == 0
                else (int(abs(y_coord - last_y_coord) / (fh * config.FONT_HEIGHT_WEIGHT)) - 1)
            )
            lines.extend([""] * blank_lines)
        line = ""
        last_disp = 0.0
        for bt_op in line_data:
            offset = int(bt_op.tx // char_width)
            spaces = (offset - len(line)) * (math.ceil(last_disp) < int(bt_op.tx))
            line = f"{line}{' ' * spaces}{bt_op.text}"
            last_disp = bt_op.displaced_tx
        if line.strip() or lines:
            lines.append("".join(c if ord(c) < 14 or ord(c) > 31 else " " for c in line))
        last_y_coord = y_coord
    return "\n".join(
        ln.rstrip() for ln in lines if config.PRESERVE_VERTICAL_WHITESPACE or ln.strip()
    )
