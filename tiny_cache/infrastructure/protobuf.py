from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_PROTOBUF_FILES = ("cache_pb2.py", "cache_pb2_grpc.py")


def ensure_generated_protobuf_modules(project_root: Path | None = None) -> None:
    root = PROJECT_ROOT if project_root is None else Path(project_root)
    missing_files = [
        name for name in REQUIRED_PROTOBUF_FILES if not (root / name).is_file()
    ]
    if missing_files:
        missing = ", ".join(missing_files)
        raise RuntimeError(
            f"Missing generated protobuf files: {missing}. Run `task gen`."
        )


def load_generated_protobuf_modules() -> tuple[ModuleType, ModuleType]:
    ensure_generated_protobuf_modules()
    return (
        importlib.import_module("cache_pb2"),
        importlib.import_module("cache_pb2_grpc"),
    )
