import json
import os


def should_output_pdf_pages_separately(page_count: int, enabled: bool) -> bool:
    return bool(enabled) and int(page_count) > 1


def build_pdf_page_output_stem(output_stem: str, page_index: int) -> str:
    return f"{output_stem}_{page_index + 1:05}"


def build_single_page_xml(page_xml: str) -> str:
    return f"<OCRDATASET>\n{page_xml}\n</OCRDATASET>"


def build_pdf_page_json_obj(page_result: dict, source_path: str, include_xml: bool = False) -> dict:
    page_json_obj = {
        "contents": [page_result["json_lines"]],
        "imginfo": {
            "img_width": page_result["img_width"],
            "img_height": page_result["img_height"],
            "img_path": source_path,
            "img_name": page_result["img_name"],
        },
    }
    if include_xml:
        page_json_obj["xml"] = build_single_page_xml(page_result["page_xml"])
    return page_json_obj


def write_pdf_page_outputs(
    output_dir: str,
    output_stem: str,
    page_index: int,
    page_result: dict,
    *,
    source_path: str,
    write_json: bool,
    write_xml: bool,
    write_txt: bool,
    write_tei: bool = False,
    convert_tei_func=None,
    include_xml_in_json: bool = False,
) -> dict:
    page_output_stem = build_pdf_page_output_stem(output_stem, page_index)
    page_json_obj = build_pdf_page_json_obj(
        page_result,
        source_path=source_path,
        include_xml=include_xml_in_json,
    )

    if write_xml:
        with open(os.path.join(output_dir, page_output_stem + ".xml"), "w", encoding="utf-8") as wf:
            wf.write(build_single_page_xml(page_result["page_xml"]))

    if write_txt:
        with open(os.path.join(output_dir, page_output_stem + ".txt"), "w", encoding="utf-8") as wf:
            wf.write(page_result["text"])

    if write_json:
        with open(os.path.join(output_dir, page_output_stem + ".json"), "w", encoding="utf-8") as wf:
            wf.write(json.dumps(page_json_obj, ensure_ascii=False, indent=2))

    if write_tei:
        if convert_tei_func is None:
            raise ValueError("convert_tei_func is required when write_tei is True")
        with open(os.path.join(output_dir, page_output_stem + "_tei.xml"), "wb") as wf:
            wf.write(convert_tei_func([page_json_obj]))

    return page_json_obj
