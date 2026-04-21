# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


def collect_files(path, only=None, skip=None, ext=""):
    from itertools import chain
    from pathlib import Path
    from yaspin import yaspin
    path = Path(path)
    if path.is_file():
        return [path]
    files = list()
    with yaspin(text="Collecting files"):
        globs = chain(*[path.rglob("*"+e) for e in ext.split("|")])
        for file_path in globs:
            if (only and only not in file_path.name) or (skip and skip in file_path.name):
                continue
            files.append(file_path)
    return files
