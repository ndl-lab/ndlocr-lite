import argparse
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ocr


class OutputDirectoryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.args = argparse.Namespace(
            sourcedir=None,
            sourceimg="sample.png",
            sourcepdf=None,
            output=str(self.root / "parent" / "output"),
        )

    def assert_processing_starts(self):
        with patch.object(
            ocr, "get_detector", side_effect=RuntimeError("model initialization reached")
        ) as detector:
            with self.assertRaisesRegex(RuntimeError, "model initialization reached"):
                ocr.process(self.args)
        detector.assert_called_once_with(self.args)

    def test_missing_output_and_parents_are_created(self):
        self.assert_processing_starts()
        self.assertTrue(Path(self.args.output).is_dir())

    def test_existing_directory_contents_are_preserved(self):
        output = Path(self.args.output)
        output.mkdir(parents=True)
        existing = output / "existing.txt"
        existing.write_text("keep", encoding="utf-8")
        self.assert_processing_starts()
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_output_file_is_reported_without_starting_models(self):
        output = self.root / "file"
        output.write_text("keep", encoding="utf-8")
        self.args.output = str(output)
        with patch.object(ocr, "get_detector") as detector, contextlib.redirect_stdout(
            io.StringIO()
        ) as messages:
            ocr.process(self.args)
        detector.assert_not_called()
        self.assertIn(f"Failed to create output directory: {output}", messages.getvalue())
        self.assertIn("Error:", messages.getvalue())
        self.assertEqual(output.read_text(encoding="utf-8"), "keep")

    def test_creation_permission_error_is_reported_without_starting_models(self):
        with patch.object(
            ocr.os, "makedirs", side_effect=PermissionError("permission denied")
        ), patch.object(ocr, "get_detector") as detector, contextlib.redirect_stdout(
            io.StringIO()
        ) as messages:
            ocr.process(self.args)
        detector.assert_not_called()
        self.assertIn("Failed to create output directory:", messages.getvalue())
        self.assertIn("permission denied", messages.getvalue())
        self.assertFalse(Path(self.args.output).exists())


if __name__ == "__main__":
    unittest.main()
