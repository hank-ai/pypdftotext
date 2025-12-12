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
from dataclasses import dataclass
from pypdf import PageObject
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject


@dataclass(frozen=True)
class PageFingerprint:
    """
    A hashable fingerprint representing the structural characteristics of a PDF page.

    Pages from the same source document will typically have matching fingerprints
    because they share resources and have common page-level attributes.
    """

    resources_id: int  # the object id of the page's resources dictionary
    resource_child_ids: frozenset[int]  # object ids of resources members (fonts, xobjects)
    has_rotate: bool  # Whether page has /Rotate key
    has_extgstate: bool  # Whether page uses ExtGState
    mediabox: tuple[int, ...]  # the mediabox for the page

    def __eq__(self, value: object) -> bool:
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

    @classmethod
    def from_page(cls, page: PageObject) -> PageFingerprint:
        """
        Build a new PageFingerprint from the supplied page.
        """
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

        return cls(
            resources_id=resources_id,
            resource_child_ids=frozenset(resource_child_ids),
            has_rotate="/Rotate" in page,
            has_extgstate=has_extgstate,
            mediabox=tuple(int(v) for v in page.mediabox or []) or (0,) * 4,
        )
