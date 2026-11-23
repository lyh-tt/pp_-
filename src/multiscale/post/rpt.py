from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from ..common.utils import clean_float, natural_sort_key, read_text_with_fallback
from ..fiber.bundle import FiberBundleRow, read_fiber_bundle_csv

RPT_REQUIRED_KEYS = (
    "E1", "v12", "v13",
    "E2", "v21", "v23",
    "E3", "v31", "v32",
    "G23", "G31", "G12",
    "CTE_1", "CTE_2", "CTE_3",
)
_MODULUS_KEYS = ("E1", "E2", "E3", "G23", "G31", "G12")

RPT_SUMMARY_COLUMNS = (
    "case",
    "model",
    "fabric",
    "thickness_um",
    "resin",
    "resin_E_MPa",
    "resin_nu",
    "resin_CTE_ppm_per_K",
    "E1_MPa",
    "E2_MPa",
    "E3_MPa",
    "v12",
    "v13",
    "v21",
    "v23",
    "v31",
    "v32",
    "G12_MPa",
    "G13_MPa",
    "G23_MPa",
    "CTE_X_ppm_per_K",
    "CTE_Y_ppm_per_K",
    "CTE_Z_ppm_per_K",
    "source_rpt",
)

_PROP_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*:\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*$"
)
_MODEL_META_RE = re.compile(r"^(.+?)_(\d+(?:[p.]\d+)?)(?:um)?$", re.IGNORECASE)
_RPT_STEM_RE = re.compile(
    r"^pp(?P<e>\d+(?:p\d+)?)_(?P<cte>\d+(?:p\d+)?)e_(?P<exp>\d+)(?:_\d+|_chang)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RptSummaryRow:
    case: str
    model: str
    fabric: str
    thickness_um: float | None
    resin: str
    resin_e_mpa: float | None
    resin_nu: float | None
    resin_cte_ppm: float | None
    e1_mpa: float
    e2_mpa: float
    e3_mpa: float
    v12: float
    v13: float
    v21: float
    v23: float
    v31: float
    v32: float
    g12_mpa: float
    g13_mpa: float
    g23_mpa: float
    cte_x_ppm: float
    cte_y_ppm: float
    cte_z_ppm: float
    source_rpt: str

    def to_csv_dict(self) -> dict[str, object]:
        return {
            "case": self.case,
            "model": self.model,
            "fabric": self.fabric,
            "thickness_um": "" if self.thickness_um is None else self.thickness_um,
            "resin": self.resin,
            "resin_E_MPa": "" if self.resin_e_mpa is None else self.resin_e_mpa,
            "resin_nu": "" if self.resin_nu is None else self.resin_nu,
            "resin_CTE_ppm_per_K": "" if self.resin_cte_ppm is None else self.resin_cte_ppm,
            "E1_MPa": self.e1_mpa,
            "E2_MPa": self.e2_mpa,
            "E3_MPa": self.e3_mpa,
            "v12": self.v12,
            "v13": self.v13,
            "v21": self.v21,
            "v23": self.v23,
            "v31": self.v31,
            "v32": self.v32,
            "G12_MPa": self.g12_mpa,
            "G13_MPa": self.g13_mpa,
            "G23_MPa": self.g23_mpa,
            "CTE_X_ppm_per_K": self.cte_x_ppm,
            "CTE_Y_ppm_per_K": self.cte_y_ppm,
            "CTE_Z_ppm_per_K": self.cte_z_ppm,
            "source_rpt": self.source_rpt,
        }


def parse_rpt_file(path: Path) -> dict[str, float]:
    """解析单个 TexGen-Abaqus .rpt 文件中的 15 项均质化等效性能（原始 SI 单位）。"""
    text = read_text_with_fallback(Path(path))
    values: dict[str, float] = {}
    for line in text.splitlines():
        match = _PROP_LINE_RE.match(line)
        if not match:
            continue
        key, raw_num = match.group(1), match.group(2)
        if key in RPT_REQUIRED_KEYS:
            val = float(raw_num)
            if not isfinite(val):
                raise ValueError(f"{Path(path).name} 中的 {key} 数值无效")
            values[key] = val

    missing = [key for key in RPT_REQUIRED_KEYS if key not in values]
    if missing:
        raise ValueError(f"{Path(path).name} 缺少等效性能字段：{'、'.join(missing)}")
    if any(values[key] <= 0 for key in _MODULUS_KEYS):
        raise ValueError(f"{Path(path).name} 包含非正的弹性/剪切模量")
    return values


def _parse_model_meta(model_name: str) -> tuple[str, float | None]:
    match = _MODEL_META_RE.match(model_name.strip())
    if not match:
        return model_name, None
    fabric = match.group(1)
    thickness = clean_float(float(match.group(2).replace("p", ".")))
    return fabric, thickness


def _parse_rpt_stem_meta(stem: str) -> tuple[float | None, float | None]:
    match = _RPT_STEM_RE.match(stem.strip())
    if not match:
        return None, None
    e_mpa = clean_float(float(match.group("e").replace("p", ".")) * 1000.0)
    cte_coeff = float(match.group("cte").replace("p", "."))
    exp = int(match.group("exp"))
    cte_ppm = clean_float(cte_coeff * (10.0 ** (-exp)) * 1e6)
    return e_mpa, cte_ppm


def discover_rpt_files(root_dir: Path) -> list[Path]:
    """递归扫描目录下所有有效 .rpt 文件（忽略隐藏临时目录）。"""
    root = Path(root_dir).resolve()
    if not root.is_dir():
        return []
    found = [
        path
        for path in root.rglob("*.rpt")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    found.sort(key=lambda p: natural_sort_key(p.relative_to(root).as_posix()))
    return found


def _build_fiber_lookup(fiber_csv: Path | None) -> dict[str, FiberBundleRow]:
    if fiber_csv is None:
        return {}
    rows = read_fiber_bundle_csv(fiber_csv)
    lookup: dict[str, FiberBundleRow] = {}
    used: dict[str, int] = {}
    for row in rows:
        stem = row.rpt_stem()
        seen = used.get(stem, 0) + 1
        used[stem] = seen
        job_name = stem if seen == 1 else f"{stem}_{seen}"
        lookup[job_name] = row
        lookup.setdefault(stem, row)
    return lookup


def summarize_rpt_dir(
    rpt_root: Path,
    *,
    fiber_csv: Path | None = None,
) -> tuple[list[RptSummaryRow], list[str]]:
    """批量解析 RPT 目录并转换为工程单位（模量 MPa，CTE ppm/K），返回 (结果行, 告警信息)。"""
    root = Path(rpt_root).resolve()
    if not root.is_dir():
        raise ValueError(f"RPT 目录不存在：{root}")

    lookup = _build_fiber_lookup(fiber_csv)
    rpt_files = discover_rpt_files(root)
    rows: list[RptSummaryRow] = []
    warnings: list[str] = []

    for rpt_path in rpt_files:
        rel_str = rpt_path.relative_to(root).as_posix()
        try:
            props = parse_rpt_file(rpt_path)
        except (OSError, ValueError) as error:
            warnings.append(f"{rel_str}：{error}")
            continue

        model_name = rpt_path.parent.name if rpt_path.parent != root else root.name
        fabric, thickness_um = _parse_model_meta(model_name)

        matched = lookup.get(rpt_path.stem)
        if matched is not None:
            resin = matched.resin_name
            resin_e_mpa: float | None = clean_float(matched.e_mpa)
            resin_nu: float | None = clean_float(matched.poisson)
            resin_cte_ppm: float | None = clean_float(matched.cte_e5 * 10.0)
        else:
            resin = ""
            resin_e_mpa, resin_cte_ppm = _parse_rpt_stem_meta(rpt_path.stem)
            resin_nu = None

        rows.append(
            RptSummaryRow(
                case=f"{model_name}/{rpt_path.stem}",
                model=model_name,
                fabric=fabric,
                thickness_um=thickness_um,
                resin=resin,
                resin_e_mpa=resin_e_mpa,
                resin_nu=resin_nu,
                resin_cte_ppm=resin_cte_ppm,
                e1_mpa=clean_float(props["E1"] / 1e6),
                e2_mpa=clean_float(props["E2"] / 1e6),
                e3_mpa=clean_float(props["E3"] / 1e6),
                v12=clean_float(props["v12"]),
                v13=clean_float(props["v13"]),
                v21=clean_float(props["v21"]),
                v23=clean_float(props["v23"]),
                v31=clean_float(props["v31"]),
                v32=clean_float(props["v32"]),
                g12_mpa=clean_float(props["G12"] / 1e6),
                g13_mpa=clean_float(props["G31"] / 1e6),
                g23_mpa=clean_float(props["G23"] / 1e6),
                cte_x_ppm=clean_float(props["CTE_1"] * 1e6),
                cte_y_ppm=clean_float(props["CTE_2"] * 1e6),
                cte_z_ppm=clean_float(props["CTE_3"] * 1e6),
                source_rpt=rel_str,
            )
        )

    return rows, warnings


def export_rpt_summary_csv(rows: list[RptSummaryRow], output_csv: Path) -> Path:
    """将 RPT 汇总结果原子导出为 UTF-8-SIG 编码 CSV。"""
    if not rows:
        raise ValueError("没有可导出的 RPT 汇总数据。")
    target = Path(output_csv).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([row.to_csv_dict() for row in rows], columns=RPT_SUMMARY_COLUMNS)

    with TemporaryDirectory(dir=target.parent, prefix=".pp-rpt-") as stage:
        staged = Path(stage) / target.name
        frame.to_csv(staged, index=False, encoding="utf-8-sig")
        staged.replace(target)
    return target
