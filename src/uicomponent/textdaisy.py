from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote
from uuid import uuid4
import re
import shutil
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class TextDaisyExportResult:
    """生成したテキストDAISYファイル一式の保存先。"""

    directory: str
    zip_path: Optional[str]
    page_count: int


class TextDaisyExporter:
    """NDLOCR-Lite の OCR 結果から DAISY 3 textNCX ファイルセットを生成する。

    変換方針:
    - NDLOCR-Lite XML の <TEXTBLOCK> 1 件を DTBook の <p> 1 件へ対応させる。
    - <LINE TYPE="タイトル本文"> が出た位置で新しいセクションを開始する。
    - セクションごとに ptk00001.xml / ptk00001.smil、ptk00002.xml / ptk00002.smil ... を出力する。
    - 各セクション内の <sent id="t0000001"> と <par id="phr00001"> は、そのファイルごとに 1 から採番する。
    - 各 OCR 行は <sent> として出力し、対応する SMIL の <par id="phrxxxxx"> と相互参照する。
    - NDLOCR-Lite XML の <BLOCK TYPE="図版"> は DTBook の <imggroup>/<img> と、同期用説明 <p><sent> として出力する。

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
    FIGURE_PLACEHOLDER_FILE = "images/figure_placeholder.svg"

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
        self._ensure_sections_start_with_heading(sections)
        self._ensure_sections_have_body(sections)
        phrases = self._assign_phrase_ids(sections, normalized_pages)
        safe_folder = self._safe_filename(folder_name or f"{self.title}_textdaisy")
        book_dir = Path(output_dir) / safe_folder

        if book_dir.exists():
            shutil.rmtree(book_dir)

        book_dir.mkdir(parents=True, exist_ok=True)

        for section in sections:
            (book_dir / section["dtbook_file"]).write_text(
                self._build_dtbook(section), encoding="utf-8"
            )
            (book_dir / section["smil_file"]).write_text(
                self._build_smil(section), encoding="utf-8"
            )

        if self._sections_have_figures(sections):
            figure_path = book_dir / self.FIGURE_PLACEHOLDER_FILE
            figure_path.parent.mkdir(parents=True, exist_ok=True)
            figure_path.write_text(self._build_figure_placeholder_svg(), encoding="utf-8")

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
            "blocks": blocks,
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
                blocks.extend(self._split_lines_to_dtbook_blocks(current_lines))
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

        def flush_pending_lines() -> None:
            nonlocal pending_lines
            if pending_lines:
                blocks.extend(self._split_lines_to_dtbook_blocks(pending_lines))
                pending_lines = []

        if self._is_figure_block_type(block_type):
            # 図版ブロック自体を DTBook の画像要素として残す。
            # SMIL で同期できるよう、後続の説明 p/sent も同じ内部ブロックから生成する。
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

    def _blocks_from_textblock_elem(self, textblock_elem: ET.Element) -> List[Dict[str, Any]]:
        lines: List[Dict[str, Any]] = []
        for elem in list(textblock_elem):
            if self._local_name(elem.tag) != "LINE":
                continue
            line = self._line_from_xml(elem)
            if line["text"]:
                lines.append(line)

        return self._split_lines_to_dtbook_blocks(lines)

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
        }

    def _split_lines_to_dtbook_blocks(self, lines: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        current_tag: Optional[str] = None
        current_lines: List[Dict[str, Any]] = []

        def tag_for_line(line: Dict[str, Any]) -> str:
            return "h1" if self._is_title_line(line) else "p"

        def flush() -> None:
            nonlocal current_lines, current_tag
            if current_lines and current_tag:
                blocks.append(self._make_dtbook_block(current_tag, current_lines))
            current_tag = None
            current_lines = []

        for line in lines:
            text = str(line.get("text", "")).strip()
            if not text:
                continue

            tag = tag_for_line(line)
            if current_tag is None:
                current_tag = tag
            elif current_tag != tag:
                # TEXTBLOCK の途中に「タイトル本文」がある場合も、そこで段落/見出しを切る。
                flush()
                current_tag = tag

            current_lines.append({"text": text, "line_type": line.get("line_type", "本文")})

        flush()
        return blocks

    def _make_dtbook_block(
        self,
        tag: str,
        lines: Sequence[Dict[str, Any]],
        *,
        generated: bool = False,
    ) -> Dict[str, Any]:
        return {
            "tag": tag,
            "is_heading": tag.startswith("h"),
            "generated": generated,
            "lines": [dict(line) for line in lines],
        }

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

    def _make_figure_block(self, block_elem: ET.Element) -> Dict[str, Any]:
        figure_label = self._figure_label(block_elem)
        return {
            "tag": "imggroup",
            "is_heading": False,
            "is_figure": True,
            "generated": False,
            "figure": {
                "alt": figure_label,
                "src": self.FIGURE_PLACEHOLDER_FILE,
                "source_type": block_elem.attrib.get("TYPE", self.FIGURE_BLOCK_TYPE),
                "x": block_elem.attrib.get("X", ""),
                "y": block_elem.attrib.get("Y", ""),
                "width": block_elem.attrib.get("WIDTH", ""),
                "height": block_elem.attrib.get("HEIGHT", ""),
                "conf": block_elem.attrib.get("CONF", ""),
            },
            # 画像要素そのものは SMIL の text 参照先にできない再生環境があるため、
            # 同期・読み上げ用に説明文を p/sent として続けて出力する。
            "lines": [
                {
                    "text": figure_label,
                    "line_type": self.FIGURE_BLOCK_TYPE,
                    "generated": True,
                }
            ],
        }

    def _figure_label(self, block_elem: ET.Element) -> str:
        for key in ("ALT", "alt", "TITLE", "title", "LABEL", "label"):
            value = block_elem.attrib.get(key)
            if value and str(value).strip():
                return str(value).strip()
        return self.FIGURE_BLOCK_TYPE

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

                if copied_block.get("is_heading"):
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
        copied.pop("phrases", None)
        copied.pop("id", None)
        return copied

    def _ensure_sections_start_with_heading(self, sections: List[Dict[str, Any]]) -> None:
        """各セクションの level1 直下が必ず h1 から始まるようにする。

        TEXT DAISY 3 / DTBook 2005-3 では、level1 の構造を安定させるため、
        セクション冒頭に見出しを置く。元の NDLOCR-Lite XML に
        TYPE="タイトル本文" が無いまま始まるセクションでは、書名を補助見出しとして追加する。
        """
        for section in sections:
            blocks = section.get("blocks", [])

            if not blocks:
                generated_heading = self._make_generated_heading_block()
                if section.get("page_nos"):
                    generated_heading["page_no"] = section["page_nos"][0]
                section["blocks"] = [generated_heading]
                continue

            first_block = blocks[0]
            if bool(first_block.get("is_heading")):
                continue

            generated_heading = self._make_generated_heading_block()
            generated_heading["page_no"] = first_block.get(
                "page_no",
                section.get("page_nos", [1])[0] if section.get("page_nos") else 1,
            )
            blocks.insert(0, generated_heading)
            section["blocks"] = blocks

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

            has_body = any(not bool(block.get("is_heading")) for block in blocks)
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
                for line in block.get("lines", []):
                    page_no = int(block.get("page_no") or section.get("page_nos", [1])[0])
                    phrase = {
                        "index": phrase_index,
                        "text_id": f"t{phrase_index:07d}",
                        "par_id": f"phr{phrase_index:05d}",
                        "text": str(line.get("text", "")),
                        "line_type": str(line.get("line_type", "")),
                        "page_no": page_no,
                        "page_label": page_index.get(page_no, {}).get("label", f"page-{page_no}"),
                        "block_id": block["id"],
                        "block_tag": block["tag"],
                        "section_index": section["index"],
                        "dtbook_file": section["dtbook_file"],
                        "smil_file": section["smil_file"],
                        "generated": bool(block.get("generated") or line.get("generated")),
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
                if block.get("is_heading") and block_phrases:
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
                if not phrases:
                    continue

                body.append('        <p class="figure-description">')
                for phrase in phrases:
                    body.append(
                        f'          <sent id="{phrase["text_id"]}" '
                        f'smilref="{section["smil_file"]}#{phrase["par_id"]}">'
                        f'{self._xml(phrase["text"])}'
                        f'</sent>'
                    )
                body.append("        </p>")
                continue

            tag = block.get("tag", "p")
            body.append(f"        <{tag}>")
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
        figure_id = self._xml(f'img_{block.get("id", "figure")}')
        src = self._xml(figure.get("src", self.FIGURE_PLACEHOLDER_FILE))
        alt = self._xml(figure.get("alt", self.FIGURE_BLOCK_TYPE) or self.FIGURE_BLOCK_TYPE)

        attrs = [
            f'id="{figure_id}"',
            f'src="{src}"',
            f'alt="{alt}"',
        ]

        # 元XMLの座標を DTBook の妥当性に影響しない class/title 等へは入れず、
        # 必要最小限の img 属性に限定する。詳細説明は後続 p/sent 側で同期する。
        return [
            '        <imggroup>',
            f'          <img {" ".join(attrs)} />',
            '        </imggroup>',
        ]

    def _sections_have_figures(self, sections: List[Dict[str, Any]]) -> bool:
        return any(
            bool(block.get("is_figure"))
            for section in sections
            for block in section.get("blocks", [])
        )

    def _build_figure_placeholder_svg(self) -> str:
        return "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1">',
                '  <title>図版</title>',
                '  <desc>NDLOCR-Lite XML の BLOCK TYPE="図版" に対応するプレースホルダー画像です。</desc>',
                '</svg>',
                '',
            ]
        )

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

        if self._sections_have_figures(sections):
            body.append(
                f'    <item id="figure_placeholder" href="{self.FIGURE_PLACEHOLDER_FILE}" '
                f'media-type="image/svg+xml"/>'
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
        return self._safe_filename(candidate)

    def _safe_filename(self, value: str) -> str:
        # DAISY 3 の URI 互換性を考慮し、出力フォルダ名・参照ファイル名を ASCII 中心に寄せる。
        value = value.strip() or "textdaisy"
        value = quote(value, safe="-_.")
        value = re.sub(r"%", "_", value)
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
        return value[:120] or "textdaisy"
