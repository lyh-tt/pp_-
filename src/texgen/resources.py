import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def app_root() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def package_resource(relative_path: str) -> Path:
    package_dir = Path(__file__).resolve().parent
    if is_frozen():
        frozen_path = Path(sys._MEIPASS) / "texgen" / relative_path  # type: ignore[attr-defined]
        if frozen_path.exists():
            return frozen_path
    return package_dir / relative_path
