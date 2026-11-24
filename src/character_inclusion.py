import csv
import re
from pathlib import Path


MAPPING_SOURCE_URL = (
    "https://github.com/ndl-lab/tugidigi-web/blob/master/"
    "src/main/resources/config/index/jd_book.json"
)
MAPPING_PATH = Path(__file__).resolve().parent / "config" / "character_inclusion_mapping.csv"


def _load_mapping_tables():
    single_character_mappings = {}
    multi_character_mappings = {}

    with MAPPING_PATH.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_fields = {
            "source",
            "target",
            "source_codepoints",
            "target_codepoints",
        }
        if set(reader.fieldnames or ()) != expected_fields:
            raise ValueError(f"Unexpected inclusion mapping columns: {reader.fieldnames}")

        for row in reader:
            source = row["source"]
            target = row["target"]
            if not source:
                raise ValueError("Inclusion mapping sources must not be empty")
            if source in single_character_mappings or source in multi_character_mappings:
                raise ValueError(f"Duplicate inclusion mapping source: {source!r}")
            if len(source) == 1:
                single_character_mappings[source] = target
            else:
                multi_character_mappings[source] = target

    translation_table = str.maketrans(single_character_mappings)
    multi_pattern = None
    if multi_character_mappings:
        sources = sorted(multi_character_mappings, key=len, reverse=True)
        multi_pattern = re.compile("|".join(re.escape(source) for source in sources))
    return translation_table, multi_character_mappings, multi_pattern


_TRANSLATION_TABLE, _MULTI_CHARACTER_MAPPINGS, _MULTI_CHARACTER_PATTERN = (
    _load_mapping_tables()
)


def _replace_multi_character_mapping(match):
    return _MULTI_CHARACTER_MAPPINGS[match.group(0)]


def apply_character_inclusion(text: str) -> str:
    """Apply the jd_book mapping char filter with longest matches first."""
    if not text:
        return text
    if _MULTI_CHARACTER_PATTERN is not None:
        text = _MULTI_CHARACTER_PATTERN.sub(_replace_multi_character_mapping, text)
    return text.translate(_TRANSLATION_TABLE)
