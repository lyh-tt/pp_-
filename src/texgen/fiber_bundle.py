from __future__ import annotations

import csv
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from .fiber_rve import ResinProperty, _read_text_with_fallback
from .workflow import clean_float, format_number

RESIN_COLUMNS = ("Resin Name", "E1MPa", "CTE/e-5", "Poisson")
FIBER_RESULT_COLUMNS = (
    "E1",
    "E2",
    "E3",
    "V12",
    "V13",
    "V23",
    "G12",
    "G13",
    "G23",
    "CTE_X",
    "CTE_Y",
    "CTE_Z",
)
BUNDLE_CSV_COLUMNS = RESIN_COLUMNS + FIBER_RESULT_COLUMNS
_POSITIVE_MODULUS_INDICES = (0, 1, 2, 6, 7, 8)


@dataclass(frozen=True)
class FiberBundleRow:
    resin_name: str
    e_mpa: float
    cte_e5: float
    poisson: float
    e1: float
    e2: float
    e3: float
    v12: float
    v13: float
    v23: float
    g12: float
    g13: float
    g23: float
    cte_x: float
    cte_y: float
    cte_z: float

    def __post_init__(self) -> None:
        if not isfinite(self.e_mpa) or self.e_mpa <= 0:
            raise ValueError("树脂模量 E1MPa 须大于 0。")
        if not isfinite(self.cte_e5):
            raise ValueError("树脂热膨胀系数 CTE/e-5 须为有限数值。")
        if not isfinite(self.poisson) or not (-1.0 < self.poisson < 0.5):
            raise ValueError("树脂泊松比 Poisson 须介于 -1 和 0.5 之间。")
        numeric = (
            self.e1,
            self.e2,
            self.e3,
            self.v12,
            self.v13,
            self.v23,
            self.g12,
            self.g13,
            self.g23,
            self.cte_x,
            self.cte_y,
            self.cte_z,
        )
        if any(not isfinite(value) for value in numeric):
            raise ValueError("纤维束等效性能存在非有限数值。")
        if any(value <= 0 for value in (self.e1, self.e2, self.e3, self.g12, self.g13, self.g23)):
            raise ValueError("纤维束等效弹性模量和剪切模量必须大于 0。")
        clean_name = self.resin_name.strip()
        if clean_name != self.resin_name:
            object.__setattr__(self, "resin_name", clean_name)

    def rpt_stem(self) -> str:
        e_token = format_number(clean_float(self.e_mpa / 1000.0)).replace(".", "p")
        cte_token = format_number(self.cte_e5).replace(".", "p")
        return f"pp{e_token}_{cte_token}e_5"

    def to_csv_dict(self) -> dict[str, object]:
        return {
            "Resin Name": self.resin_name,
            "E1MPa": self.e_mpa,
            "CTE/e-5": self.cte_e5,
            "Poisson": self.poisson,
            "E1": self.e1,
            "E2": self.e2,
            "E3": self.e3,
            "V12": self.v12,
            "V13": self.v13,
            "V23": self.v23,
            "G12": self.g12,
            "G13": self.g13,
            "G23": self.g23,
            "CTE_X": self.cte_x,
            "CTE_Y": self.cte_y,
            "CTE_Z": self.cte_z,
        }


def inspect_fiber_result_dir(result_dir: Path) -> tuple[Path | None, Path | None]:
    root = Path(result_dir)
    if not root.is_dir():
        return None, None
    constants = root / "Mechanical_constants.txt"
    resins = root / "resin_inputs.csv"
    return (
        constants if constants.is_file() else None,
        resins if resins.is_file() else None,
    )


def read_mechanical_constants(path: Path) -> list[tuple[float, ...]]:
    text = _read_text_with_fallback(Path(path))
    rows: list[tuple[float, ...]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 12:
            raise ValueError(
                f"{Path(path).name} 第 {line_no} 行只有 {len(parts)} 列，至少需要 12 列等效性能数据。"
            )
        try:
            values = tuple(float(part) for part in parts[:12])
        except ValueError as error:
            raise ValueError(f"{Path(path).name} 第 {line_no} 行包含非数字内容。") from error

        if any(not isfinite(val) for val in values):
            raise ValueError(f"{Path(path).name} 第 {line_no} 行包含无效数值。")
        if any(values[idx] <= 0 for idx in _POSITIVE_MODULUS_INDICES):
            raise ValueError(
                f"{Path(path).name} 第 {line_no} 行的等效模量须大于 0（请确认 Abaqus 求解已完成）。"
            )
        rows.append(values)

    if not rows:
        raise ValueError(f"{Path(path).name} 中没有有效数据行。")
    return rows


def build_fiber_bundle_rows(
    resins: list[ResinProperty],
    constants: list[tuple[float, ...]],
) -> list[FiberBundleRow]:
    if not resins:
        raise ValueError("树脂参数不能为空。")
    if not constants:
        raise ValueError("Mechanical_constants.txt 数据不能为空。")
    if len(resins) != len(constants):
        raise ValueError(
            f"树脂参数行数（{len(resins)}）与 Mechanical_constants.txt 行数（{len(constants)}）不一致，"
            "无法一一对应。"
        )

    return [
        FiberBundleRow(
            resin_name=resin.name,
            e_mpa=resin.e_mpa,
            cte_e5=resin.cte_e5,
            poisson=resin.poisson,
            e1=const[0],
            e2=const[1],
            e3=const[2],
            v12=const[3],
            v13=const[4],
            v23=const[5],
            g12=const[6],
            g13=const[7],
            g23=const[8],
            cte_x=const[9],
            cte_y=const[10],
            cte_z=const[11],
        )
        for resin, const in zip(resins, constants)
    ]


def export_fiber_bundle_csv(rows: list[FiberBundleRow], output_csv: Path) -> Path:
    if not rows:
        raise ValueError("没有可导出的纤维束等效性能数据。")
    target = Path(output_csv).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([row.to_csv_dict() for row in rows], columns=BUNDLE_CSV_COLUMNS)

    with TemporaryDirectory(dir=target.parent, prefix=".pp-bundle-") as temporary:
        staged = Path(temporary) / target.name
        frame.to_csv(staged, index=False, encoding="utf-8-sig")
        staged.replace(target)
    return target


def read_fiber_bundle_csv(
    path: Path, *, default_poisson: float = 0.38
) -> list[FiberBundleRow]:
    if not isfinite(default_poisson) or not (-1.0 < default_poisson < 0.5):
        raise ValueError("默认树脂泊松比须介于 -1 和 0.5 之间。")

    content = _read_text_with_fallback(Path(path))
    raw_rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(content.splitlines())
        if any(cell.strip() for cell in row)
    ]
    if len(raw_rows) < 2:
        raise ValueError("纤维束等效性能 CSV 至少需要表头和 1 行数据。")

    headers = [col.strip() for col in raw_rows[0]]
    index_by_name = {col: idx for idx, col in enumerate(headers)}
    required = ("E1MPa", "CTE/e-5", *FIBER_RESULT_COLUMNS)
    missing = [col for col in required if col not in index_by_name]
    if missing:
        raise ValueError("纤维束 CSV 缺少列：" + "、".join(missing))

    name_idx = index_by_name.get("Resin Name")
    poisson_idx = index_by_name.get("Poisson")

    bundle_rows: list[FiberBundleRow] = []
    for row_no, cells in enumerate(raw_rows[1:], start=1):
        try:
            def _num(col_name: str) -> float:
                col_idx = index_by_name[col_name]
                if col_idx >= len(cells) or not cells[col_idx]:
                    raise ValueError(f"缺少 {col_name}")
                return float(cells[col_idx])

            resin_name = (
                cells[name_idx] if name_idx is not None and name_idx < len(cells) else ""
            )
            if poisson_idx is not None and poisson_idx < len(cells) and cells[poisson_idx]:
                poisson = float(cells[poisson_idx])
            else:
                poisson = default_poisson

            bundle_rows.append(
                FiberBundleRow(
                    resin_name=resin_name,
                    e_mpa=_num("E1MPa"),
                    cte_e5=_num("CTE/e-5"),
                    poisson=poisson,
                    e1=_num("E1"),
                    e2=_num("E2"),
                    e3=_num("E3"),
                    v12=_num("V12"),
                    v13=_num("V13"),
                    v23=_num("V23"),
                    g12=_num("G12"),
                    g13=_num("G13"),
                    g23=_num("G23"),
                    cte_x=_num("CTE_X"),
                    cte_y=_num("CTE_Y"),
                    cte_z=_num("CTE_Z"),
                )
            )
        except ValueError as error:
            raise ValueError(f"纤维束 CSV 第 {row_no} 条数据无效：{error}") from error

    return bundle_rows
