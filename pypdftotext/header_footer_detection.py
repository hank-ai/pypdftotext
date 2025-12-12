"""Tools for detecting and removing header / footer data from extracted pdf text."""

import logging
from collections import defaultdict
from collections.abc import Sequence
from difflib import SequenceMatcher
from itertools import pairwise

import numpy as np

from ._config import PyPdfToTextConfig
from .extracted_page import ExtractedPage

logger = logging.getLogger(__name__)


def match_ratio(string_1: Sequence, string_2: Sequence, zero_if_empty=False) -> float:
    """Convenience function for calculating sequence similarity ratio.

    Args:
        string_1: First sequence to compare
        string_2: Second sequence to compare
        zero_if_empty: If True, return 0.0 when both sequences are empty

    Returns:
        float: Similarity ratio between 0.0 and 1.0
    """
    if zero_if_empty and not string_1 and not string_2:
        return 0.0
    return SequenceMatcher(None, string_1, string_2).ratio()


def trim_leading_spaces(lines: list[str]) -> list[str]:
    """
    Remove leading columns consisting only of spaces from a list of strings.

    This function processes a list of strings, converts them to a character array,
    and removes all leading columns that consist entirely of spaces. It returns
    the modified list of strings with leading spaces removed.

    Args:
        lines (list[str]): The input list of strings to process.

    Returns:
        list[str]: The list of strings with leading columns of spaces removed.

    Example:
        >>> trim_leading_spaces(["    abc", "  def", "    ghi"])
        ['  abc', 'def', '  ghi']
    """
    if not lines:
        return lines

    # Find minimum leading whitespace
    min_spaces = float("inf")
    for line in lines:
        stripped = line.lstrip()
        if stripped:  # Ignore blank lines
            min_spaces = min(min_spaces, len(line) - len(stripped))

    if min_spaces in (float("inf"), 0):
        return lines

    return [line[min_spaces:] for line in lines]


def header_footer_update(
    extracted_page: ExtractedPage,
    header_footer_lines: dict[int, str],
    config: PyPdfToTextConfig | None = None,
):
    """
    Move header and footer lines from an extracted page's text into its header/footer attributes.

    This function modifies the ExtractedPage in place, removing detected header and footer
    lines from the text attribute and populating the header and footer attributes.

    Args:
        extracted_page: An ExtractedPage instance to update.
        header_footer_lines: Dictionary of canonical header/footer lines.
            Positive integer keys indicate header line indices (0-based from top),
            negative keys indicate footer line indices (-1 is last line).
        config: Optional PyPdfToTextConfig instance for configuration settings.

    NOTE:
        This function modifies the extracted_page in place. The text, header,
        and footer attributes are updated directly.

    Example:
        >>> from pypdftotext.extracted_page import ExtractedPage
        >>> from pypdf import PageObject
        >>> # Create an ExtractedPage with header, content, and footer
        >>> page = ExtractedPage(
        ...     page_obj=PageObject.create_blank_page(width=612, height=792),
        ...     handwritten_ratio=0.0,
        ...     text="Company Report\\nQ3 2023\\n\\nRevenue increased by 15%\\nExpenses decreased by 5%\\n\\nPage 1 of 10\\nConfidential",
        ...     document_idx=0
        ... )
        >>> # Define header and footer lines to extract
        >>> header_footer_lines = {
        ...     0: "Company Report",  # First line is header
        ...     1: "Q3 2023",         # Second line is also header
        ...     -2: "Page 1 of 10",   # Second to last line is footer
        ...     -1: "Confidential"    # Last line is also footer
        ... }
        >>> # Apply header/footer extraction
        >>> header_footer_update(page, header_footer_lines)
        >>> # Check that headers were extracted
        >>> page.header
        'Company Report\\nQ3 2023'
        >>> # Check that footers were extracted
        >>> page.footer
        'Page 1 of 10\\nConfidential'
        >>> # Check that main text no longer contains headers/footers
        >>> page.text
        'Revenue increased by 15%\\nExpenses decreased by 5%'
    """
    config = config or PyPdfToTextConfig()
    page_lines: list[str] = [
        line for line in trim_leading_spaces(extracted_page.text.splitlines()) if line.strip()
    ]
    header_lines: list[str] = []
    footer_lines: list[str] = []
    # sort ensures that pops occur from most negative to -1 then most positive
    # to zero. ensures that, for example, idx 0 isn't popped before idx 1 since
    # after pop(0) the old idx 1 is the new idx 0.
    for line_idx, header_footer_line in sorted(
        header_footer_lines.items(), key=lambda x: (x[0] < 0, abs(x[0])), reverse=True
    ):
        # don't attempt pop of idx that doesn't exist (positive or negative).
        if len(page_lines) > (line_idx if line_idx >= 0 else (abs(line_idx) - 1)):
            if (
                match_ratio(
                    header_footer_line.replace(" ", ""), page_lines[line_idx].replace(" ", "")
                )
                > config.MIN_HEADER_FOOTER_LINE_MATCH_RATIO
            ):
                popped_line = page_lines.pop(line_idx)
                if line_idx >= 0:
                    header_lines.append(popped_line)
                else:
                    footer_lines.append(popped_line)
    extracted_page.text = "\n".join(
        line
        for line in trim_leading_spaces(page_lines)
        if config.RETAIN_CONTINUED_HEADINGS or "(continued)" not in line
    )
    # Reverse header_lines to maintain original order (popped from highest to lowest index)
    extracted_page.header = "\n".join(reversed(header_lines))
    # footer_lines are already in correct order (popped from most negative to -1)
    extracted_page.footer = "\n".join(footer_lines)


def header_footer_test_lines(
    extracted_pages: list[ExtractedPage],
    max_header_lines: int = 10,
    max_footer_lines: int = 10,
) -> dict[int, list[list[str]]]:
    """
    Collect lists of formatted lines of text for each page of each document.

    This function processes a dictionary of pages indexed by document and page
    numbers. It extracts the header and footer lines from each page, formats
    them, and returns a dictionary with document indices as keys and lists of
    formatted lines for each page as values.

    Args:
        extracted_pages (list[ExtractedPage]): A list of extracted pages from a
            PdfExtract instance.
        max_header_lines (int): Optional. Maximum number of header lines. Defaults to 10.
        max_footer_lines (int): Optional. Maximum number of footer lines. Defaults to 10.

    Returns:
        dict[int, list[list[str]]]: A dictionary with document indices as keys.
            The values are lists, one list per page in the source PDF. Each inner
            list contains the lines of text on each page, formatted to always have
            a length of max_header_lines + max_footer_lines by inserting empty
            string values between the header and footer lines.

    Example:
        >>> from pypdftotext.extracted_page import ExtractedPage
        >>> from pypdf import PageObject
        >>> # Create mock ExtractedPage instances
        >>> pages = [
        ...     ExtractedPage(
        ...         page_obj=PageObject.create_blank_page(width=612, height=792),
        ...         handwritten_ratio=0.0,
        ...         text="Header1\\nAn arbitrary non header line\\nPage 1 of 3",
        ...         document_idx=0
        ...     ),
        ...     ExtractedPage(
        ...         page_obj=PageObject.create_blank_page(width=612, height=792),
        ...         handwritten_ratio=0.0,
        ...         text="Header1\\nSome other\\ntext but\\nnot a footer\\nPage 2 of 3",
        ...         document_idx=0
        ...     ),
        ...     ExtractedPage(
        ...         page_obj=PageObject.create_blank_page(width=612, height=792),
        ...         handwritten_ratio=0.0,
        ...         text="Header1\\nPage 3 of 3",
        ...         document_idx=0
        ...     )
        ... ]
        >>> result = header_footer_test_lines(pages, max_header_lines=1, max_footer_lines=3)
        >>> result == {
        ...     0: [
        ...         ['Header1', 'Header1', 'An arbitrary non header line', 'Page 1 of 3'],
        ...         ['Header1', 'text but', 'not a footer', 'Page 2 of 3'],
        ...         ['Header1', '', 'Header1', 'Page 3 of 3']
        ...     ]
        ... }
        True
    """
    tests_by_docidx: dict[int, list[list[str]]] = defaultdict(list)
    for page in extracted_pages:
        split_lines = [line for line in page.text.strip().splitlines() if line.strip()]
        head = split_lines[:max_header_lines]
        waist: list[str] = []
        feet = split_lines[-max_footer_lines:] if max_footer_lines != 0 else []
        while len(head) + len(feet) + len(waist) < max_header_lines + max_footer_lines:
            waist += [""]
        head.extend(waist)
        head.extend(feet)  # we got a body here!
        tests_by_docidx[page.document_idx].append(head)
    return tests_by_docidx


def find_model_headers_and_footers(
    extracted_pages: list[ExtractedPage], config: PyPdfToTextConfig | None = None
):
    """
    Identifies lines that repeat at the top and bottom of all pages
    sharing a common document_idx and returns a reference containing canonical
    examples of each that can be used to identify and remove similar lines
    from the text of extracted pages.

    Args:
        extracted_pages (list[ExtractedPage]): A list of extracted pages from a
            PdfExtract instance.
        config: A PyPdfToTextConfig instance defining header footer extraction settings.

    Returns:
        dict[int, dict[int, str]]: A nested dictionary containing document
            indices as top-level keys and header/footer line indices as
            second-level keys. The values are the header/footer lines.
            Footer lines are keyed as negative integers.

    Example:
        >>> from pypdftotext.extracted_page import ExtractedPage
        >>> from pypdf import PageObject
        >>> # Create ExtractedPage instances with consistent headers and footers
        >>> pages = [
        ...     ExtractedPage(
        ...         page_obj=PageObject.create_blank_page(width=612, height=792),
        ...         handwritten_ratio=0.0,
        ...         text="Header1\\nAn arbitrary non header line\\nPage 1 of 3",
        ...         document_idx=0
        ...     ),
        ...     ExtractedPage(
        ...         page_obj=PageObject.create_blank_page(width=612, height=792),
        ...         handwritten_ratio=0.0,
        ...         text="Header1\\nSome other text that is not a footer\\nPage 2 of 3",
        ...         document_idx=0
        ...     ),
        ...     ExtractedPage(
        ...         page_obj=PageObject.create_blank_page(width=612, height=792),
        ...         handwritten_ratio=0.0,
        ...         text="Header1\\nThis one is neither a footer nor a header\\nPage 3 of 3",
        ...         document_idx=0
        ...     )
        ... ]
        >>> config = PyPdfToTextConfig(overrides={"MAX_HEADER_LINES": 1, "MAX_FOOTER_LINES": 1})
        >>> result = find_model_headers_and_footers(pages, config)
        >>> # The function will detect 'Header1' as a consistent header across all pages
        >>> 0 in result and 0 in result[0]  # Document index 0 has header at line 0
        True
        >>> result[0][0]  # The header text
        'Header1'
        >>> -1 in result[0]  # Check if there's a footer detected
        True
    """
    config = config or PyPdfToTextConfig()
    min_page_match_ratio = config.MIN_HEADER_FOOTER_PAGE_MATCH_RATIO
    max_header_lines = config.MAX_HEADER_LINES
    max_footer_lines = config.MAX_FOOTER_LINES
    header_footer_lines: dict[int, dict[int, str]] = {}
    if max_header_lines + max_footer_lines == 0:
        return header_footer_lines
    for doc_idx, line_lists in header_footer_test_lines(
        extracted_pages, max_header_lines, max_footer_lines
    ).items():
        # stack the first max_header_lines and last max_footer_lines into
        # an array, rotate it clockwise 90deg and then flip it vertically.
        # the resulting `lines_from_all_pages_by_index` is of form
        # [
        #   [page 1/line 1, page 2/line 1, ..., page n/line 1],
        #   [page 1/line 2, page 2/line 2, ..., page n/line 2],
        #       ...
        #   [page 1/line max_header_lines, page 2/line max_header_lines, ...],
        #   [page 1/line -max_footer_lines, page 2/line -max_footer_lines, ...],
        #       ...
        #   [page 1/line n-1, page 2/line n-1, ..., page n/line n-1],
        #   [page 1/line n, page 2/line n, ..., page n/line n],
        # ]
        lines_from_all_pages_aligned_by_index = np.flipud(np.rot90(np.stack(line_lists)))
        # perform a pairwise comparison of the lines at each line index across all
        # pages using match_ratio.
        # output is an array of floats of length max_header_lines + max_footer_lines
        # having values of sum(match_ratios)/(pairs compared).
        page_match_ratios = np.apply_along_axis(
            lambda line_for_each_page: sum(
                match_ratio(x, y, True) / (len(line_for_each_page) - 1)
                for x, y in pairwise(np.char.replace(line_for_each_page, " ", ""))
            ),
            1,
            lines_from_all_pages_aligned_by_index,
        )
        # collect header indices as positive ints, and...
        header_footer_idxs = [
            idx
            for idx, ratio in enumerate(page_match_ratios[:max_header_lines])
            if ratio > min_page_match_ratio
        ]
        # ...footer indices as negative ints.
        header_footer_idxs.extend(
            [
                idx
                for idx, ratio in zip(
                    range(-max_footer_lines, 0, 1), page_match_ratios[-max_footer_lines:]
                )
                if ratio > min_page_match_ratio
            ]
        )
        header_footer_lines[doc_idx] = {
            # perform pairwise comparison of lines at each selected header/
            # footer index and return the first item in the pair with the
            # highest match_ratio to guarantee the returned value was in
            # the set of matches (up to 40% of the lines may not be in our
            # matched set and slight variation is allowed for matched entries,
            # so "most_freq_element" isn't sufficient).
            idx: str(
                sorted(
                    pairwise(lines_from_all_pages_aligned_by_index[idx]),
                    key=lambda x_y: match_ratio(
                        x_y[0].replace(" ", ""), x_y[1].replace(" ", ""), True
                    ),
                )[-1][0]
            )
            for idx in header_footer_idxs
        }
        logger.debug(
            "\n*******************************************\n"
            f"     Header/Footer Tests for Doc Idx {doc_idx}\n"
            "*******************************************\n"
            f"{page_match_ratios=}\n"
            f"{header_footer_idxs=}\n"
            f"{header_footer_lines[doc_idx]=}\n"
        )
    return header_footer_lines


def assign_headers_and_footers(
    extracted_pages: list[ExtractedPage], config: PyPdfToTextConfig | None = None
):
    """
    Detect model header/footer lines according to config and update the text,
    header, and footer attributes of each extracted page accordingly.

    This function:
    1. Identifies common header and footer patterns across pages with the same document_idx
    2. Removes detected headers/footers from the text attribute of each page
    3. Populates the header and footer attributes with the removed content

    Args:
        extracted_pages: A list of ExtractedPage instances from a PdfExtract instance.
        config: Optional PyPdfToTextConfig instance defining header/footer extraction settings.
            If None, uses default configuration.

    Note:
        Modifies the extracted_pages in place. After calling this function, each page's
        text, header, and footer attributes will be updated based on the detected patterns.
    """
    config = config or PyPdfToTextConfig()
    model_headers_footers = find_model_headers_and_footers(extracted_pages, config)
    for page in extracted_pages:
        if page.document_idx not in model_headers_footers:
            continue
        header_footer_update(page, model_headers_footers[page.document_idx], config)
