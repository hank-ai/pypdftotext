"""
Define PageFingerprint for PDF Ancestry Detection

This module provides functionality to detect which pages in a merged PDF
share a common ancestor document. When PDFs are merged using tools like pypdf,
pages from the same source retain structural fingerprints that can be used
to identify their common origin.

Key Indicators of Common Ancestry:
1. Sharing a /Resources object
2. Sharing objects that are referenced by /Resources objects
3. Mismatched use of /ExtGState resource dictionaries
4. Common rotations and mediabox dimensions

The algorithm creates a structural fingerprint for each page and groups
contiguous pages with matching fingerprints.
"""

from __future__ import annotations

from typing import Any

from pypdf import PageObject
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject


class PageFingerprint:
    """
    A fingerprint representing the structural characteristics of a PDF page.

    Pages from the same source document will typically have matching fingerprints
    because they share resources and have common page-level attributes.
    """

    def __init__(self, page: PageObject) -> None:
        resources_id: int = -1
        resource_child_ids: list[int] = []
        has_extgstate = False

        def _add_child_id(obj: Any):
            if isinstance(ind_ref := getattr(obj, "indirect_reference", None), IndirectObject):
                resource_child_ids.append(ind_ref.idnum)

        # Extract resource structure
        if "/Resources" in page:
            res = page["/Resources"]
            if isinstance(res, DictionaryObject):
                has_extgstate = "/ExtGState" in res
                if isinstance(ind_ref := getattr(res, "indirect_reference", None), IndirectObject):
                    resources_id = ind_ref.idnum
                for child in res.values():
                    _add_child_id(child)
                    if isinstance(child, DictionaryObject):
                        for sub_child in child.values():
                            _add_child_id(sub_child)
                    if isinstance(child, ArrayObject):
                        for sub_child in child:
                            _add_child_id(sub_child)

        self.resources_id: int = resources_id
        self.resource_child_ids: frozenset[int] = frozenset(resource_child_ids)
        self.has_rotate: bool = "/Rotate" in page
        self.has_extgstate: bool = has_extgstate
        self.mediabox: tuple[int, ...] = tuple(int(v) for v in page.mediabox or []) or (0,) * 4

    def shares_origin(self, value: PageFingerprint) -> bool:
        """Determine whether the two pages are likely to have shared a common ancestor PDF."""
        if not isinstance(value, PageFingerprint):
            return False
        if self.resources_id == value.resources_id and self.resources_id != -1:
            return True
        if (
            self.has_extgstate != value.has_extgstate
            or self.has_rotate != value.has_rotate
            or self.mediabox != value.mediabox
        ):
            return False

        # sum below equals 0 if neither have resource children,
        # 1 if self does and value does not, and 2 if both do.
        match sum((bool(self.resource_child_ids), bool(value.resource_child_ids))):
            case 0:
                # neither has resource children. inconclusive, so do nothing.
                return True
            case 1:
                # one has resource children. the other does not. no match. return False.
                return False
            case _:  # will always be 2, but treat as default case
                # both have resource children. if they share any resources, return True.
                return bool(self.resource_child_ids & value.resource_child_ids)
