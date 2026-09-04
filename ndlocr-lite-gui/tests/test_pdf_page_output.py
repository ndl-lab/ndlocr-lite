import importlib.util
import tempfile
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gui_config = load_module("gui_config", "ndlocr-lite-gui/gui_config.py")
pdf_output_utils = load_module("pdf_output_utils", "src/pdf_output_utils.py")
tei_module = load_module("ndlkoten2tei", "src/tools/ndlkoten2tei.py")


class PdfPageOutputTest(unittest.TestCase):
    def setUp(self):
        self.page_result = {
            "page_xml": '<PAGE WIDTH="100" HEIGHT="200"><LINE X="1" Y="2" WIDTH="3" HEIGHT="4" STRING="あ"/></PAGE>',
            "text": "ページ1のテキスト",
            "json_lines": [
                {
                    "boundingBox": [[1, 2], [1, 6], [4, 2], [4, 6]],
                    "id": 0,
                    "text": "あ",
                }
            ],
            "img_width": 100,
            "img_height": 200,
            "img_name": "sample_00001.png",
        }

    def test_gui_default_enables_pdf_page_output(self):
        self.assertTrue(gui_config.build_default_config()["pdf_page_output"])

    def test_page_output_helpers_only_split_multi_page_pdf(self):
        self.assertFalse(pdf_output_utils.should_output_pdf_pages_separately(1, True))
        self.assertFalse(pdf_output_utils.should_output_pdf_pages_separately(3, False))
        self.assertTrue(pdf_output_utils.should_output_pdf_pages_separately(3, True))

    def test_write_pdf_page_outputs_uses_page_numbered_filenames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page_json = pdf_output_utils.write_pdf_page_outputs(
                tmpdir,
                "sample",
                0,
                self.page_result,
                source_path="/tmp/sample.pdf",
                write_json=True,
                write_xml=True,
                write_txt=True,
                write_tei=True,
                convert_tei_func=tei_module.convert_tei,
                include_xml_in_json=True,
            )

            self.assertEqual(
                sorted(path.name for path in Path(tmpdir).iterdir()),
                [
                    "sample_00001.json",
                    "sample_00001.txt",
                    "sample_00001.xml",
                    "sample_00001_tei.xml",
                ],
            )
            self.assertEqual(page_json["imginfo"]["img_name"], "sample_00001.png")
            self.assertIn("<OCRDATASET>", Path(tmpdir, "sample_00001.xml").read_text(encoding="utf-8"))
            self.assertEqual(
                Path(tmpdir, "sample_00001.txt").read_text(encoding="utf-8"),
                self.page_result["text"],
            )

    def test_existing_combined_xml_helper_still_wraps_single_page(self):
        wrapped = pdf_output_utils.build_single_page_xml(self.page_result["page_xml"])
        self.assertEqual(
            wrapped,
            "<OCRDATASET>\n"
            + self.page_result["page_xml"]
            + "\n</OCRDATASET>",
        )


if __name__ == "__main__":
    unittest.main()
