from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..abaqus.batch import discover_texgen_models
from ..fiber.bundle import read_fiber_bundle_csv


@dataclass(frozen=True)
class ProjectLayout:
    """统一多尺度工程目录布局：三步结果与最终汇总集中管理。"""

    root: Path

    @property
    def fiber_dir(self) -> Path:
        return self.root / "01_fiber"

    @property
    def texgen_dir(self) -> Path:
        return self.root / "02_texgen"

    @property
    def rpt_dir(self) -> Path:
        return self.root / "03_rpt"

    @property
    def summary_csv(self) -> Path:
        return self.root / "effective_properties_summary.csv"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_latest_fiber_bundle_csv(layout: ProjectLayout) -> Path | None:
    """在 01_fiber 目录下（深度 <= 2）按修改时间查找最新的有效纤维束 CSV。"""
    base = layout.fiber_dir
    if not base.is_dir():
        return None

    candidates: list[Path] = []
    for pattern in ("*_fiber_bundle.csv", "fiber_bundle.csv"):
        for path in base.rglob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(base)
            if len(rel.parts) > 2 or any(part.startswith(".") for part in rel.parts):
                continue
            candidates.append(path.resolve())

    # 去重后按最新修改时间排序，并校验格式确保文件可读有效
    unique = list(dict.fromkeys(candidates))
    unique.sort(key=_mtime, reverse=True)
    for csv_path in unique:
        try:
            if read_fiber_bundle_csv(csv_path):
                return csv_path
        except (OSError, ValueError):
            continue
    return None


def find_latest_texgen_batch_dir(layout: ProjectLayout) -> Path | None:
    """在 02_texgen 目录下查找最新的 TexGen 批次目录（优先含有效模型，其次含建模脚本）。"""
    base = layout.texgen_dir
    if not base.is_dir():
        return None

    subdirs = sorted(
        (entry for entry in base.iterdir() if entry.is_dir() and not entry.name.startswith(".")),
        key=_mtime,
        reverse=True,
    )
    for sub in subdirs:
        if discover_texgen_models(sub):
            return sub.resolve()

    if discover_texgen_models(base):
        return base.resolve()

    for sub in subdirs:
        if (sub / "generated_scripts_parametric_modeling.py").is_file():
            return sub.resolve()
    return None
