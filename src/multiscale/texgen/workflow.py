import re
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from ..common.utils import clean_float, format_number
from .geometry import PlainWeave, WeaveGeometry, calculate_geometry

VOXELS = (50, 50, 50)
ThicknessMode = Literal["total", "resin"]

# Standard placeholder properties matching the validated INP template (Mat0 / Mat1)
# so TexGen exports complete thermal-mechanical step blocks for later Abaqus runs.
PLACEHOLDER_MATRIX_PROPERTIES = {
    "young_mpa": 3000.0,
    "poisson": 0.2,
    "cte_per_k": 6.5e-6,
}

PLACEHOLDER_YARN_PROPERTIES = {
    "E1": 200000.0,
    "E2": 10000.0,
    "E3": 10000.0,
    "V12": 0.3,
    "V13": 0.4,
    "V23": 0.4,
    "G12": 5000.0,
    "G13": 5000.0,
    "G23": 5000.0,
    "CTE_X": -2e-7,
    "CTE_Y": 3e-6,
    "CTE_Z": 3e-6,
}


@dataclass(frozen=True)
class CaseInput:
    thickness_um: float
    mode: ThicknessMode = "total"
    dir_suffix: str = ""


@dataclass(frozen=True)
class ModelingCase:
    name: str
    total_thickness_um: float
    resin_thickness_um: float


@dataclass(frozen=True)
class ModelingBatch:
    fabric: PlainWeave
    geometry: WeaveGeometry
    cases: tuple[ModelingCase, ...]
    batch_name: str = ""
    voxels: tuple[int, int, int] = VOXELS


def format_thickness_token(thickness_um: float) -> str:
    return format_number(thickness_um).replace(".", "p")


def build_prefixed_dir_name(pp_model: str, suffix: str, *, field_label: str) -> str:
    clean = suffix.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", clean):
        raise ValueError(f"{field_label}仅支持英文字母、数字、点、横线和下划线。")
    prefix = f"{pp_model}_"
    if clean.casefold().startswith(prefix.casefold()):
        remainder = clean[len(prefix) :]
        if not remainder:
            raise ValueError(f"{field_label}在前缀 {prefix} 之后不能为空。")
        return f"{pp_model}_{remainder}"
    return f"{pp_model}_{clean}"


def resolve_thicknesses(
    value_um: float, mode: ThicknessMode, fabric_thickness_um: float
) -> tuple[float, float]:
    if not isfinite(fabric_thickness_um) or fabric_thickness_um <= 0:
        raise ValueError("织物厚度须大于 0 μm。")
    if mode == "total":
        if not isfinite(value_um) or value_um <= fabric_thickness_um:
            raise ValueError(
                f"PP 总厚度须大于织物厚度 {format_number(fabric_thickness_um)} μm。"
            )
        return clean_float(value_um), clean_float(value_um - fabric_thickness_um)
    if mode == "resin":
        if not isfinite(value_um) or value_um <= 0:
            raise ValueError("剩余树脂厚度须大于 0 μm。")
        return clean_float(fabric_thickness_um + value_um), clean_float(value_um)
    raise ValueError(f"不支持的厚度输入模式：{mode}")


def prepare_case(case: CaseInput, fabric: PlainWeave, index: int) -> ModelingCase:
    tag = f"工况 {index}"
    try:
        total_um, resin_um = resolve_thicknesses(
            case.thickness_um, case.mode, fabric.fabric_thickness_um
        )
    except ValueError as error:
        raise ValueError(f"{tag}：{error}") from error

    if case.dir_suffix.strip():
        name = build_prefixed_dir_name(
            fabric.name, case.dir_suffix, field_label=f"{tag}的子目录后缀"
        )
    else:
        name = f"{fabric.name}_{format_thickness_token(total_um)}"
    return ModelingCase(
        name=name,
        total_thickness_um=total_um,
        resin_thickness_um=resin_um,
    )


def prepare_batch(
    inputs: list[CaseInput], fabric: PlainWeave, batch_suffix: str = ""
) -> ModelingBatch:
    if not inputs:
        raise ValueError("请添加至少一个厚度工况。")

    batch_name = (
        build_prefixed_dir_name(fabric.name, batch_suffix, field_label="批次目录名")
        if batch_suffix.strip()
        else ""
    )

    cases: list[ModelingCase] = []
    names: set[str] = set()
    for index, case in enumerate(inputs, start=1):
        prepared = prepare_case(case, fabric, index)
        key = prepared.name.casefold()
        if key in names:
            raise ValueError(f"工况 {index} 的目录名与已有工况重复（{prepared.name}）。")
        names.add(key)
        cases.append(prepared)
    return ModelingBatch(
        fabric=fabric,
        geometry=calculate_geometry(fabric),
        cases=tuple(cases),
        batch_name=batch_name,
        voxels=VOXELS,
    )
