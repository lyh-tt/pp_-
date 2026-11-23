import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def app_root() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # common/resources.py 位于 src/multiscale/common/下，parents[3] 为仓库根目录
    return Path(__file__).resolve().parents[3]


def package_resource(relative_path: str) -> Path:
    package_dir = Path(__file__).resolve().parents[1]
    if is_frozen():
        frozen_path = Path(sys._MEIPASS) / "multiscale" / relative_path  # type: ignore[attr-defined]
        if frozen_path.exists():
            return frozen_path
    return package_dir / relative_path
