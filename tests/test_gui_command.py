import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import cli


class GuiCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "ndlocr_lite_gui", ROOT / "ndlocr-lite-gui" / "__init__.py"
        )
        cls.package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.package
        spec.loader.exec_module(cls.package)
        from ndlocr_lite_gui import main
        cls.app = main

    def test_gui_dispatch_forwards_only_gui_arguments(self):
        argv = ["ndlocr-lite", "gui", "--device", "cpu"]
        with patch.object(sys, "argv", argv), patch.object(self.package, "run") as run:
            cli.main()
            self.assertEqual(sys.argv, ["ndlocr-lite", "gui", "--device", "cpu"])
        run.assert_called_once_with(["--device", "cpu"])

    def test_gui_help_does_not_start_window_or_download(self):
        with patch.object(self.app.ft, "run") as run, patch(
            "model_download.resolve_model_paths"
        ) as resolve, contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:
                self.package.run(["--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("--det-weights", output.getvalue())
        run.assert_not_called()
        resolve.assert_not_called()

    def test_invalid_gui_option_does_not_start_window(self):
        with patch.object(self.app.ft, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                self.package.run(["--unknown"])
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_gui_launcher_passes_args_and_asset_directory(self):
        with patch.object(self.app.ft, "run") as run:
            self.package.run(["--device", "cpu", "--det-weights", "custom.onnx"])
        target = run.call_args.args[0]
        self.assertIs(target.func, self.app.main)
        self.assertTrue(target.keywords["download_models"])
        args = target.keywords["args"]
        self.assertEqual(args.det_weights, "custom.onnx")
        self.assertIsNone(args.rec_weights)
        self.assertEqual(args.device, "cpu")
        self.assertEqual(run.call_args.kwargs["assets_dir"], str(self.app.ASSETS_DIR))
        self.assertTrue((self.app.ASSETS_DIR / "icon.png").is_file())

    def test_packaged_resource_paths(self):
        self.assertEqual(self.app.SOURCE_DIR, ROOT)
        self.assertEqual(self.app.APP_DIR, ROOT / "ndlocr-lite-gui")
        self.assertEqual(self.app.build_default_config()["langcode"], "ja")

    def test_standalone_gui_preserves_bundled_model_defaults(self):
        args = self.app.parse_gui_args(["--flet-host-option"])
        self.assertEqual(
            args.det_weights,
            str(self.app.SOURCE_DIR / "model" / "deim-s-1024x1024.onnx"),
        )
        self.assertIsNotNone(args.rec_weights)

    def test_packaged_model_loading_resolves_models_first(self):
        args = self.app.parse_gui_args([], download_models=True)
        events = []
        engine = Mock()
        with patch("model_download.resolve_model_paths", side_effect=lambda *a: events.append("resolve")) as resolve, patch.object(
            self.app, "load_runtime_modules", side_effect=lambda: events.append("runtime")
        ), patch.object(self.app, "ocr", engine):
            models = self.app.load_ocr_models(args, download_models=True)
        resolve.assert_called_once_with(args, self.app.SOURCE_DIR / "model")
        self.assertEqual(events, ["resolve", "runtime"])
        self.assertEqual(len(models), 4)
        engine.get_detector.assert_called_once_with(args)
        self.assertEqual(engine.get_recognizer.call_count, 3)

    def test_standalone_model_loading_never_downloads(self):
        args = self.app.parse_gui_args([])
        with patch("model_download.resolve_model_paths") as resolve, patch.object(
            self.app, "load_runtime_modules"
        ), patch.object(self.app, "ocr", Mock()):
            self.app.load_ocr_models(args)
        resolve.assert_not_called()

    def test_download_errors_propagate_to_gui_initialization(self):
        args = self.app.parse_gui_args([], download_models=True)
        with patch("model_download.resolve_model_paths", side_effect=RuntimeError("offline")), patch.object(
            self.app, "load_runtime_modules"
        ) as runtime:
            with self.assertRaisesRegex(RuntimeError, "offline"):
                self.app.load_ocr_models(args, download_models=True)
        runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
