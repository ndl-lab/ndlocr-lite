from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "set_version.py"


class SetVersionTest(unittest.TestCase):
    def run_version(self, version, *options):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "pyproject.toml"
            original = (
                '[project]\nname = "ndlocr-lite"\nversion = "1.4.0"\n'
                '\n[tool.example]\nversion = "unchanged"\n'
            )
            project.write_text(original, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), version, *options],
                cwd=root,
                capture_output=True,
                text=True,
            )
            version_file = root / "src" / "version.py"
            return (
                result,
                project.read_text(encoding="utf-8"),
                version_file.read_text(encoding="utf-8")
                if version_file.exists() else None,
                original,
            )

    def test_gui_version_is_unchanged(self):
        result, project, version_file, _ = self.run_version("1.5.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "1.5.0"', project)
        self.assertIn('__version__ = "1.5.0"', version_file)
        self.assertIn('[tool.example]\nversion = "unchanged"', project)

    def test_cli_version_is_development_release(self):
        result, project, version_file, _ = self.run_version("1.5.0", "--dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "1.5.0.dev0"', project)
        self.assertIn('__version__ = "1.5.0.dev0"', version_file)
        self.assertIn('[tool.example]\nversion = "unchanged"', project)

    def test_invalid_dev_versions_do_not_modify_files(self):
        for version in ("v1.5.0", "1.5", "1.5.0-rc1", "1.5.0+build"):
            with self.subTest(version=version):
                result, project, version_file, original = self.run_version(
                    version, "--dev"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("version", result.stderr)
                self.assertEqual(project, original)
                self.assertIsNone(version_file)


if __name__ == "__main__":
    unittest.main()
