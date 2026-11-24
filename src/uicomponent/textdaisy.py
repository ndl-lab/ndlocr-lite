from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote
from uuid import uuid4
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from PIL import Image


@dataclass(frozen=True)
class TextDaisyExportResult:
    """生成したテキストDAISYファイル一式の保存先。"""

    directory: str
    zip_path: Optional[str]
    page_count: int


class TextDaisyExporter:
    """NDLOCR-Lite の OCR 結果から DAISY 3 textNCX ファイルセットを生成する。

    変換方針:
    - 本文行の字下げを TEXTBLOCK と LINE の座標差から検出し、段落ごとの <p> に分割する。
    - <LINE TYPE="タイトル本文"> が出た位置で新しいセクションを開始する。
    - セクションごとに ptk00001.xml / ptk00001.smil、ptk00002.xml / ptk00002.smil ... を出力する。
    - 各セクション内の <sent id="t0000001"> と <par id="phr00001"> は、そのファイルごとに 1 から採番する。
    - 段落内の OCR 行を結合して句読点ごとの <sent> とし、対応する SMIL の <par id="phrxxxxx"> と相互参照する。
    - NDLOCR-Lite XML の <BLOCK TYPE="図版"> は元画像から切り出し、JPEG と DTBook の <imggroup>/<img> として出力する。

    pages は main.py の alljsonobjlist と同じ形式を基本としつつ、XML 構造を使う場合は
    各 page dict に次のいずれかのキーで NDLOCR-Lite XML 文字列または XML パスを渡す。
    - "xml", "xmlstr", "ocr_xml", "ndl_xml", "page_xml"
    - "xml_path", "ocr_xml_path", "ndl_xml_path"

    XML が渡されない場合は従来どおり JSON の OCR 行を 1 行 1 段落としてフォールバックする。
    """

    # 代表ファイル名。実際の本文/SMILはセクションごとに ptk000NN.xml / ptk000NN.smil を生成する。
    DTBOOK_FILE = "ptk00001.xml"
    NCX_FILE = "toc.ncx"
    SMIL_FILE = "ptk00001.smil"
    OPF_FILE = "book.opf"

    TITLE_LINE_TYPE = "タイトル本文"
    FIGURE_BLOCK_TYPE = "図版"
    TABLE_BLOCK_TYPE = "表組"
    FIGURE_IMAGE_DIR = "image"
    FIGURE_DPI = 300
    FIGURE_MAX_WIDTH_PX = 550
    FIGURE_MAX_HEIGHT_PX = 400
    FIGURE_JPEG_QUALITY = 95
    PHRASE_DELIMITERS = frozenset("、。，．")
    PUNCTUATION_CLOSERS = frozenset("」』)）］】〉》〕〗〙〛”’")
    INDENT_THRESHOLD_EM = 0.5

    def __init__(
        self,
        *,
        title: str,
        language: str = "ja",
        producer: str = "NDLOCR-Lite",
        identifier: Optional[str] = "urn:uuid:ad57085e-ba0e-4145-a8e7-ffe6d5d13a58",
        generator: str = "PLEXTALK DAISY Producer",
    ) -> None:
        self.title = title.strip() or "NDLOCR-Lite OCR Result"
        self.language = language or "ja"
        self.producer = producer
        self.generator = generator
        self.identifier = identifier or f"urn:uuid:{uuid4()}"
        self.produced_date = date.today().isoformat()
        self.asset_stem = self._safe_identifier_for_asset_name(self.identifier)
        self.CSS_FILE = f"{self.asset_stem}.css"
        self.XSL_FILE = f"{self.asset_stem}.xsl"

    def export(
        self,
        pages: List[Dict[str, Any]],
        output_dir: str,
        *,
        folder_name: Optional[str] = None,
        make_zip: bool = True,
    ) -> TextDaisyExportResult:
        """DAISY 3 text-only のファイルセットを出力する。"""
        if not pages:
            raise ValueError("Text DAISY export requires at least one OCR result page.")

        normalized_pages = self._normalize_pages(pages)
        sections = self._build_sections(normalized_pages)
        self._ensure_sections_have_single_heading(sections)
        self._ensure_sections_have_body(sections)
        phrases = self._assign_phrase_ids(sections, normalized_pages)
        safe_folder = self._safe_filename(folder_name or f"{self.title}_textdaisy")
        book_dir = Path(output_dir) / safe_folder

        if book_dir.exists():
            shutil.rmtree(book_dir)

        book_dir.mkdir(parents=True, exist_ok=True)
        self._write_figure_images(sections, normalized_pages, book_dir)

        for section in sections:
            (book_dir / section["dtbook_file"]).write_text(
                self._build_dtbook(section), encoding="utf-8"
            )
            (book_dir / section["smil_file"]).write_text(
                self._build_smil(section), encoding="utf-8"
            )

        (book_dir / self.NCX_FILE).write_text(
            self._build_ncx(normalized_pages, sections, phrases), encoding="utf-8"
        )
        (book_dir / self.OPF_FILE).write_text(
            self._build_opf(sections), encoding="utf-8"
        )
        (book_dir / self.CSS_FILE).write_text(
            self._build_css(), encoding="utf-8"
        )
        (book_dir / self.XSL_FILE).write_text(
            self._build_xsl(), encoding="utf-8"
        )

        zip_path: Optional[str] = None
        if make_zip:
            archive_base = str(book_dir)
            zip_path = shutil.make_archive(archive_base, "zip", root_dir=book_dir)

        return TextDaisyExportResult(
            directory=str(book_dir),
            zip_path=zip_path,
            page_count=len(normalized_pages),
        )

    def _normalize_pages(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []

        for fallback_page_index, page in enumerate(pages, start=1):
            xml_source = self._get_xml_source(page)
            if xml_source:
                xml_pages = self._extract_pages_from_ndl_xml(xml_source)
                if xml_pages:
                    source_info = self._source_image_info(page)
                    for source_offset, xml_page in enumerate(xml_pages):
                        xml_page.update(source_info)
                        if source_info.get("source_page_index") is not None:
                            xml_page["source_page_index"] = (
                                int(source_info["source_page_index"]) + source_offset
                            )
                    normalized.extend(xml_pages)
                    continue

            normalized.append(self._normalize_page_from_json(page, fallback_page_index))

        # XML を複数ページ含む形で受け取った場合にも、全体として 1 始まりのページ番号へ振り直す。
        for page_no, page in enumerate(normalized, start=1):
            page["page_no"] = page_no
            for block in page.get("blocks", []):
                block["page_no"] = page_no

        return normalized

    def _normalize_page_from_json(
        self,
        page: Dict[str, Any],
        page_index: int,
    ) -> Dict[str, Any]:
        imginfo = page.get("imginfo", {}) or {}
        label = imginfo.get("img_name") or f"page-{page_index}"
        line_objects = self._extract_json_line_objects(page)

        if not line_objects:
            line_objects = [{"text": " ", "line_type": "本文"}]

        blocks = self._blocks_from_json_lines(line_objects)
        for block in blocks:
            block["page_no"] = page_index

        return {
            "page_no": page_index,
            "label": label,
            **self._source_image_info(page),
            "blocks": blocks,
        }

    def _source_image_info(self, page: Dict[str, Any]) -> Dict[str, Any]:
        imginfo = page.get("imginfo", {}) or {}
        image_path = (
            imginfo.get("img_path")
            or imginfo.get("image_path")
            or page.get("img_path")
            or page.get("image_path")
        )
        source_page_index = imginfo.get("source_page_index")
        source_dpi = imginfo.get("source_dpi")
        return {
            "image_path": str(image_path) if image_path else None,
            "source_page_index": source_page_index,
            "source_dpi": source_dpi,
        }

    def _extract_json_line_objects(self, page: Dict[str, Any]) -> List[Dict[str, Any]]:
        contents = page.get("contents") or []
        if not contents:
            return []

        line_objects: Iterable[Dict[str, Any]] = contents[0] or []
        sorted_lines = sorted(
            line_objects,
            key=lambda obj: int(obj.get("id", 0))
            if str(obj.get("id", "0")).isdigit()
            else 0,
        )

        lines: List[Dict[str, Any]] = []
        for obj in sorted_lines:
            text = str(obj.get("text", "")).strip()
            if not text:
                continue
            lines.append(
                {
                    "text": text,
                    "line_type": self._get_json_line_type(obj),
                    "block_key": self._get_json_block_key(obj),
                    "bbox": self._bbox_from_json_line(obj),
                    "is_vertical": self._json_line_is_vertical(obj),
                }
            )

        return lines

    def _get_json_line_type(self, obj: Dict[str, Any]) -> str:
        for key in ("line_type", "lineType", "type", "TYPE", "ndl_type", "category"):
            value = obj.get(key)
            if value:
                return str(value)
        return "本文"

    def _get_json_block_key(self, obj: Dict[str, Any]) -> Optional[str]:
        for key in (
            "textblock_id",
            "textBlockId",
            "block_id",
            "blockId",
            "block_index",
            "blockIndex",
            "TEXTBLOCK_ID",
        ):
            value = obj.get(key)
            if value is not None:
                return str(value)
        return None

    def _blocks_from_json_lines(
        self,
        line_objects: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        # block_key が入っている場合は同じ TEXTBLOCK に属する行をまとめる。
        # 入っていない既存 JSON では構造が分からないため、1 行 1 ブロックへフォールバックする。
        blocks: List[Dict[str, Any]] = []
        current_key: Optional[str] = None
        current_lines: List[Dict[str, Any]] = []

        def flush() -> None:
            nonlocal current_lines
            if current_lines:
                blocks.extend(
                    self._split_lines_to_dtbook_blocks(
                        current_lines,
                        self._bbox_from_lines(current_lines),
                    )
                )
                current_lines = []

        for line in line_objects:
            block_key = line.get("block_key")
            if block_key is None:
                flush()
                blocks.extend(self._split_lines_to_dtbook_blocks([line]))
                current_key = None
                continue

            if current_key is None:
                current_key = block_key
            elif current_key != block_key:
                flush()
                current_key = block_key

            current_lines.append(line)

        flush()
        return blocks or [self._make_dtbook_block("p", [{"text": " ", "line_type": "本文"}])]

    def _get_xml_source(self, page: Dict[str, Any]) -> Optional[str]:
        for key in ("xml", "xmlstr", "ocr_xml", "ndl_xml", "page_xml"):
            value = page.get(key)
            if value:
                return str(value)

        for key in ("xml_path", "ocr_xml_path", "ndl_xml_path"):
            value = page.get(key)
            if not value:
                continue
            path = Path(str(value))
            if path.exists():
                return path.read_text(encoding="utf-8")

        return None

    def _extract_pages_from_ndl_xml(self, xml_source: str) -> List[Dict[str, Any]]:
        try:
            root = ET.fromstring(xml_source)
        except ET.ParseError:
            return []

        root_tag = self._local_name(root.tag)
        if root_tag == "PAGE":
            page_elements = [root]
        else:
            page_elements = [elem for elem in root.iter() if self._local_name(elem.tag) == "PAGE"]

        normalized_pages: List[Dict[str, Any]] = []
        for index, page_elem in enumerate(page_elements, start=1):
            label = page_elem.attrib.get("IMAGENAME") or f"page-{index}"
            blocks = self._extract_dtbook_blocks_from_page_xml(page_elem)
            if not blocks:
                blocks = [self._make_dtbook_block("p", [{"text": " ", "line_type": "本文"}])]
            for block in blocks:
                block["page_no"] = index
            normalized_pages.append(
                {
                    "page_no": index,
                    "label": label,
                    "blocks": blocks,
                }
            )

        return normalized_pages

    def _extract_dtbook_blocks_from_page_xml(self, page_elem: ET.Element) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []

        for elem in list(page_elem):
            tag = self._local_name(elem.tag)

            if tag == "TEXTBLOCK":
                blocks.extend(self._blocks_from_textblock_elem(elem))
            elif tag == "LINE":
                blocks.extend(self._split_lines_to_dtbook_blocks([self._line_from_xml(elem)]))
            elif tag == "BLOCK":
                blocks.extend(self._blocks_from_block_elem(elem))

        return blocks

    def _blocks_from_block_elem(self, block_elem: ET.Element) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        pending_lines: List[Dict[str, Any]] = []
        block_type = str(block_elem.attrib.get("TYPE", "")).strip()

        if self._is_table_block_type(block_type):
            return self._blocks_from_table_elem(block_elem)

        def flush_pending_lines() -> None:
            nonlocal pending_lines
            if pending_lines:
                blocks.extend(self._split_lines_to_dtbook_blocks(pending_lines))
                pending_lines = []

        if self._is_figure_block_type(block_type):
            blocks.append(self._make_figure_block(block_elem))

        for elem in list(block_elem):
            tag = self._local_name(elem.tag)
            if tag == "TEXTBLOCK":
                flush_pending_lines()
                blocks.extend(self._blocks_from_textblock_elem(elem))
            elif tag == "LINE":
                pending_lines.append(self._line_from_xml(elem))
            elif tag == "BLOCK":
                flush_pending_lines()
                blocks.extend(self._blocks_from_block_elem(elem))

        flush_pending_lines()
        return blocks

    def _blocks_from_table_elem(self, table_elem: ET.Element) -> List[Dict[str, Any]]:
        cells = []
        for original_index, elem in enumerate(table_elem.iter()):
            if self._local_name(elem.tag) != "LINE":
                continue
            line = self._line_from_xml(elem)
            if line["text"]:
                cells.append({**line, "original_index": original_index})

        if not cells:
            return []

        rows = [cells]
        if all(cell.get("bbox") is not None for cell in cells):
            heights = [
                cell["bbox"][3] - cell["bbox"][1]
                for cell in cells
            ]
            row_tolerance = max(1.0, float(median(heights)) * 0.6)
            rows = []
            for cell in sorted(
                cells,
                key=lambda item: (
                    (item["bbox"][1] + item["bbox"][3]) / 2,
                    item["bbox"][0],
                    item["original_index"],
                ),
            ):
                center_y = (cell["bbox"][1] + cell["bbox"][3]) / 2
                if not rows or abs(center_y - rows[-1]["center_y"]) > row_tolerance:
                    rows.append({"center_y": center_y, "cells": [cell]})
                    continue

                row = rows[-1]
                row["cells"].append(cell)
                row["center_y"] = sum(
                    (item["bbox"][1] + item["bbox"][3]) / 2
                    for item in row["cells"]
                ) / len(row["cells"])

            rows = [
                sorted(
                    row["cells"],
                    key=lambda item: (item["bbox"][0], item["original_index"]),
                )
                for row in rows
            ]

        return [
            self._make_dtbook_block(
                "p",
                [{
                    "text": " ".join(cell["text"] for cell in row),
                    "line_type": "本文",
                }],
            )
            for row in rows
        ]

    def _blocks_from_textblock_elem(self, textblock_elem: ET.Element) -> List[Dict[str, Any]]:
        lines: List[Dict[str, Any]] = []
        for elem in list(textblock_elem):
            if self._local_name(elem.tag) != "LINE":
                continue
            line = self._line_from_xml(elem)
            if line["text"]:
                lines.append(line)

        return self._split_lines_to_dtbook_blocks(
            lines,
            self._element_bbox(textblock_elem),
        )

    def _line_from_xml(self, line_elem: ET.Element) -> Dict[str, Any]:
        text = (line_elem.attrib.get("STRING") or line_elem.attrib.get("TEXT") or "").strip()
        if not text:
            text = "".join(
                char.attrib.get("MOJI", "")
                for char in line_elem.iter()
                if self._local_name(char.tag) == "CHAR"
            ).strip()

        return {
            "text": text,
            "line_type": line_elem.attrib.get("TYPE", "本文"),
            "bbox": self._element_bbox(line_elem),
            "is_vertical": self._xml_line_is_vertical(line_elem),
        }

    def _split_lines_to_dtbook_blocks(
        self,
        lines: Sequence[Dict[str, Any]],
        textblock_bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        current_tag: Optional[str] = None
        current_lines: List[Dict[str, Any]] = []
        current_text_indent = False
        is_vertical = self._lines_are_vertical(lines)

        def tag_for_line(line: Dict[str, Any]) -> str:
            return "h1" if self._is_title_line(line) else "p"

        def flush() -> None:
            nonlocal current_lines, current_tag, current_text_indent
            if current_lines and current_tag:
                blocks.append(
                    self._make_dtbook_block(
                        current_tag,
                        current_lines,
                        text_indent=current_text_indent,
                    )
                )
            current_tag = None
            current_lines = []
            current_text_indent = False

        for line in lines:
            text = str(line.get("text", "")).strip()
            if not text:
                continue

            tag = tag_for_line(line)
            starts_indented_paragraph = (
                tag == "p"
                and self._is_body_line(line)
                and self._line_starts_indented(line, textblock_bbox, is_vertical)
            )
            if current_tag is None:
                current_tag = tag
                current_text_indent = starts_indented_paragraph
            elif current_tag != tag or starts_indented_paragraph:
                # TEXTBLOCK の途中に「タイトル本文」がある場合も、そこで段落/見出しを切る。
                flush()
                current_tag = tag
                current_text_indent = starts_indented_paragraph

            current_lines.append(
                {
                    "text": text,
                    "line_type": line.get("line_type", "本文"),
                    "generated": bool(line.get("generated")),
                }
            )

        flush()
        return blocks

    def _make_dtbook_block(
        self,
        tag: str,
        lines: Sequence[Dict[str, Any]],
        *,
        generated: bool = False,
        text_indent: bool = False,
    ) -> Dict[str, Any]:
        return {
            "tag": tag,
            "is_heading": tag.startswith("h"),
            "generated": generated,
            "text_indent": text_indent,
            "lines": [dict(line) for line in lines],
        }

    def _line_starts_indented(
        self,
        line: Dict[str, Any],
        textblock_bbox: Optional[Tuple[float, float, float, float]],
        is_vertical: bool,
    ) -> bool:
        line_bbox = line.get("bbox")
        if textblock_bbox is None or line_bbox is None:
            return False

        block_start = textblock_bbox[1] if is_vertical else textblock_bbox[0]
        line_start = line_bbox[1] if is_vertical else line_bbox[0]
        em_size = (
            line_bbox[2] - line_bbox[0]
            if is_vertical
            else line_bbox[3] - line_bbox[1]
        )
        return em_size > 0 and line_start - block_start >= em_size * self.INDENT_THRESHOLD_EM

    def _lines_are_vertical(self, lines: Sequence[Dict[str, Any]]) -> bool:
        body_lines = [line for line in lines if self._is_body_line(line)]
        direction_lines = body_lines or [
            line for line in lines if not self._is_title_line(line)
        ]
        directions = [
            bool(line["is_vertical"])
            for line in direction_lines
            if line.get("is_vertical") is not None
        ]
        if not directions:
            return False
        return sum(directions) * 2 > len(directions)

    def _is_body_line(self, line: Dict[str, Any]) -> bool:
        line_type = str(line.get("line_type", "")).strip().lower()
        return line_type in {"本文", "line_main", "main", "body"}

    def _xml_line_is_vertical(self, line_elem: ET.Element) -> Optional[bool]:
        direction = str(line_elem.attrib.get("DIRECTION", "")).strip().lower()
        if direction:
            if direction in {"縦", "vertical", "v"}:
                return True
            if direction in {"横", "horizontal", "h"}:
                return False

        bbox = self._element_bbox(line_elem)
        if bbox is None:
            return None
        return bbox[3] - bbox[1] > bbox[2] - bbox[0]

    def _json_line_is_vertical(self, obj: Dict[str, Any]) -> Optional[bool]:
        value = obj.get("isVertical")
        if isinstance(value, bool):
            return value
        if value is not None:
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "縦", "vertical", "v"}:
                return True
            if normalized in {"false", "0", "横", "horizontal", "h"}:
                return False

        bbox = self._bbox_from_json_line(obj)
        if bbox is None:
            return None
        return bbox[3] - bbox[1] > bbox[2] - bbox[0]

    def _bbox_from_json_line(
        self,
        obj: Dict[str, Any],
    ) -> Optional[Tuple[float, float, float, float]]:
        points = obj.get("boundingBox") or obj.get("bounding_box")
        if not isinstance(points, (list, tuple)):
            return None

        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            return None
        if not xs or not ys:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def _element_bbox(
        self,
        elem: ET.Element,
    ) -> Optional[Tuple[float, float, float, float]]:
        try:
            x = float(elem.attrib["X"])
            y = float(elem.attrib["Y"])
            width = float(elem.attrib["WIDTH"])
            height = float(elem.attrib["HEIGHT"])
            if width > 0 and height > 0:
                return x, y, x + width, y + height
        except (KeyError, TypeError, ValueError):
            pass

        polygon = next(
            (
                child
                for child in elem.iter()
                if self._local_name(child.tag) == "POLYGON"
            ),
            None,
        )
        if polygon is not None:
            raw_points = str(polygon.attrib.get("POINTS", "")).strip()
            try:
                coordinates = [
                    float(value)
                    for value in re.split(r"[\s,]+", raw_points)
                    if value
                ]
            except ValueError:
                coordinates = []
            points = list(zip(coordinates[0::2], coordinates[1::2]))
            if points:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                return min(xs), min(ys), max(xs), max(ys)

        descendant_lines = [
            self._element_bbox(child)
            for child in elem.iter()
            if child is not elem and self._local_name(child.tag) == "LINE"
        ]
        return self._merge_bboxes([bbox for bbox in descendant_lines if bbox is not None])

    def _bbox_from_lines(
        self,
        lines: Sequence[Dict[str, Any]],
    ) -> Optional[Tuple[float, float, float, float]]:
        return self._merge_bboxes(
            [line["bbox"] for line in lines if line.get("bbox") is not None]
        )

    def _merge_bboxes(
        self,
        bboxes: Sequence[Tuple[float, float, float, float]],
    ) -> Optional[Tuple[float, float, float, float]]:
        if not bboxes:
            return None
        return (
            min(bbox[0] for bbox in bboxes),
            min(bbox[1] for bbox in bboxes),
            max(bbox[2] for bbox in bboxes),
            max(bbox[3] for bbox in bboxes),
        )

    def _make_generated_body_block(self) -> Dict[str, Any]:
        # DTBook 2005-3 では level1 が h1 だけで終わると不完全になるため、
        # タイトルだけのセクションに最小限の本文要素を補う。
        return self._make_dtbook_block(
            "p",
            [{"text": " ", "line_type": "本文", "generated": True}],
            generated=True,
        )

    def _make_generated_heading_block(self, text: Optional[str] = None) -> Dict[str, Any]:
        # DTBook 2005-3 の level1 直下には、先頭要素として見出しを置く。
        # 元XMLにタイトル本文が無いセクションでは、書名を補助見出しとして使う。
        heading_text = (text or self.title or " ").strip() or " "
        return self._make_dtbook_block(
            "h1",
            [{"text": heading_text, "line_type": self.TITLE_LINE_TYPE, "generated": True}],
            generated=True,
        )

    def _is_figure_block_type(self, block_type: str) -> bool:
        normalized = block_type.strip().lower()
        return block_type.strip() == self.FIGURE_BLOCK_TYPE or normalized in {
            "block_fig",
            "figure",
            "fig",
            "image",
        }

    def _is_table_block_type(self, block_type: str) -> bool:
        normalized = block_type.strip().lower()
        return block_type.strip() == self.TABLE_BLOCK_TYPE or normalized in {
            "block_table",
            "table",
        }

    def _make_figure_block(self, block_elem: ET.Element) -> Dict[str, Any]:
        figure_label = self._figure_label(block_elem)
        return {
            "tag": "imggroup",
            "is_heading": False,
            "is_figure": True,
            "generated": False,
            "figure": {
                "alt": figure_label,
                "src": "",
                "source_type": block_elem.attrib.get("TYPE", self.FIGURE_BLOCK_TYPE),
                "x": block_elem.attrib.get("X", ""),
                "y": block_elem.attrib.get("Y", ""),
                "width": block_elem.attrib.get("WIDTH", ""),
                "height": block_elem.attrib.get("HEIGHT", ""),
                "conf": block_elem.attrib.get("CONF", ""),
            },
            "lines": [],
        }

    def _figure_label(self, block_elem: ET.Element) -> str:
        for key in ("ALT", "alt", "TITLE", "title", "LABEL", "label"):
            value = block_elem.attrib.get(key)
            if value and str(value).strip():
                label = str(value).strip()
                if label != self.FIGURE_BLOCK_TYPE:
                    return label
        return ""

    def _write_figure_images(
        self,
        sections: List[Dict[str, Any]],
        pages: List[Dict[str, Any]],
        book_dir: Path,
    ) -> None:
        figures_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for block in self._iter_figure_blocks(sections):
            page_no = int(block.get("page_no", 0))
            figures_by_page.setdefault(page_no, []).append(block)

        if not figures_by_page:
            return

        pages_by_no = {
            int(page.get("page_no", index)): page
            for index, page in enumerate(pages, start=1)
        }
        image_dir = book_dir / self.FIGURE_IMAGE_DIR
        image_dir.mkdir(parents=True, exist_ok=True)

        for page_no, figure_blocks in figures_by_page.items():
            page = pages_by_no.get(page_no)
            if page is None:
                raise ValueError(f"Source page {page_no} for a DAISY figure was not found.")

            source_image = self._load_source_page_image(page)
            try:
                coordinate_scale = self._source_coordinate_scale(page)
                for figure_index, block in enumerate(figure_blocks, start=1):
                    figure = block.get("figure", {}) or {}
                    crop_box = self._figure_crop_box(
                        figure,
                        source_image.width,
                        source_image.height,
                        page_no,
                        coordinate_scale,
                    )
                    figure_image = source_image.crop(crop_box)
                    figure_image.thumbnail(
                        (self.FIGURE_MAX_WIDTH_PX, self.FIGURE_MAX_HEIGHT_PX),
                        Image.Resampling.LANCZOS,
                    )

                    filename = f"{page_no}-{figure_index}.jpg"
                    relative_path = f"{self.FIGURE_IMAGE_DIR}/{filename}"
                    figure_image.save(
                        image_dir / filename,
                        format="JPEG",
                        quality=self.FIGURE_JPEG_QUALITY,
                        dpi=(self.FIGURE_DPI, self.FIGURE_DPI),
                    )
                    figure["src"] = relative_path
                    figure["pixel_width"] = figure_image.width
                    figure["pixel_height"] = figure_image.height
                    block["figure"] = figure
            finally:
                source_image.close()

    def _load_source_page_image(self, page: Dict[str, Any]) -> Image.Image:
        image_path_value = page.get("image_path")
        page_no = int(page.get("page_no", 0))
        if not image_path_value:
            raise ValueError(
                f"Source image path is required to export figures on page {page_no}."
            )

        image_path = Path(str(image_path_value))
        if not image_path.is_file():
            raise FileNotFoundError(
                f"Source image for DAISY figures was not found: {image_path}"
            )

        if image_path.suffix.lower() != ".pdf":
            with Image.open(image_path) as source_image:
                return source_image.convert("RGB")

        source_page_index = page.get("source_page_index")
        if source_page_index is None:
            raise ValueError(
                f"PDF page index is required to export figures on page {page_no}."
            )

        try:
            import pypdfium2
        except ImportError as exc:
            raise RuntimeError(
                "pypdfium2 is required to export DAISY figures from PDF input."
            ) from exc

        pdf_document = pypdfium2.PdfDocument(str(image_path))
        try:
            rendered_pages = pdf_document.render(
                pypdfium2.PdfBitmap.to_pil,
                page_indices=[int(source_page_index)],
                scale=self.FIGURE_DPI / 72.0,
            )
            return next(iter(rendered_pages)).convert("RGB")
        finally:
            pdf_document.close()

    def _source_coordinate_scale(self, page: Dict[str, Any]) -> float:
        image_path_value = page.get("image_path")
        if not image_path_value or Path(str(image_path_value)).suffix.lower() != ".pdf":
            return 1.0
        source_dpi = float(page.get("source_dpi") or self.FIGURE_DPI)
        if source_dpi <= 0:
            raise ValueError("Source PDF DPI must be greater than zero.")
        return self.FIGURE_DPI / source_dpi

    def _figure_crop_box(
        self,
        figure: Dict[str, Any],
        image_width: int,
        image_height: int,
        page_no: int,
        coordinate_scale: float = 1.0,
    ) -> Tuple[int, int, int, int]:
        try:
            x = float(figure["x"])
            y = float(figure["y"])
            width = float(figure["width"])
            height = float(figure["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Figure coordinates on page {page_no} are incomplete."
            ) from exc

        left = max(0, math.floor(x * coordinate_scale))
        top = max(0, math.floor(y * coordinate_scale))
        right = min(image_width, math.ceil((x + width) * coordinate_scale))
        bottom = min(image_height, math.ceil((y + height) * coordinate_scale))
        if width <= 0 or height <= 0 or right <= left or bottom <= top:
            raise ValueError(
                f"Figure coordinates on page {page_no} are outside the source image."
            )
        return left, top, right, bottom

    def _iter_figure_blocks(
        self,
        sections: List[Dict[str, Any]],
    ) -> Iterable[Dict[str, Any]]:
        return (
            block
            for section in sections
            for block in section.get("blocks", [])
            if block.get("is_figure")
        )

    def _is_title_line(self, line: Dict[str, Any]) -> bool:
        line_type = str(line.get("line_type", "")).strip()
        return (
            line_type == self.TITLE_LINE_TYPE
            or line_type == "line_title"
            or "タイトル本文" in line_type
        )

    def _dtbook_filename(self, section_index: int) -> str:
        return f"ptk{section_index:05d}.xml"

    def _smil_filename(self, section_index: int) -> str:
        return f"ptk{section_index:05d}.smil"

    def _build_sections(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """タイトル本文でセクションを切り、各セクションを別ファイル単位にする。"""
        sections: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        def new_section() -> Dict[str, Any]:
            section_index = len(sections) + 1
            return {
                "index": section_index,
                "dtbook_file": self._dtbook_filename(section_index),
                "smil_file": self._smil_filename(section_index),
                "blocks": [],
                "page_nos": [],
                "phrases": [],
                "heading_blocks": [],
                "first_phrase": None,
            }

        def append_current() -> None:
            nonlocal current
            if current is not None and current.get("blocks"):
                sections.append(current)
            current = None

        for page in pages:
            page_no = int(page.get("page_no", len(sections) + 1))
            for block in page.get("blocks", []):
                copied_block = self._copy_block(block)
                copied_block["page_no"] = page_no

                if self._block_is_heading(copied_block):
                    # タイトル本文が出たら、そこで前セクションを閉じて新ファイルを開始する。
                    append_current()
                    current = new_section()
                elif current is None:
                    current = new_section()

                current["blocks"].append(copied_block)
                if page_no not in current["page_nos"]:
                    current["page_nos"].append(page_no)

        append_current()

        if not sections:
            section = new_section()
            section["blocks"].append(self._make_generated_body_block())
            section["page_nos"].append(1)
            sections.append(section)

        # append_current() 後に index がずれることは通常ないが、念のためファイル名を正規化する。
        for index, section in enumerate(sections, start=1):
            section["index"] = index
            section["dtbook_file"] = self._dtbook_filename(index)
            section["smil_file"] = self._smil_filename(index)

        return sections

    def _copy_block(self, block: Dict[str, Any]) -> Dict[str, Any]:
        copied = dict(block)
        copied["lines"] = [dict(line) for line in block.get("lines", [])]
        if block.get("figure") is not None:
            copied["figure"] = dict(block["figure"])
        copied.pop("phrases", None)
        copied.pop("id", None)
        return copied

    def _ensure_sections_have_single_heading(
        self,
        sections: List[Dict[str, Any]],
    ) -> None:
        """各セクションの level1 直下を、ちょうど1つの h1 から始める。

        TEXT DAISY 3 / DTBook 2005-3 では、level1 の構造を安定させるため、
        セクション冒頭に見出しを置く。元の NDLOCR-Lite XML に
        TYPE="タイトル本文" が無い場合は書名を補助見出しとして追加し、
        複数の見出しが含まれる場合は1つへ統合する。
        """
        for section in sections:
            blocks = section.get("blocks", [])
            if not blocks:
                generated_heading = self._make_generated_heading_block()
                if section.get("page_nos"):
                    generated_heading["page_no"] = section["page_nos"][0]
                section["blocks"] = [generated_heading]
                continue

            heading_blocks = [
                block for block in blocks if self._block_is_heading(block)
            ]
            if not heading_blocks:
                heading = self._make_generated_heading_block()
                heading["page_no"] = blocks[0].get(
                    "page_no",
                    section.get("page_nos", [1])[0] if section.get("page_nos") else 1,
                )
            else:
                heading = self._copy_block(
                    next(
                        (
                            block
                            for block in heading_blocks
                            if not block.get("generated")
                        ),
                        heading_blocks[0],
                    )
                )
                heading["lines"] = [
                    dict(line)
                    for block in heading_blocks
                    for line in block.get("lines", [])
                ]
                heading["generated"] = all(
                    bool(block.get("generated")) for block in heading_blocks
                )

            heading["tag"] = "h1"
            heading["is_heading"] = True
            body_blocks = [
                block for block in blocks if not self._block_is_heading(block)
            ]
            section["blocks"] = [heading, *body_blocks]

    def _ensure_sections_have_body(self, sections: List[Dict[str, Any]]) -> None:
        """各セクションの level1 が h1 だけで終わらないようにする。"""
        for section in sections:
            blocks = section.get("blocks", [])
            if not blocks:
                section["blocks"] = [
                    self._make_generated_heading_block(),
                    self._make_generated_body_block(),
                ]
                continue

            has_body = any(not self._block_is_heading(block) for block in blocks)
            if not has_body:
                generated = self._make_generated_body_block()
                if section.get("page_nos"):
                    generated["page_no"] = section["page_nos"][-1]
                else:
                    generated["page_no"] = blocks[0].get("page_no", 1)
                section["blocks"].append(generated)

    def _assign_phrase_ids(
        self,
        sections: List[Dict[str, Any]],
        pages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        phrases: List[Dict[str, Any]] = []

        for page in pages:
            page["first_phrase"] = None

        page_index: Dict[int, Dict[str, Any]] = {
            int(page.get("page_no", index)): page for index, page in enumerate(pages, start=1)
        }

        for section in sections:
            phrase_index = 1
            block_index = 1
            section["phrases"] = []
            section["heading_blocks"] = []
            section["first_phrase"] = None

            for block in section.get("blocks", []):
                block["id"] = f"s{section['index']:05d}_block{block_index:05d}"
                block_index += 1

                block_phrases: List[Dict[str, Any]] = []
                phrase_units = self._phrase_units_for_block(block)
                if block.get("is_figure"):
                    phrase_units = [
                        {
                            "text": "",
                            "line_type": self.FIGURE_BLOCK_TYPE,
                            "generated": False,
                            "text_id": self._figure_element_id(block),
                        }
                    ]

                for phrase_unit in phrase_units:
                    page_no = int(block.get("page_no") or section.get("page_nos", [1])[0])
                    phrase = {
                        "index": phrase_index,
                        "text_id": phrase_unit.get(
                            "text_id",
                            f"t{phrase_index:07d}",
                        ),
                        "par_id": f"phr{phrase_index:05d}",
                        "text": phrase_unit["text"],
                        "line_type": phrase_unit["line_type"],
                        "page_no": page_no,
                        "page_label": page_index.get(page_no, {}).get("label", f"page-{page_no}"),
                        "block_id": block["id"],
                        "block_tag": block["tag"],
                        "section_index": section["index"],
                        "dtbook_file": section["dtbook_file"],
                        "smil_file": section["smil_file"],
                        "generated": bool(
                            block.get("generated") or phrase_unit["generated"]
                        ),
                    }
                    phrases.append(phrase)
                    section["phrases"].append(phrase)
                    block_phrases.append(phrase)

                    if section["first_phrase"] is None and not phrase["generated"]:
                        section["first_phrase"] = phrase

                    page_obj = page_index.get(page_no)
                    if page_obj is not None and page_obj.get("first_phrase") is None and not phrase["generated"]:
                        page_obj["first_phrase"] = phrase

                    phrase_index += 1

                block["phrases"] = block_phrases
                if self._block_is_heading(block) and block_phrases:
                    section["heading_blocks"].append(block)

            if section["first_phrase"] is None and section.get("phrases"):
                section["first_phrase"] = section["phrases"][0]

        # 構造補完用の空白だけのページがある場合でも pageList が空参照にならないようにする。
        for page in pages:
            if page.get("first_phrase") is not None:
                continue
            page_no = int(page.get("page_no", 1))
            for phrase in phrases:
                if int(phrase.get("page_no", -1)) == page_no:
                    page["first_phrase"] = phrase
                    break

        if not phrases:
            # 通常は _build_sections() と _ensure_sections_have_body() により到達しない。
            section = sections[0]
            phrase = {
                "index": 1,
                "text_id": "t0000001",
                "par_id": "phr00001",
                "text": " ",
                "line_type": "本文",
                "page_no": 1,
                "page_label": "page-1",
                "block_id": "s00001_block00001",
                "block_tag": "p",
                "section_index": 1,
                "dtbook_file": section["dtbook_file"],
                "smil_file": section["smil_file"],
                "generated": True,
            }
            section["phrases"] = [phrase]
            section["first_phrase"] = phrase
            phrases.append(phrase)

        return phrases

    def _phrase_units_for_block(self, block: Dict[str, Any]) -> List[Dict[str, Any]]:
        lines = block.get("lines", [])
        text = "".join(str(line.get("text", "")) for line in lines)
        if not text:
            return []
        text = self._normalize_parentheses(text)

        line_type = next(
            (
                str(line.get("line_type", ""))
                for line in lines
                if str(line.get("line_type", "")).strip()
            ),
            "",
        )
        generated = any(bool(line.get("generated")) for line in lines)
        return [
            {
                "text": phrase_text,
                "line_type": line_type,
                "generated": generated,
            }
            for phrase_text in self._split_text_at_punctuation(text)
        ]

    def _split_text_at_punctuation(self, text: str) -> List[str]:
        phrases: List[str] = []
        phrase_start = 0
        index = 0

        while index < len(text):
            if text[index] not in self.PHRASE_DELIMITERS:
                index += 1
                continue

            phrase_end = index + 1
            while (
                phrase_end < len(text)
                and (
                    text[phrase_end] in self.PHRASE_DELIMITERS
                    or text[phrase_end] in self.PUNCTUATION_CLOSERS
                )
            ):
                phrase_end += 1

            phrase = text[phrase_start:phrase_end]
            if phrase:
                phrases.append(phrase)
            phrase_start = phrase_end
            index = phrase_end

        if phrase_start < len(text):
            phrases.append(text[phrase_start:])
        return phrases or [text]

    def _normalize_parentheses(self, text: str) -> str:
        opening_parentheses = {"(", "（"}
        closing_parentheses = {")", "）"}
        stack: List[int] = []
        pairs: List[Tuple[int, int]] = []
        paired_indices = set()

        for index, character in enumerate(text):
            if character in opening_parentheses:
                stack.append(index)
            elif character in closing_parentheses and stack:
                opening_index = stack.pop()
                pairs.append((opening_index, index))
                paired_indices.update((opening_index, index))

        replacements: Dict[int, str] = {}
        for opening_index, closing_index in pairs:
            before = self._nearest_non_space(text, opening_index - 1, -1)
            after = self._nearest_non_space(text, closing_index + 1, 1)
            use_full_width = self._is_full_width(before) or self._is_full_width(after)
            replacements[opening_index] = "（" if use_full_width else "("
            replacements[closing_index] = "）" if use_full_width else ")"

        for index, character in enumerate(text):
            if (
                index in paired_indices
                or character not in opening_parentheses | closing_parentheses
            ):
                continue
            before = self._nearest_non_space(text, index - 1, -1)
            after = self._nearest_non_space(text, index + 1, 1)
            use_full_width = self._is_full_width(before) or self._is_full_width(after)
            if character in opening_parentheses:
                replacements[index] = "（" if use_full_width else "("
            else:
                replacements[index] = "）" if use_full_width else ")"

        return "".join(
            replacements.get(index, character)
            for index, character in enumerate(text)
        )

    def _nearest_non_space(
        self,
        text: str,
        index: int,
        step: int,
    ) -> Optional[str]:
        while 0 <= index < len(text):
            if not text[index].isspace():
                return text[index]
            index += step
        return None

    def _is_full_width(self, character: Optional[str]) -> bool:
        return bool(
            character
            and unicodedata.east_asian_width(character) in {"F", "W"}
        )

    def _block_is_heading(self, block: Dict[str, Any]) -> bool:
        tag = str(block.get("tag", "")).strip().lower()
        return bool(block.get("is_heading")) or bool(
            re.fullmatch(r"h[1-6]", tag)
        )

    def _build_dtbook(self, section: Dict[str, Any]) -> str:
        body: List[str] = []

        body.append('<?xml version="1.0" encoding="utf-8"?>')
        body.append(
            f'<?xml-stylesheet href="{self._xml(self.CSS_FILE)}" '
            f'type="text/css" media="screen"?>'
        )
        body.append(
            f'<?xml-stylesheet href="{self._xml(self.XSL_FILE)}" '
            f'type="text/xsl" media="screen"?>'
        )
        body.append(
            '<!DOCTYPE dtbook PUBLIC "-//NISO//DTD dtbook 2005-3//EN" '
            '"dtbook-2005-3.dtd">'
        )
        body.append(
            f'<dtbook xml:lang="{self._xml(self.language)}" version="2005-3" '
            'xmlns="http://www.daisy.org/z3986/2005/dtbook/">'
        )
        body.append("  <head>")
        body.append(f'    <meta name="dc:Title" content="{self._xml(self.title)}" />')
        body.append(f'    <meta name="dtb:uid" content="{self._xml(self.identifier)}" />')
        body.append(f'    <meta name="dtb:generator" content="{self._xml(self.generator)}" />')
        body.append("  </head>")
        body.append("  <book>")
        body.append("    <bodymatter>")
        body.append("      <level1>")

        for block in section.get("blocks", []):
            phrases = block.get("phrases", [])
            if not phrases and not block.get("is_figure"):
                continue

            if block.get("is_figure"):
                body.extend(self._build_dtbook_figure_block(block))
                continue

            tag = block.get("tag", "p")
            tag_attributes = (
                ' class="text-indent: 1em;"'
                if tag == "p" and block.get("text_indent")
                else ""
            )
            body.append(f"        <{tag}{tag_attributes}>")
            for phrase in phrases:
                body.append(
                    f'          <sent id="{phrase["text_id"]}" '
                    f'smilref="{section["smil_file"]}#{phrase["par_id"]}">'
                    f'{self._xml(phrase["text"])}'
                    f'</sent>'
                )
            body.append(f"        </{tag}>")

        body.append("      </level1>")
        body.append("    </bodymatter>")
        body.append("  </book>")
        body.append("</dtbook>")

        return "\n".join(body) + "\n"

    def _build_dtbook_figure_block(self, block: Dict[str, Any]) -> List[str]:
        figure = block.get("figure", {}) or {}
        figure_id = self._xml(self._figure_element_id(block))
        src = self._xml(figure.get("src", ""))
        alt = self._xml(figure.get("alt", ""))

        attrs = [
            f'id="{figure_id}"',
            f'src="{src}"',
            f'alt="{alt}"',
        ]
        phrases = block.get("phrases", [])
        if phrases:
            attrs.append(
                f'smilref="{self._xml(phrases[0]["smil_file"])}'
                f'#{self._xml(phrases[0]["par_id"])}"'
            )

        return [
            '        <imggroup>',
            f'          <img {" ".join(attrs)} />',
            '        </imggroup>',
        ]

    def _figure_element_id(self, block: Dict[str, Any]) -> str:
        return f'img_{block.get("id", "figure")}'

    def _build_smil(self, section: Dict[str, Any]) -> str:
        body: List[str] = []
        phrases = section.get("phrases", [])

        body.append('<?xml version="1.0" encoding="utf-8"?>')
        body.append(
            '<!DOCTYPE smil PUBLIC "-//NISO//DTD dtbsmil 2005-1//EN" '
            '"dtbsmil-2005-2.dtd">'
        )
        body.append('<smil xmlns="http://www.w3.org/2001/SMIL20/">')
        body.append("  <head>")
        body.append(f'    <meta name="dtb:uid" content="{self._xml(self.identifier)}" />')
        body.append(f'    <meta name="dtb:generator" content="{self._xml(self.generator)}" />')
        body.append('    <meta name="dtb:totalElapsedTime" content="0:00:00.000" />')
        body.append("    <customAttributes>")

        for custom_test_id in [
            "normal",
            "front",
            "special",
            "note",
            "noteref",
            "annotation",
            "linenum",
            "sidebar",
            "prodnote",
        ]:
            body.append(
                f'      <customTest id="{custom_test_id}" '
                f'defaultState="true" override="visible" />'
            )

        body.append("    </customAttributes>")
        body.append("  </head>")
        body.append("  <body>")
        body.append('    <seq id="seq1">')

        for phrase in phrases:
            body.append(f'      <par id="{phrase["par_id"]}">')
            body.append(
                f'        <text src="{section["dtbook_file"]}#{phrase["text_id"]}" />'
            )
            body.append("      </par>")

        body.append("    </seq>")
        body.append("  </body>")
        body.append("</smil>")

        return "\n".join(body) + "\n"

    def _build_ncx(
        self,
        pages: List[Dict[str, Any]],
        sections: List[Dict[str, Any]],
        phrases: List[Dict[str, Any]],
    ) -> str:
        page_count = len(pages)
        first_phrase = phrases[0] if phrases else None
        nav_entries = self._collect_nav_entries(sections)

        body: List[str] = []

        body.append('<?xml version="1.0" encoding="UTF-8"?>')
        body.append(
            '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" '
            '"ncx-2005-1.dtd">'
        )
        body.append(
            f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
            f'version="2005-1" xml:lang="{self._xml(self.language)}">'
        )
        body.append("  <head>")
        body.append(f'    <meta name="dtb:uid" content="{self._xml(self.identifier)}"/>')
        body.append('    <meta name="dtb:depth" content="1"/>')
        body.append(f'    <meta name="dtb:totalPageCount" content="{page_count}"/>')
        body.append(f'    <meta name="dtb:maxPageNumber" content="{page_count}"/>')
        body.append(f'    <meta name="dtb:generator" content="{self._xml(self.generator)}"/>')
        body.append("  </head>")
        body.append(f"  <docTitle><text>{self._xml(self.title)}</text></docTitle>")
        body.append("  <navMap>")

        for play_order, entry in enumerate(nav_entries, start=1):
            body.append(
                f'    <navPoint id="nav_{play_order:05d}" playOrder="{play_order}">'
            )
            body.append(f"      <navLabel><text>{self._xml(entry['label'])}</text></navLabel>")
            body.append(f'      <content src="{entry["smil_file"]}#{entry["par_id"]}"/>')
            body.append("    </navPoint>")

        body.append("  </navMap>")
        body.append('  <pageList id="pages">')
        body.append("    <navLabel><text>Pages</text></navLabel>")

        page_play_order_start = len(nav_entries) + 1
        for offset, page in enumerate(pages):
            pageno = int(page.get("page_no", offset + 1))
            page_phrase = page.get("first_phrase") or first_phrase
            if page_phrase is None:
                continue
            play_order = page_play_order_start + offset

            body.append(
                f'    <pageTarget id="pagetarget_{pageno:05d}" '
                f'type="normal" value="{pageno}" playOrder="{play_order}">'
            )
            body.append(f"      <navLabel><text>{pageno}</text></navLabel>")
            body.append(
                f'      <content src="{page_phrase["smil_file"]}#{page_phrase["par_id"]}"/>'
            )
            body.append("    </pageTarget>")

        body.append("  </pageList>")
        body.append("</ncx>")

        return "\n".join(body) + "\n"

    def _collect_nav_entries(self, sections: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []

        for section in sections:
            heading_blocks = section.get("heading_blocks", [])
            label = ""
            par_id = ""

            if heading_blocks:
                phrases = heading_blocks[0].get("phrases", [])
                label = "".join(str(phrase.get("text", "")) for phrase in phrases).strip()
                if phrases:
                    par_id = phrases[0]["par_id"]
            elif section.get("first_phrase"):
                label = self.title
                par_id = section["first_phrase"]["par_id"]

            if not label:
                label = self.title
            if not par_id and section.get("phrases"):
                par_id = section["phrases"][0]["par_id"]
            if not par_id:
                continue

            entries.append(
                {
                    "label": label,
                    "smil_file": section["smil_file"],
                    "par_id": par_id,
                }
            )

        return entries

    def _build_opf(self, sections: List[Dict[str, Any]]) -> str:
        body: List[str] = []

        body.append('<?xml version="1.0" encoding="UTF-8"?>')
        body.append(
            '<!DOCTYPE package PUBLIC "+//ISBN 0-9673008-1-9//DTD OEB 1.2 Package//EN"'
        )
        body.append('  "http://openebook.org/dtds/oeb-1.2/oebpkg12.dtd">')
        body.append(
            '<package xmlns="http://openebook.org/namespaces/oeb-package/1.0/" '
            'unique-identifier="uid">'
        )
        body.append("  <metadata>")
        body.append('    <dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/"')
        body.append(
            '                 xmlns:oebpackage="http://openebook.org/namespaces/oeb-package/1.0/">'
        )
        body.append(f"      <dc:Title>{self._xml(self.title)}</dc:Title>")
        body.append(
            f'      <dc:Identifier id="uid" scheme="DTB">'
            f'{self._xml(self.identifier)}'
            f'</dc:Identifier>'
        )
        body.append(f"      <dc:Language>{self._xml(self.language)}</dc:Language>")
        body.append(f"      <dc:Publisher>{self._xml(self.producer)}</dc:Publisher>")
        body.append(f"      <dc:Date>{self._xml(self.produced_date)}</dc:Date>")
        body.append("      <dc:Format>ANSI/NISO Z39.86-2005</dc:Format>")
        body.append("    </dc-metadata>")
        body.append("    <x-metadata>")
        body.append('      <meta name="dtb:multimediaContent" content="text"/>')
        body.append('      <meta name="dtb:multimediaType" content="textNCX"/>')
        body.append('      <meta name="dtb:totalTime" content="0:00:00.000"/>')
        body.append(f'      <meta name="dtb:producer" content="{self._xml(self.producer)}"/>')
        body.append(
            f'      <meta name="dtb:producedDate" content="{self._xml(self.produced_date)}"/>'
        )
        body.append("    </x-metadata>")
        body.append("  </metadata>")
        body.append("  <manifest>")
        body.append(f'    <item id="opf" href="{self.OPF_FILE}" media-type="text/xml"/>')
        body.append(
            f'    <item id="ncx" href="{self.NCX_FILE}" '
            f'media-type="application/x-dtbncx+xml"/>'
        )

        for section in sections:
            index = section["index"]
            body.append(
                f'    <item id="dtbook{index:05d}" href="{section["dtbook_file"]}" '
                f'media-type="application/x-dtbook+xml"/>'
            )
            body.append(
                f'    <item id="smil{index:05d}" href="{section["smil_file"]}" '
                f'media-type="application/smil"/>'
            )

        for figure_index, block in enumerate(
            self._iter_figure_blocks(sections),
            start=1,
        ):
            figure = block.get("figure", {}) or {}
            body.append(
                f'    <item id="figure{figure_index:05d}" '
                f'href="{self._xml(figure.get("src", ""))}" '
                f'media-type="image/jpeg"/>'
            )

        body.append(f'    <item id="css" href="{self.CSS_FILE}" media-type="text/css"/>')
        body.append(f'    <item id="xsl" href="{self.XSL_FILE}" media-type="text/xsl"/>')
        body.append("  </manifest>")
        body.append("  <spine>")
        for section in sections:
            body.append(f'    <itemref idref="smil{section["index"]:05d}"/>')
        body.append("  </spine>")
        body.append("</package>")

        return "\n".join(body) + "\n"

    def _build_css(self) -> str:
        return "\n".join(
            [
                "dtbook { display: block; }",
                "book, bodymatter, level1, p, h1, imggroup { display: block; }",
                "h1 { font-size: 1.6em; font-weight: bold; margin: 1em 0; }",
                "p { margin: 0.5em 0; }",
                "imggroup { margin: 1em 0; }",
                "img { max-width: 100%; }",
                "sent { display: inline; }",
                "",
            ]
        )

    def _build_xsl(self) -> str:
        return "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<xsl:stylesheet version="1.0" ',
                '  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"',
                '  xmlns:dtb="http://www.daisy.org/z3986/2005/dtbook/">',
                '  <xsl:output method="html" encoding="UTF-8"/>',
                '  <xsl:template match="/">',
                '    <html><body><xsl:apply-templates select="//dtb:bodymatter"/></body></html>',
                '  </xsl:template>',
                '  <xsl:template match="dtb:h1"><h1><xsl:apply-templates/></h1></xsl:template>',
                '  <xsl:template match="dtb:p"><p><xsl:apply-templates/></p></xsl:template>',
                '  <xsl:template match="dtb:imggroup"><div class="imggroup"><xsl:apply-templates/></div></xsl:template>',
                '  <xsl:template match="dtb:img"><img src="{@src}" alt="{@alt}"/></xsl:template>',
                '  <xsl:template match="dtb:sent"><xsl:value-of select="."/></xsl:template>',
                '</xsl:stylesheet>',
                "",
            ]
        )

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _xml(self, value: Any) -> str:
        return escape(str(value), {'"': "&quot;"})

    def _safe_identifier_for_asset_name(self, identifier: str) -> str:
        candidate = identifier.rsplit(":", 1)[-1].strip() or "textdaisy"
        candidate = quote(candidate, safe="-_.")
        candidate = re.sub(r"%", "_", candidate)
        candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate)
        return candidate[:120] or "textdaisy"

    def _safe_filename(self, value: str) -> str:
        value = value.strip() or "textdaisy"
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
        value = value.rstrip(" .") or "textdaisy"
        reserved_name = value.split(".", 1)[0].upper()
        if reserved_name in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }:
            value = f"_{value}"
        return value
