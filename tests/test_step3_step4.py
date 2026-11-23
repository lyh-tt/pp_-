import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from multiscale.abaqus.batch import (
    SolveConfig,
    discover_texgen_models,
    export_standalone_runner,
    patch_texgen_inp,
    plan_abaqus_tasks,
    run_abaqus_batch,
)
from multiscale.fiber.bundle import (
    BUNDLE_CSV_COLUMNS,
    FiberBundleRow,
    build_fiber_bundle_rows,
    export_fiber_bundle_csv,
    inspect_fiber_result_dir,
    read_fiber_bundle_csv,
    read_mechanical_constants,
)
from multiscale.fiber.rve import ResinProperty, write_sample_resin_csv
from multiscale.ui.pages.bundle_dialog import FiberBundleDialog
from multiscale.ui.pages.meso_page import MesoSolvePage
from multiscale.ui.window import ToolboxWindow

RPT_NAME_RE = re.compile(r"^pp(?P<e>[0-9p]+)_(?P<resin>[0-9p]+)e_\d+(?:_chang)?\.rpt$")


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    return app


def _sample_inp_text() -> str:
    return """*Heading
*Distribution, Location=Element, Table=TexGenOrientationVectors, Name=TexGenOrientationVectors, Input=1.ori
*Orientation, Name=TexGenOrientations, Definition=coordinates
TexGenOrientationVectors
*Material, Name=Mat0
*Elastic
3e+09, 0.2
*Expansion
6.5e-06
*Material, Name=Mat1
*Elastic, type=ENGINEERING CONSTANTS
2e+11, 1e+10, 1e+10, 0.3, 0.4, 0.4, 5e+09, 5e+09
5e+09
*Expansion, type=ORTHO
-2e-07, 3e-06, 3e-06
*Step, Name=Isothermal linear perturbation step, perturbation
*Static
*End Step
*Step, name=Thermomechanical step, perturbation
*Static
*End Step
"""


def _write_fake_abaqus(tool_dir: Path) -> str:
    tool_dir.mkdir(parents=True, exist_ok=True)
    py_path = tool_dir / "fake_abq.py"
    py_path.write_text(
        r'''import ast
import re
import sys
from pathlib import Path

args = sys.argv[1:]
if "cae" in args:
    nogui = next(a.split("=", 1)[1] for a in args if a.startswith("noGUI="))
    script_text = Path(nogui).read_text(encoding="utf-8")
    ast.parse(script_text)
    match = re.search(r"MODEL_NAME\s*=\s*(['\"])(.+?)\1", script_text)
    model_name = match.group(2)
    odb_meta = Path(model_name + ".odb").read_text(encoding="utf-8")
    Path(model_name + ".rpt").write_text(
        "Effective material properties:\nE1: 1.0000e+10\nCTE_1: 1.0000e-05\n" + odb_meta,
        encoding="utf-8",
    )
    sys.exit(0)

job = next(a.split("=", 1)[1] for a in args if a.startswith("job="))
inp = next(a.split("=", 1)[1] for a in args if a.startswith("input="))
inp_text = Path(inp).read_text(encoding="utf-8")
assert Path(job + ".ori").is_file()
assert f"Input={job}.ori" in inp_text
Path(job + ".sta").write_text("  1  1  1\n  2  1  1\n", encoding="utf-8")
Path(job + ".log").write_text("THE ANALYSIS HAS COMPLETED SUCCESSFULLY\n", encoding="utf-8")
mat0_match = re.search(r"\*Material, Name=Mat0\n\*Elastic\n([^\n]+)", inp_text)
Path(job + ".odb").write_text(f"MAT0_ELASTIC={mat0_match.group(1).strip()}\n", encoding="utf-8")
''',
        encoding="utf-8",
    )
    if os.name == "nt":
        cmd_path = tool_dir / "fake_abq.cmd"
        cmd_path.write_text(
            f'@echo off\nchcp 65001 >nul\n"{sys.executable}" "%~dp0fake_abq.py" %*\n',
            encoding="utf-8",
        )
        return str(cmd_path)
    sh_path = tool_dir / "fake_abq.sh"
    sh_path.write_text(f'#!/bin/sh\n"{sys.executable}" "{py_path}" "$@"\n', encoding="utf-8")
    sh_path.chmod(0o755)
    return str(sh_path)


def test_step3_bundle_csv_build_export_and_legacy_compat(tmp_path: Path) -> None:
    result_dir = tmp_path / "fiber_5um_test"
    result_dir.mkdir()
    write_sample_resin_csv(result_dir / "resin_inputs.csv")
    (result_dir / "Mechanical_constants.txt").write_text(
        "57278.09 32804.95 32773.01 0.244 0.244 0.398 14845.26 14845.39 15961.59 8.81e-6 3.19e-5 3.21e-5\n"
        "56023.36 9621.11 9523.32 0.234 0.235 0.545 4954.71 4954.80 7682.32 -4.31e-6 2.09e-5 2.15e-5\n",
        encoding="utf-8",
    )

    constants_path, resin_path = inspect_fiber_result_dir(result_dir)
    assert constants_path is not None and resin_path is not None
    constants = read_mechanical_constants(constants_path)
    assert len(constants) == 2

    resins = [
        ResinProperty(4604.51, 6.04967, 0.36, "S8NE"),
        ResinProperty(4030.56, 5.27133, 0.40, "H350"),
    ]
    rows = build_fiber_bundle_rows(resins, constants)
    assert [row.rpt_stem() for row in rows] == [
        "pp4p60451_6p04967e_5",
        "pp4p03056_5p27133e_5",
    ]
    for row in rows:
        assert RPT_NAME_RE.match(row.rpt_stem() + ".rpt")

    out_csv = export_fiber_bundle_csv(rows, tmp_path / "bundle.csv")
    frame = pd.read_csv(out_csv)
    assert list(frame.columns) == list(BUNDLE_CSV_COLUMNS)
    assert list(frame["Poisson"]) == pytest.approx([0.36, 0.40])
    # Single clean CSV only, no copied templates or temp folders left behind.
    assert [p.name for p in (tmp_path / "bundle.csv").parent.iterdir() if p.name.startswith(".pp-bundle-")] == []

    reloaded = read_fiber_bundle_csv(out_csv)
    assert reloaded == rows

    # Mismatched row count must fail cleanly.
    with pytest.raises(ValueError, match="不一致"):
        build_fiber_bundle_rows(resins[:1], constants)

    # Legacy 14-column CSV with trailing spaces uses default_poisson fallback.
    legacy_csv = tmp_path / "legacy.csv"
    legacy_csv.write_text(
        "E1MPa,CTE/e-5,E1,E2,E3,V12,V13,V23,G12,G13,G23,CTE_X,CTE_Y,CTE_Z\n"
        "6000,4.459,57273.15 ,33108.87 ,33107.82 ,0.244 ,0.244 ,0.393 ,14927.81 ,14927.00 ,16074.64 ,4.99e-6 ,1.15e-5 ,1.15e-5 \n",
        encoding="utf-8-sig",
    )
    legacy_rows = read_fiber_bundle_csv(legacy_csv, default_poisson=0.39)
    assert len(legacy_rows) == 1
    assert legacy_rows[0].poisson == pytest.approx(0.39)
    assert legacy_rows[0].e1 == pytest.approx(57273.15)


def test_step4_discovery_inp_patching_and_zero_residue_batch(tmp_path: Path) -> None:
    models_root = tmp_path / "texgen_batch"
    for name, legacy_nested in (("PP1080_80", False), ("PP1080_89", True)):
        model_dir = models_root / name / ("texgen" if legacy_nested else ".")
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "1.inp").write_text(_sample_inp_text(), encoding="utf-8")
        (model_dir / "1.ori").write_text("1, 1, 0, 0\n", encoding="utf-8")
        (model_dir / "1.eld").write_text("1, 1\n", encoding="utf-8")

    models = discover_texgen_models(models_root)
    assert [m.name for m in models] == ["PP1080_80", "PP1080_89"]

    row_a = FiberBundleRow(
        "S8NE", 4604.51, 6.04967, 0.36,
        57278.0, 32805.0, 32773.0, 0.244, 0.244, 0.398,
        14845.0, 14845.0, 15962.0, -2.5e-6, 3.1e-5, 3.2e-5,
    )
    row_b = FiberBundleRow(
        "H350", 4030.56, 5.27133, 0.41,
        56023.0, 9621.0, 9523.0, 0.234, 0.235, 0.545,
        4955.0, 4955.0, 7682.0, 4.3e-6, 2.0e-5, 2.1e-5,
    )

    patched_inp = tmp_path / "patched.inp"
    patch_texgen_inp(
        models[0].inp_path,
        patched_inp,
        row_a,
        {"1.ori": "custom.ori", "1.eld": "custom.eld"},
    )
    patched_text = patched_inp.read_text(encoding="utf-8")
    assert "Input=custom.ori" in patched_text
    assert "4.604510E+09, 0.36" in patched_text
    assert "6.049670E-05" in patched_text
    assert "5.727800E+10" in patched_text
    assert "-2.500000E-06, 3.100000E-05, 3.200000E-05" in patched_text

    # Run real subprocess fake Abaqus batch with max_parallel=2.
    fake_abq = _write_fake_abaqus(tmp_path / "tools")
    rpt_out = tmp_path / "rpt_out"
    tasks = plan_abaqus_tasks(models, [row_a, row_b], rpt_out)
    assert len(tasks) == 4

    config = SolveConfig(abaqus_command=fake_abq, cpus=2, max_parallel=2, retries=0, resume_existing=True)
    summary = run_abaqus_batch(tasks, config)
    assert summary.succeeded == 4
    assert summary.failed == 0

    # Verify ONLY clean <model>/<job>.rpt files exist in rpt_out (zero ODB/INP/temp clutter).
    produced = sorted(p.relative_to(rpt_out).as_posix() for p in rpt_out.rglob("*") if p.is_file())
    assert produced == [
        "PP1080_80/pp4p03056_5p27133e_5.rpt",
        "PP1080_80/pp4p60451_6p04967e_5.rpt",
        "PP1080_89/pp4p03056_5p27133e_5.rpt",
        "PP1080_89/pp4p60451_6p04967e_5.rpt",
    ]
    assert "MAT0_ELASTIC=4.604510E+09, 0.36" in (
        rpt_out / "PP1080_80" / "pp4p60451_6p04967e_5.rpt"
    ).read_text(encoding="utf-8")

    # Second run skips existing RPTs (resume_existing=True).
    resumed = run_abaqus_batch(tasks, config)
    assert resumed.skipped == 4
    assert resumed.succeeded == 0

    # Standalone runner script compiles cleanly under Python 3.9 syntax and runs end-to-end.
    bundle_csv = export_fiber_bundle_csv([row_a, row_b], tmp_path / "fiber_bundle.csv")
    standalone_out = tmp_path / "standalone_rpt_out"
    runner = export_standalone_runner(
        tmp_path / "run_abaqus_batch.py",
        models_dir=models_root,
        fiber_csv=bundle_csv,
        output_dir=standalone_out,
        config=config,
    )
    ast.parse(runner.read_text(encoding="utf-8"), feature_version=(3, 9))
    proc = subprocess.run([sys.executable, str(runner)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    standalone_files = sorted(
        p.relative_to(standalone_out).as_posix() for p in standalone_out.rglob("*") if p.is_file()
    )
    assert standalone_files == produced


def test_step3_and_step4_ui_offscreen(tmp_path: Path) -> None:
    app = _qt_app()
    settings = QSettings()
    tracked_keys = ("bundle_result_dir",)
    saved_state = {
        key: (settings.contains(key), settings.value(key, "", type=str))
        for key in tracked_keys
    }
    window = ToolboxWindow(default_output=tmp_path)
    try:
        # Step 1 bundle dialog: load Fiber RVE result dir and generate single CSV.
        fiber_dir = tmp_path / "01_fiber" / "fiber_5um_demo"
        fiber_dir.mkdir(parents=True)
        write_sample_resin_csv(fiber_dir / "resin_inputs.csv")
        (fiber_dir / "Mechanical_constants.txt").write_text(
            "57000 27400 27500 0.24 0.24 0.41 12000 12100 14000 5e-6 1.2e-5 1.3e-5\n"
            "56000 26400 26500 0.23 0.23 0.42 11000 11100 13000 4e-6 1.1e-5 1.2e-5\n",
            encoding="utf-8",
        )

        bundle_dlg = FiberBundleDialog(window.workspace.layout.fiber_dir, window.page("fiber_rve"))
        try:
            bundle_dlg.load_result_dir(fiber_dir)
            assert bundle_dlg.preview_table.rowCount() == 2
            assert "已匹配 2 组" in bundle_dlg.summary_badge.text()

            csv_out = bundle_dlg.generate()
            assert csv_out is not None and csv_out.is_file()
        finally:
            bundle_dlg.close()
            bundle_dlg.deleteLater()

        # Notify upstream fiber CSV ready, then prepare TexGen models dir and verify Step 3 UI.
        window.workspace.notify_fiber_csv(csv_out)
        texgen_dir = tmp_path / "02_texgen" / "PP1080_batch"
        model_dir = texgen_dir / "PP1080_80"
        model_dir.mkdir(parents=True)
        (model_dir / "1.inp").write_text(_sample_inp_text(), encoding="utf-8")
        (model_dir / "1.ori").write_text("1, 1, 0, 0\n", encoding="utf-8")

        window.select_module("meso_solve")
        meso_page = window.page("meso_solve")
        assert isinstance(meso_page, MesoSolvePage)
        assert Path(meso_page.csv_path_edit.text()) == csv_out.resolve()
        meso_page.load_models_dir(texgen_dir)
        assert meso_page.summary_badge.text() == "1 模型 × 2 材料 = 2 任务"

        meso_page.abaqus_edit.setText("abq2026")
        runner = meso_page.export_script_to(tmp_path / "run_batch.py")
        assert runner.is_file()
        assert "abq2026" in runner.read_text(encoding="utf-8")
    finally:
        for key, (had_key, old_val) in saved_state.items():
            if had_key:
                settings.setValue(key, old_val)
            else:
                settings.remove(key)
        window.close()
        window.deleteLater()
        app.processEvents()
