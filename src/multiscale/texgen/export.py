from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from pprint import pformat
from string import Template
from tempfile import TemporaryDirectory

import pandas as pd

from ..common.resources import package_resource
from .workflow import (
    PLACEHOLDER_MATRIX_PROPERTIES,
    PLACEHOLDER_YARN_PROPERTIES,
    ModelingBatch,
)

SCRIPT_NAME = "generated_scripts_parametric_modeling.py"


def export_batch(batch: ModelingBatch, parent: Path) -> Path:
    parent = parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    if batch.batch_name:
        destination = parent / batch.batch_name
        if destination.exists():
            raise ValueError(
                f"目标目录已存在：{destination.name}。请更换自定义目录名或留空按时间自动命名。"
            )
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = parent / f"{batch.fabric.name}_TexGen_{timestamp}"

    geometry = batch.geometry
    cases = [
        {**asdict(case), "serial": serial, "mesh_dir": case.name}
        for serial, case in enumerate(batch.cases, start=1)
    ]
    script_data = {
        "pp_model": batch.fabric.name,
        "geometry_m": asdict(geometry),
        "voxels": batch.voxels,
        "placeholder_materials": {
            "matrix": dict(PLACEHOLDER_MATRIX_PROPERTIES),
            "yarn": dict(PLACEHOLDER_YARN_PROPERTIES),
        },
        "cases": cases,
    }
    template = Template(package_resource("texgen/templates/batch.py.tpl").read_text(encoding="utf-8"))
    script = template.substitute(batch=pformat(script_data, sort_dicts=False))
    compile(script, SCRIPT_NAME, "exec")

    geometry_rows = [
        {
            "serial number": serial,
            "Case Name": case.name,
            "PP Model": batch.fabric.name,
            "Warp Width (mm)": batch.fabric.warp_width_mm,
            "Warp Spacing (mm)": batch.fabric.warp_spacing_mm,
            "Weft Width (mm)": batch.fabric.weft_width_mm,
            "Weft Spacing (mm)": batch.fabric.weft_spacing_mm,
            "Fabric Thickness (um)": batch.fabric.fabric_thickness_um,
            "Extra Resin Thickness (um)": case.resin_thickness_um,
            "PP Total Thickness (um)": case.total_thickness_um,
        }
        for serial, case in enumerate(batch.cases, start=1)
    ]

    with TemporaryDirectory(dir=parent, prefix=".pp-texgen-") as temporary:
        staged = Path(temporary) / destination.name
        staged.mkdir()
        (staged / SCRIPT_NAME).write_text(script, encoding="utf-8")
        pd.DataFrame(geometry_rows).to_csv(
            staged / "yarn_parameter_combinations.csv", index=False, encoding="utf-8-sig"
        )
        for case in cases:
            (staged / case["mesh_dir"]).mkdir(parents=True)
        staged.rename(destination)
    return destination / SCRIPT_NAME
