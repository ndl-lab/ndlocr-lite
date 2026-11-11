"""Launch the packaged GUI without changing the desktop build entry point."""


def run(argv=None):
    from functools import partial
    from . import main as app

    args = app.parse_gui_args(argv, download_models=True)
    app.ft.run(
        partial(app.main, args=args, download_models=True),
        assets_dir=str(app.ASSETS_DIR),
    )
