# -*- coding: utf-8 -*-
"""统一从 7 个 Fiber 方向 ODB 中提取等效性能，并直接合并树脂参数写出 16 列纤维束 CSV。"""
from __future__ import print_function

import csv
import sys

from abaqusConstants import NODAL
from odbAccess import openOdb
import numpy as np

CELL_SIZE_MM = $cell_size_mm
ODB_MAP = $odb_map
RESIN_ROWS = $resin_rows
OUTPUT_CSV = $output_csv

BUNDLE_HEADERS = (
    "Resin Name", "E1MPa", "CTE/e-5", "Poisson",
    "E1", "E2", "E3", "V12", "V13", "V23",
    "G12", "G13", "G23", "CTE_X", "CTE_Y", "CTE_Z",
)


def _sum_rf(frame, nset, axis):
    subset = frame.fieldOutputs["RF"].getSubset(region=nset, position=NODAL)
    total = 0.0
    for v in subset.values:
        total += float(v.data[axis])
    return total


def _sum_u(frame, nset, axis):
    subset = frame.fieldOutputs["U"].getSubset(region=nset, position=NODAL)
    total = 0.0
    for v in subset.values:
        total += float(v.data[axis])
    return total


def _corner_coord(nset):
    return nset.nodes[0][0].coordinates


def _extract_e11(odb_path, L, H, W, dispx):
    odb = openOdb(odb_path, readOnly=True)
    try:
        step = odb.steps["Step-1"]
        rows = len(step.frames) - 1
        data = np.zeros((rows, 12))
        rp4 = odb.rootAssembly.nodeSets["RP4"]
        c1 = odb.rootAssembly.nodeSets["C1"]
        c2 = odb.rootAssembly.nodeSets["C2"]
        c4 = odb.rootAssembly.nodeSets["C4"]
        c5 = odb.rootAssembly.nodeSets["C5"]
        p1, p2, p4, p5 = _corner_coord(c1), _corner_coord(c2), _corner_coord(c4), _corner_coord(c5)
        for idx in range(1, len(step.frames)):
            frame = step.frames[idx]
            e11 = abs(_sum_rf(frame, rp4, 0) / (W * H)) / (dispx / L)
            du1 = abs(L - abs((p1[0] + _sum_u(frame, c1, 0)) - (p2[0] + _sum_u(frame, c2, 0))))
            du2 = abs(H - abs((p1[1] + _sum_u(frame, c1, 1)) - (p5[1] + _sum_u(frame, c5, 1))))
            du3 = abs(W - abs((p1[2] + _sum_u(frame, c1, 2)) - (p4[2] + _sum_u(frame, c4, 2))))
            data[idx - 1, 0] = e11
            data[idx - 1, 3] = (du2 / H) / (du1 / L)
            data[idx - 1, 4] = (du3 / W) / (du1 / L)
        return data
    finally:
        odb.close()


def _extract_e22(odb_path, data, L, H, W, dispy):
    odb = openOdb(odb_path, readOnly=True)
    try:
        step = odb.steps["Step-1"]
        rp5 = odb.rootAssembly.nodeSets["RP5"]
        c1 = odb.rootAssembly.nodeSets["C1"]
        c4 = odb.rootAssembly.nodeSets["C4"]
        c5 = odb.rootAssembly.nodeSets["C5"]
        p1, p4, p5 = _corner_coord(c1), _corner_coord(c4), _corner_coord(c5)
        for idx in range(1, len(step.frames)):
            frame = step.frames[idx]
            data[idx - 1, 1] = abs(_sum_rf(frame, rp5, 1) / (W * L)) / (dispy / H)
            du2 = abs(H - abs((p1[1] + _sum_u(frame, c1, 1)) - (p5[1] + _sum_u(frame, c5, 1))))
            du3 = abs(W - abs((p1[2] + _sum_u(frame, c1, 2)) - (p4[2] + _sum_u(frame, c4, 2))))
            data[idx - 1, 5] = (du3 / W) / (du2 / H)
    finally:
        odb.close()


def _extract_scalar(odb_path, data, col, nset_name, rf_axis, area, strain):
    odb = openOdb(odb_path, readOnly=True)
    try:
        step = odb.steps["Step-1"]
        nset = odb.rootAssembly.nodeSets[nset_name]
        for idx in range(1, len(step.frames)):
            force = _sum_rf(step.frames[idx], nset, rf_axis)
            data[idx - 1, col] = abs(force / area) / strain
    finally:
        odb.close()


def _extract_cte(odb_path, data, L, H, W, delta_t):
    odb = openOdb(odb_path, readOnly=True)
    try:
        step = odb.steps["Step-1"]
        c1 = odb.rootAssembly.nodeSets["C1"]
        c2 = odb.rootAssembly.nodeSets["C2"]
        c4 = odb.rootAssembly.nodeSets["C4"]
        c5 = odb.rootAssembly.nodeSets["C5"]
        for idx in range(1, len(step.frames)):
            frame = step.frames[idx]
            data[idx - 1, 9] = abs(_sum_u(frame, c1, 0) - _sum_u(frame, c2, 0)) / (L * delta_t)
            data[idx - 1, 10] = abs(_sum_u(frame, c1, 1) - _sum_u(frame, c5, 1)) / (H * delta_t)
            data[idx - 1, 11] = abs(_sum_u(frame, c1, 2) - _sum_u(frame, c4, 2)) / (W * delta_t)
    finally:
        odb.close()


def _csv_cell(val):
    if sys.version_info[0] < 3:
        try:
            unicode_type = unicode
        except NameError:
            return str(val)
        if isinstance(val, unicode_type):
            return val.encode("utf-8")
    return str(val)


def _write_bundle_csv(output_csv, resin_rows, data):
    """兼容 Abaqus Python 2/3 直接写出 UTF-8-SIG 16 列纤维束等效性能 CSV。"""
    if len(resin_rows) != data.shape[0]:
        raise RuntimeError(
            "Resin row count (%d) does not match ODB incremental frames (%d)."
            % (len(resin_rows), data.shape[0])
        )

    if sys.version_info[0] >= 3:
        handle = open(output_csv, "w", encoding="utf-8-sig", newline="")
        need_bom = False
    else:
        handle = open(output_csv, "wb")
        need_bom = True

    try:
        if need_bom:
            handle.write(b"\xef\xbb\xbf")
        writer = csv.writer(handle)
        writer.writerow([_csv_cell(col) for col in BUNDLE_HEADERS])
        for r in range(data.shape[0]):
            name, e_str, cte_str, nu_str = resin_rows[r]
            props = ["%.10g" % float(data[r, c]) for c in range(12)]
            writer.writerow([_csv_cell(x) for x in [name, e_str, cte_str, nu_str] + props])
    finally:
        handle.close()


def main():
    L = H = W = float(CELL_SIZE_MM)
    dispx, dispy, dispz = L * 0.2, H * 0.2, W * 0.2

    # 依次提取各方向 ODB，每次提取后立刻关闭释放内存（天然兼容 1 组或多组树脂）
    data = _extract_e11(ODB_MAP["E11"], L, H, W, dispx)
    _extract_e22(ODB_MAP["E22"], data, L, H, W, dispy)
    _extract_scalar(ODB_MAP["E33"], data, 2, "RP6", 2, H * L, dispz / W)
    _extract_scalar(ODB_MAP["G12"], data, 6, "RP4", 0, W * L, (dispx / H) + (dispy / L))
    _extract_scalar(ODB_MAP["G13"], data, 7, "RP5", 0, H * L, (dispx / W) + (dispz / L))
    _extract_scalar(ODB_MAP["G23"], data, 8, "RP6", 1, L * H, (dispy / W) + (dispz / H))
    _extract_cte(ODB_MAP["CTE"], data, L, H, W, 100.0)

    _write_bundle_csv(OUTPUT_CSV, RESIN_ROWS, data)


main()
