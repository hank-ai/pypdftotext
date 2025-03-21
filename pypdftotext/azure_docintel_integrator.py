"""Microsoft Azure Document Intelligence API Handler"""

import io
import json
import os
from dataclasses import dataclass, field
import math
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient, AnalyzeDocumentLROPoller
from azure.ai.documentintelligence.models import AnalyzeResult
from azure.core.credentials import AzureKeyCredential
from pypdf._text_extraction._layout_mode import (
    fixed_char_width,
    fixed_width_page,
    y_coordinate_groups,
)
from pypdf._text_extraction._layout_mode._fixed_width_page import BTGroup

from . import constants


def _rotate_bbox(
    p: list[float], angle: float, width: float = 8.5, height: float = 11
) -> list[float]:
    if abs(angle) < 5:
        return p
    angle = math.radians(angle)
    _sin = math.sin(angle)
    _cos = math.cos(angle)

    def _rotate_point(x: float, y: float) -> tuple[float, float]:
        return (
            _cos * (x - width / 2) - _sin * (y - height / 2) + height / 2,
            _sin * (x - width / 2) + _cos * (y - height / 2) + width / 2,
        )

    return [
        *_rotate_point(p[0], p[1]),
        *_rotate_point(p[2], p[3]),
        *_rotate_point(p[4], p[5]),
        *_rotate_point(p[6], p[7]),
    ]


@dataclass
class AzureDocIntelIntegrator:
    """
    Extract text from pdf images via calls to Azure Document Intelligence OCR API.
    """

    timeout: int = 60
    preserve_vertical_whitespace: bool = False
    font_height_weight: float = 1.0
    client: DocumentIntelligenceClient | None = field(default=None, init=False, repr=False)
    last_result: AnalyzeResult = field(default_factory=lambda: AnalyzeResult({}), init=False)

    def create_client(self) -> bool:
        """
        Create an Azure DocumentIntelligenceClient based on current global
        constants and env var settings.

        The following may be set via env var prior to module import OR set via
        the corresponding constants.<ENV_VARIABLE_NAME> global constant after
        module import.

        Constants/Environment Variables:
            AZURE_DOCINTEL_ENDPOINT: Azure Document Intelligence Instance Endpoint URL.
            AZURE_DOCINTEL_SUBSCRIPTION_KEY: Azure Document Intelligence Subscription Key.

        Returns:
            bool: True if client was created successfully. False otherwise.
        """
        if (
            _endpoint := os.getenv("AZURE_DOCINTEL_ENDPOINT") or constants.AZURE_DOCINTEL_ENDPOINT
        ) and (
            _key := os.getenv("AZURE_DOCINTEL_SUBSCRIPTION_KEY")
            or constants.AZURE_DOCINTEL_SUBSCRIPTION_KEY
        ):
            self.client = DocumentIntelligenceClient(_endpoint, AzureKeyCredential(_key))
            constants.log(f"Azure OCR Client Created: {_endpoint=!r}")
            return True
        constants.log("Failed to create Azure OCR Client.")
        return False

    def ocr_pages(self, pdf: bytes, pages: list[int], debug_path: Path | None = None) -> list[str]:
        """
        Read the text from supplied pdf page indices.

        Args:
            pdf: bytes of a pdf file
            pages: list of pdf page indices to OCR

        Returns:
            list[str]: list of strings containing structured text extracted
                from each supplied page index.
        """
        if constants.AZURE_DOCINTEL_AUTO_CLIENT and self.client is None:
            self.create_client()
        if self.client is None:
            print("Azure OCR API not available. Did you create a client? Returning empty string.")
            return []
        assert self.client is not None
        pdf_io = io.BytesIO(pdf)
        pdf_io.seek(0)
        poller: AnalyzeDocumentLROPoller = self.client.begin_analyze_document(
            model_id="prebuilt-read",
            body=pdf_io,
            pages=",".join(str(pg + 1) for pg in pages),
        )
        self.last_result = poller.result(self.timeout)
        results: list[str] = []
        for idx, doc_page in enumerate(self.last_result.pages):
            if not doc_page.lines:
                results.append("")  # No text on this page. Add empty string and continue.
                continue
            bt_groups = [
                BTGroup(
                    tx=rotated_polygon[0] * 100,
                    ty=rotated_polygon[1] * 100,
                    font_size=(_fsz := (rotated_polygon[-1] - rotated_polygon[1]) * 100),
                    font_height=_fsz,
                    text=_line.content,
                    displaced_tx=rotated_polygon[2] * 100,
                    flip_sort=-1,
                )
                for _line in doc_page.lines
                if _line.polygon is not None
                and (
                    rotated_polygon := _rotate_bbox(
                        _line.polygon,
                        -(doc_page.angle or 0.0),
                        doc_page.width or 8.5,
                        doc_page.height or 11,
                    )
                )
            ]
            if not bt_groups:
                results.append("")
                continue
            min_x = min((x["tx"] for x in bt_groups), default=0.0)
            bt_groups = [
                BTGroup(  # type: ignore[misc] # mypy doesn't like typedict inheritance.
                    ogrp,
                    tx=ogrp["tx"] - min_x,
                    displaced_tx=ogrp["displaced_tx"] - min_x,
                )
                for ogrp in sorted(
                    bt_groups, key=lambda x: (x["ty"] * x["flip_sort"], -x["tx"]), reverse=True
                )
                if ogrp["text"]
            ]
            if debug_path:
                debug_path.joinpath(f"ocr_bts{pages[idx]}.json").write_text(
                    json.dumps(bt_groups, indent=2, default=str), "utf-8"
                )
            ty_groups = y_coordinate_groups(bt_groups)
            char_width = fixed_char_width(bt_groups)
            results.append(
                fixed_width_page(
                    ty_groups,
                    char_width,
                    self.preserve_vertical_whitespace,
                    self.font_height_weight,
                )
            )
        return results


AZURE_READ = AzureDocIntelIntegrator()
