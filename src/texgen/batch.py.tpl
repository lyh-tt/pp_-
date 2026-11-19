# -*- coding: utf-8 -*-
# Run this file in TexGen using Python > Run Script.
# Geometry is in m, elastic moduli are supplied in MPa, and CTE is in /K.
# Placeholder Mat0/Mat1 values are written so the exported INP keeps thermal-mechanical steps;
# real resin and fiber-bundle properties are substituted later during Abaqus batch solving.
import os
from pathlib import Path

from TexGen.Core import *

BATCH = $batch
OUTPUT_ROOT = Path(__file__).resolve().parent


def export_case(case):
    geometry = BATCH["geometry_m"]
    warp_spacing = geometry["warp_spacing_m"]
    weft_spacing = geometry["weft_spacing_m"]
    thickness = case["total_thickness_um"] * 1e-6

    weave = CTextileWeave2D(2, 2, warp_spacing, thickness, False, True)
    weave.SetGeometryScale("m")
    weave.SwapPosition(0, 1)
    weave.SwapPosition(1, 0)
    # Warp runs along Y with spacing along X. Weft runs along X with spacing along Y.
    weave.SetYYarnWidths(geometry["warp_width_m"])
    weave.SetXYarnWidths(geometry["weft_width_m"])
    weave.SetYYarnSpacings(warp_spacing)
    weave.SetXYarnSpacings(weft_spacing)
    weave.SetYarnHeights(geometry["yarn_height_m"])
    weave.AssignDomain(CDomainPlanes(
        XYZ(0.0, 0.0, 0.0),
        XYZ(2 * warp_spacing, 2 * weft_spacing, thickness),
    ))

    materials = BATCH["placeholder_materials"]
    properties = materials["yarn"]
    for index in range(weave.GetNumYarns()):
        yarn = weave.GetYarn(index)
        yarn.SetFibresPerYarn(0)
        yarn.SetYoungsModulusX(properties["E1"], "MPa")
        yarn.SetYoungsModulusY(properties["E2"], "MPa")
        yarn.SetYoungsModulusZ(properties["E3"], "MPa")
        yarn.SetPoissonsRatioX(properties["V12"])
        yarn.SetPoissonsRatioY(properties["V13"])
        yarn.SetPoissonsRatioZ(properties["V23"])
        yarn.SetShearModulusXY(properties["G12"], "MPa")
        yarn.SetShearModulusXZ(properties["G13"], "MPa")
        yarn.SetShearModulusYZ(properties["G23"], "MPa")
        yarn.SetAlphaX(properties["CTE_X"], "/K")
        yarn.SetAlphaY(properties["CTE_Y"], "/K")
        yarn.SetAlphaZ(properties["CTE_Z"], "/K")

    matrix = materials["matrix"]
    weave.SetMatrixYoungsModulus(matrix["young_mpa"], "MPa")
    weave.SetMatrixPoissonsRatio(matrix["poisson"])
    weave.SetMatrixAlpha(matrix["cte_per_k"])
    textile_name = AddTextile(weave)
    textile = GetTextile(textile_name)

    folder = OUTPUT_ROOT / case["mesh_dir"]
    folder.mkdir(parents=True, exist_ok=True)
    serial = case["serial"]
    previous_dir = Path.cwd()
    # Pass ASCII file names to the native exporter even under a Unicode path.
    os.chdir(folder)
    try:
        SaveToXML(f"{serial}.tg3", textile_name, OUTPUT_STANDARD)
        mesh = CRectangularVoxelMesh("CPeriodicBoundaries")
        nx, ny, nz = BATCH["voxels"]
        mesh.SaveVoxelMesh(
            textile, f"{serial}.inp", nx, ny, nz, True, True, MATERIAL_CONTINUUM, 0
        )
    finally:
        os.chdir(previous_dir)

    for extension in ("inp", "ori", "eld", "tg3"):
        path = folder / f"{serial}.{extension}"
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("TexGen export missing or empty: " + str(path))
    print("Completed " + case["name"])


for case in BATCH["cases"]:
    export_case(case)
print("TexGen batch completed: " + str(OUTPUT_ROOT))
