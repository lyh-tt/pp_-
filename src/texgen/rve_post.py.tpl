# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys
import visualization

MODEL_NAME = $model_name


def compute_effective_properties(model_name):
    odb = visualization.openOdb(path=model_name + ".odb", readOnly=True)
    try:
        steps = list(odb.steps.values())
        if len(steps) < 2:
            raise RuntimeError("ODB does not contain mechanical and thermomechanical steps: " + model_name)
        iso_step = steps[0]
        thm_step = steps[1]

        frame_fx = iso_step.frames[1]
        frame_fy = iso_step.frames[2]
        frame_fz = iso_step.frames[3]
        frame_xy = iso_step.frames[4]
        frame_zx = iso_step.frames[5]
        frame_yz = iso_step.frames[6]
        frame_th = thm_step.frames[1]

        fx_x = frame_fx.fieldOutputs["U"].values[0].data[0]
        fx_y = frame_fx.fieldOutputs["U"].values[1].data[0]
        fx_z = frame_fx.fieldOutputs["U"].values[2].data[0]

        fy_x = frame_fy.fieldOutputs["U"].values[0].data[0]
        fy_y = frame_fy.fieldOutputs["U"].values[1].data[0]
        fy_z = frame_fy.fieldOutputs["U"].values[2].data[0]

        fz_x = frame_fz.fieldOutputs["U"].values[0].data[0]
        fz_y = frame_fz.fieldOutputs["U"].values[1].data[0]
        fz_z = frame_fz.fieldOutputs["U"].values[2].data[0]

        g_xy = frame_xy.fieldOutputs["U"].values[3].data[0]
        g_zx = frame_zx.fieldOutputs["U"].values[4].data[0]
        g_yz = frame_yz.fieldOutputs["U"].values[5].data[0]

        th_x = frame_th.fieldOutputs["U"].values[0].data[0]
        th_y = frame_th.fieldOutputs["U"].values[1].data[0]
        th_z = frame_th.fieldOutputs["U"].values[2].data[0]
    finally:
        odb.close()

    return (
        1.0 / fx_x,
        -fx_y / fx_x,
        -fx_z / fx_x,
        1.0 / fy_y,
        -fy_x / fy_y,
        -fy_z / fy_y,
        1.0 / fz_z,
        -fz_x / fz_z,
        -fz_y / fz_z,
        1.0 / g_yz,
        1.0 / g_zx,
        1.0 / g_xy,
        fx_x,
        fx_y,
        fx_z,
        fy_x,
        fy_y,
        fy_z,
        fz_x,
        fz_y,
        fz_z,
        g_yz,
        g_zx,
        g_xy,
        th_x,
        th_y,
        th_z,
    )


def write_report(model_name, props):
    report_path = model_name + ".rpt"
    lines = [
        "This file provides the equivalent material properties of a simulated unit-cell with appropriate theoretical boundary-conditions set-up.",
        "",
        "",
        "Output results:",
        "",
        "Effective compliance matrix:",
        "%1.3e\t%1.3e\t%1.3e\t\t0\t\t\t0\t\t\t0\t\t" % (props[12], props[15], props[18]),
        "%1.3e\t%1.3e\t%1.3e\t\t0\t\t\t0\t\t\t0\t\t" % (props[13], props[16], props[19]),
        "%1.3e\t%1.3e\t%1.3e\t\t0\t\t\t0\t\t\t0\t\t" % (props[14], props[17], props[20]),
        "\t0\t\t\t0\t\t\t0\t\t%1.3e\t\t0\t\t\t0\t\t" % props[21],
        "\t0\t\t\t0\t\t\t0\t\t\t0\t\t%1.3e\t\t0\t\t" % props[22],
        "\t0\t\t\t0\t\t\t0\t\t\t0\t\t\t0\t\t%1.3e" % props[23],
        "",
        "",
        "Effective material properties:",
        "E1: %1.4e" % props[0],
        "v12: %1.4f" % props[1],
        "v13: %1.4f" % props[2],
        "E2: %1.4e" % props[3],
        "v21: %1.4f" % props[4],
        "v23: %1.4f" % props[5],
        "E3: %1.4e" % props[6],
        "v31: %1.4f" % props[7],
        "v32: %1.4f" % props[8],
        "G23: %1.4e" % props[9],
        "G31: %1.4e" % props[10],
        "G12: %1.4e" % props[11],
        "CTE_1: %1.4e" % props[24],
        "CTE_2: %1.4e" % props[25],
        "CTE_3: %1.4e" % props[26],
        "",
    ]
    with open(report_path, "w") as out:
        out.write("\n".join(lines))


def main():
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.append(cwd)
    props = compute_effective_properties(MODEL_NAME)
    write_report(MODEL_NAME, props)


main()
