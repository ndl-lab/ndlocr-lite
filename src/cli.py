"""Console entry point for the pip-distributed CLI."""

import sys


def main():
    if sys.argv[1:2] == ["gui"]:
        from ndlocr_lite_gui import run

        run(sys.argv[2:])
        return

    from ocr import main as ocr_main

    ocr_main(download_models=True)
