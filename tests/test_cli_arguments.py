import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ocr


class CliArgumentsTest(unittest.TestCase):
    def parse_cli(self, *options):
        argv = ["ocr.py", "--sourceimg", "sample.png", "--output", "output", *options]
        with patch.object(sys, "argv", argv), patch.object(ocr, "process") as process:
            ocr.main()
        process.assert_called_once()
        return process.call_args.args[0]

    def test_tcy_custom_options_reach_processing(self):
        args = self.parse_cli("--enable-tcy", "--tcy-max-aspect-ratio", "0.6",
                              "--tcy-ocr-margin-ratio", "0.4")
        self.assertTrue(args.enable_tcy)
        self.assertEqual(args.tcy_max_aspect_ratio, 0.6)
        self.assertEqual(args.tcy_ocr_margin_ratio, 0.4)
        self.assertEqual(args.sourceimg, "sample.png")
        self.assertEqual(args.output, "output")

    def test_each_tcy_option_accepts_its_value(self):
        options = [
            ("min-line-width", "20", 20),
            ("max-line-width", "90", 90),
            ("det-margin-ratio", "0.2", 0.2),
            ("ocr-margin-ratio", "0.4", 0.4),
            ("min-components", "3", 3),
            ("max-aspect-ratio", "0.6", 0.6),
            ("seg-min-gap", "7", 7),
            ("ink-threshold-ratio", "0.15", 0.15),
        ]
        for name, value, expected in options:
            with self.subTest(option=name):
                args = self.parse_cli("--enable-tcy", "--tcy-" + name, value)
                self.assertEqual(getattr(args, "tcy_" + name.replace("-", "_")), expected)

    def test_commands_without_custom_tcy_options(self):
        for options, enabled in [((), False), (("--enable-tcy",), True)]:
            with self.subTest(options=options):
                args = self.parse_cli(*options)
                self.assertEqual(args.enable_tcy, enabled)
                self.assertEqual(args.sourceimg, "sample.png")
                self.assertEqual(args.output, "output")

    def test_invalid_options_do_not_start_processing(self):
        for options in [
            ("--unknown-option",),
            ("--tcy-max-aspect-ratio", "0.6"),
            ("--enable-tcy", "--tcy-unknown-option", "1"),
            ("--enable-tcy", "--tcy-min-line-width", "invalid"),
        ]:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                argv = ["ocr.py", "--sourceimg", "sample.png", "--output", "output", *options]
                with patch.object(sys, "argv", argv), patch.object(ocr, "process") as process:
                    with self.assertRaises(SystemExit) as error:
                        ocr.main()
                self.assertEqual(error.exception.code, 2)
                process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
