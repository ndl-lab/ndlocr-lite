from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
from types import ModuleType


FEATURE_NAMES = ('data_pool', 'llm', 'table_structure')


@dataclass(frozen=True)
class OptionalFeatures:
    data_pool: ModuleType | None = None
    llm: ModuleType | None = None
    table_structure: ModuleType | None = None


def load_optional_features(feature_dir: Path | None = None) -> OptionalFeatures:
    """Load only feature modules whose source files are present."""
    directory = Path(feature_dir) if feature_dir is not None else Path(__file__).resolve().parent
    loaded = {}

    for name in FEATURE_NAMES:
        module_path = next(
            (
                candidate
                for candidate in (
                    directory / f'{name}.py',
                    directory / f'{name}.pyc',
                )
                if candidate.is_file()
            ),
            None,
        )
        if module_path is None:
            loaded[name] = None
            continue

        module_name = f'ndlocr_optional_features.{name}.{abs(hash(module_path.resolve()))}'
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f'Cannot load optional feature module: {module_path}')
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        loaded[name] = module

    return OptionalFeatures(**loaded)
