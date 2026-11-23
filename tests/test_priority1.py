import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from multiscale.abaqus.batch import (
    SolveConfig,
    TexGenModel,
    export_standalone_runner,
    plan_abaqus_tasks,
    run_abaqus_batch,
)
from multiscale.common import abaqus_utils
from multiscale.common.abaqus_utils import (
    auto_per_job_memory_mb,
    detect_total_memory_mb,
    is_ascii_path,
    memory_cli_args,
    non_ascii_path_warning,
    safe_ascii_temp_dir,
)
from multiscale.fiber.bundle import FiberBundleRow, export_fiber_bundle_csv, read_fiber_bundle_csv
from multiscale.fiber.rve import (
    ResinProperty,
    discover_fiber_templates,
    generate_fiber_cases,
)
from multiscale.fiber.solver import FiberSolveConfig, run_fiber_solve_batch
from multiscale.post.rpt import (
    RPT_SUMMARY_COLUMNS,
    export_rpt_summary_csv,
    parse_rpt_file,
    summarize_rpt_dir,
)
from multiscale.ui.pages.meso_page import MesoSolvePage
from multiscale.ui.window import ToolboxWindow
from tests.test_fiber_rve import _make_synthetic_template


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    return app


def _write_fake_fiber_abaqus(tool_dir: Path, expected_memory_mb: int | None) -> str:
    tool_dir.mkdir(parents=True, exist_ok=True)
    py_path = tool_dir / "fake_fiber_abq.py"
    expected_arg = repr(f"memory={expected_memory_mb}mb" if expected_memory_mb else "")
    py_path.write_text(
        rf'''import ast
import os
import re
import sys
from pathlib import Path

args = sys.argv[1:]
cwd = os.getcwd()
assert cwd.isascii(), f"Abaqus cwd must be ASCII, got: {{cwd}}"

if "cae" in args:
    nogui = next(a.split("=", 1)[1] for a in args if a.startswith("noGUI="))
    script_text = Path(nogui).read_text(encoding="utf-8")
    ast.parse(script_text)
    if nogui == "extract_fiber.py":
        e11_text = Path("job-E11/job-E11.odb").read_text(encoding="utf-8")
        nrows = int(re.search(r"NROWS=(\d+)", e11_text).group(1))
        out_lines = [
            "5.68e+04\t2.83e+04\t2.83e+04\t0.216\t0.216\t0.278\t1.43e+04\t1.43e+04\t1.39e+04\t4.42e-06\t9.46e-06\t9.46e-06"
            for _ in range(nrows)
        ]
        Path("Mechanical_constants.txt").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    else:
        match = re.search(r"MODEL_NAME\s*=\s*(['\"])(.+?)\1", script_text)
        model_name = match.group(2)
        Path(model_name + ".rpt").write_text(
            "Effective material properties:\n"
            "E1: 1.6928e+10\nv12: 0.1215\nv13: 0.2096\n"
            "E2: 1.8713e+10\nv21: 0.1343\nv23: 0.2058\n"
            "E3: 8.2307e+09\nv31: 0.1019\nv32: 0.0905\n"
            "G23: 3.2066e+09\nG31: 3.2481e+09\nG12: 4.8510e+09\n"
            "CTE_1: 1.7132e-05\nCTE_2: 1.5303e-05\nCTE_3: 4.2350e-05\n",
            encoding="utf-8",
        )
    sys.exit(0)

job = next(a.split("=", 1)[1] for a in args if a.startswith("job="))
inp = next(a.split("=", 1)[1] for a in args if a.startswith("input="))
expected_mem = {expected_arg}
if expected_mem:
    assert expected_mem in args, f"Expected {{expected_mem}}, got: {{args}}"
inp_text = Path(inp).read_text(encoding="utf-8")
m_inc = re.search(r"\*Static,\s*direct\s*\n\s*1\.,\s*(\d+)\.", inp_text)
n_inc = int(m_inc.group(1)) if m_inc else 1
n_steps = max(1, len(re.findall(r"^\s*\*Step\b", inp_text, flags=re.IGNORECASE | re.MULTILINE)))
sta_lines = [f"   {{s}}   {{n_inc}}   1   0   1   1   {{n_inc}}.0" for s in range(1, n_steps + 1)]
Path(job + ".sta").write_text("\n".join(sta_lines) + "\n", encoding="utf-8")
Path(job + ".log").write_text("THE ANALYSIS HAS COMPLETED SUCCESSFULLY\n", encoding="utf-8")
Path(job + ".odb").write_text(f"NROWS={{n_inc}}\nJOB={{job}}\n", encoding="utf-8")
''',
        encoding="utf-8",
    )
    if os.name == "nt":
        cmd_path = tool_dir / "fake_fiber_abq.cmd"
        cmd_path.write_text(
            f'@echo off\nchcp 65001 >nul\n"{sys.executable}" "%~dp0fake_fiber_abq.py" %*\n',
            encoding="utf-8",
        )
        return str(cmd_path)
    sh_path = tool_dir / "fake_fiber_abq.sh"
    sh_path.write_text(f'#!/bin/sh\n"{sys.executable}" "{py_path}" "$@"\n', encoding="utf-8")
    sh_path.chmod(0o755)
    return str(sh_path)


def test_auto_90_percent_memory_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    real_total = detect_total_memory_mb()
    assert real_total is not None and real_total > 1000

    monkeypatch.setattr(abaqus_utils, "detect_total_memory_mb", lambda: 32768)
    per_job = auto_per_job_memory_mb(7, 0.90)
    assert per_job == 4213
    assert 7 * per_job == 29491 <= int(32768 * 0.90)


def test_safe_ascii_temp_dir_fallback_and_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert memory_cli_args(3072) == ["memory=3072mb"]
    assert memory_cli_args(None) == []
    assert memory_cli_args(0) == []

    ascii_dir = tmp_path / "ascii_dir"
    zh_dir = tmp_path / "中文测试"
    assert is_ascii_path(ascii_dir)
    assert not is_ascii_path(zh_dir)
    assert non_ascii_path_warning(ascii_dir) is None
    assert "中文" in (non_ascii_path_warning(zh_dir) or "")

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(zh_dir))
    with safe_ascii_temp_dir(prefix="abq-p1-") as safe_tmp:
        assert Path(safe_tmp).is_dir()
        assert is_ascii_path(Path(safe_tmp))


@pytest.mark.parametrize("resin_count", [1, 2])
def test_step1_parallel_fiber_solver_zero_residue_and_chinese_output(
    tmp_path: Path, resin_count: int
) -> None:
    tmpl_root = tmp_path / "templates"
    _make_synthetic_template(tmpl_root, "5", "rve-5", "7.12E-03")
    tmpl = discover_fiber_templates(tmpl_root)[0]

    resins = [
        ResinProperty(4600.0, 6.0, 0.38, "S8NE"),
        ResinProperty(4000.0, 5.2, 0.38, "H350"),
    ][:resin_count]

    expected_per_job = auto_per_job_memory_mb(7, 0.90)
    fake_abq = _write_fake_fiber_abaqus(tmp_path / "tools", expected_memory_mb=expected_per_job)
    out_parent = tmp_path / "中文输出目录"
    ticks: list[tuple[int, int, str]] = []

    # 使用默认配置：默认 7 路并发 + 自动控制总内存 <= 90%
    config = FiberSolveConfig(abaqus_command=fake_abq, retries=0)
    assert config.max_parallel == 7
    assert config.total_memory_ratio == pytest.approx(0.90)

    summary = run_fiber_solve_batch(
        [tmpl],
        resins,
        out_parent,
        f"batch_{resin_count}",
        config,
        on_progress=lambda done, total, msg: ticks.append((done, total, msg)),
    )
    assert summary.total == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert len(ticks) == 7
    assert ticks[-1][:2] == (7, 7)

    case_dir = summary.results[0].output_dir
    produced = sorted(p.name for p in case_dir.iterdir())
    assert produced == sorted([
        "Mechanical_constants.txt",
        f"{case_dir.name}_fiber_bundle.csv",
        "resin_inputs.csv",
    ])
    rows = read_fiber_bundle_csv(case_dir / f"{case_dir.name}_fiber_bundle.csv")
    assert len(rows) == resin_count
    assert rows[0].resin_name == "S8NE"


def test_step1_standalone_fiber_runner_execution(tmp_path: Path) -> None:
    tmpl_root = tmp_path / "templates"
    _make_synthetic_template(tmpl_root, "5", "rve-5", "7.12E-03")
    tmpl = discover_fiber_templates(tmpl_root)[0]
    resins = [ResinProperty(4600.0, 6.0, 0.38, "S8NE")]

    out_parent = tmp_path / "中文生成路径"
    results = generate_fiber_cases([tmpl], resins, out_parent, batch_name="srv")
    case_dir = results[0].output_dir
    runner = case_dir / "run_fiber_parallel.py"
    assert runner.is_file()
    assert not (case_dir / "EqPropFEA_BatchScripts_Viscoelastic-fiber.py").exists()
    ast.parse(runner.read_text(encoding="utf-8"), feature_version=(3, 9))

    expected_per_job = auto_per_job_memory_mb(7, 0.90)
    fake_abq = _write_fake_fiber_abaqus(tmp_path / "tools", expected_memory_mb=expected_per_job)
    proc = subprocess.run(
        [sys.executable, str(runner), "--abaqus", fake_abq],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (case_dir / "Mechanical_constants.txt").is_file()
    bundle_csv = case_dir / f"{case_dir.name}_fiber_bundle.csv"
    assert bundle_csv.is_file()
    assert len(read_fiber_bundle_csv(bundle_csv)) == 1
    assert list(case_dir.glob("*.odb")) == []


def test_step4_memory_cli_and_safe_ui_defaults(tmp_path: Path) -> None:
    model_dir = tmp_path / "PP1080_60"
    model_dir.mkdir()
    inp_path = model_dir / "1.inp"
    ori_path = model_dir / "1.ori"
    inp_path.write_text(
        "*Heading\n"
        "*Distribution, Location=Element, Table=TexGenOrientationVectors, Name=TexGenOrientationVectors, Input=1.ori\n"
        "*Material, Name=Mat0\n*Elastic\n3e+09, 0.2\n*Expansion\n6.5e-06\n"
        "*Material, Name=Mat1\n*Elastic, type=ENGINEERING CONSTANTS\n"
        "2e+11, 1e+10, 1e+10, 0.3, 0.4, 0.4, 5e+09, 5e+09\n5e+09\n"
        "*Expansion, type=ORTHO\n-2e-07, 3e-06, 3e-06\n"
        "*Step, Name=Step1\n*Static\n*End Step\n"
        "*Step, Name=Step2\n*Static\n*End Step\n",
        encoding="utf-8",
    )
    ori_path.write_text("1, 1, 0, 0\n", encoding="utf-8")

    row = FiberBundleRow(
        "S8NE", 4600.0, 6.0, 0.38,
        56800.0, 28300.0, 28300.0, 0.216, 0.216, 0.278,
        14350.0, 14350.0, 13905.0, 4.42e-6, 9.46e-6, 9.46e-6,
    )
    fake_abq = _write_fake_fiber_abaqus(tmp_path / "tools", expected_memory_mb=12288)
    model = TexGenModel("PP1080_60", inp_path, ori_path)
    tasks = plan_abaqus_tasks([model], [row], tmp_path / "rpt_out")
    config = SolveConfig(abaqus_command=fake_abq, cpus=6, max_parallel=1, retries=0, memory_mb=12288)
    summary = run_abaqus_batch(tasks, config)
    assert summary.succeeded == 1

    bundle_csv = export_fiber_bundle_csv([row], tmp_path / "bundle.csv")
    runner = export_standalone_runner(
        tmp_path / "run_step4.py",
        models_dir=model_dir.parent,
        fiber_csv=bundle_csv,
        output_dir=tmp_path / "rpt_out_runner",
        config=config,
    )
    assert "MEMORY_MB = 12288" in runner.read_text(encoding="utf-8")

    _qt_app()
    window = ToolboxWindow(default_output=tmp_path)
    try:
        page = window.page("meso_solve")
        assert isinstance(page, MesoSolvePage)
        assert page.cpus_spin.value() == 6
        assert page.parallel_spin.value() == 1
        assert page.memory_spin.value() == 12288
        assert page.auto_summary_check.isChecked()
    finally:
        window.close()
        window.deleteLater()


def test_step5_rpt_parsing_summary_and_ui(tmp_path: Path) -> None:
    rpt_root = tmp_path / "rpt_results"
    (rpt_root / "PP1080_60").mkdir(parents=True)
    (rpt_root / "PP1080_80").mkdir(parents=True)

    rpt_body = (
        "Effective material properties:\n"
        "E1: 1.6928e+10\nv12: 0.1215\nv13: 0.2096\n"
        "E2: 1.8713e+10\nv21: 0.1343\nv23: 0.2058\n"
        "E3: 8.2307e+09\nv31: 0.1019\nv32: 0.0905\n"
        "G23: 3.2066e+09\nG31: 3.2481e+09\nG12: 4.8510e+09\n"
        "CTE_1: 1.7132e-05\nCTE_2: 1.5303e-05\nCTE_3: 4.2350e-05\n"
    )
    (rpt_root / "PP1080_60" / "pp4p6_6e_5.rpt").write_text(rpt_body, encoding="utf-8")
    (rpt_root / "PP1080_80" / "pp4_5p2e_5.rpt").write_text(rpt_body, encoding="utf-8")
    (rpt_root / "PP1080_80" / "corrupt.rpt").write_text("incomplete output\n", encoding="utf-8")

    parsed = parse_rpt_file(rpt_root / "PP1080_60" / "pp4p6_6e_5.rpt")
    assert parsed["E1"] == pytest.approx(1.6928e10)
    assert parsed["CTE_1"] == pytest.approx(1.7132e-5)

    rows_raw, warnings = summarize_rpt_dir(rpt_root)
    assert len(rows_raw) == 2
    assert len(warnings) == 1
    assert rows_raw[0].fabric == "PP1080"
    assert rows_raw[0].thickness_um == pytest.approx(60.0)
    assert rows_raw[0].resin_e_mpa == pytest.approx(4600.0)
    assert rows_raw[0].resin_cte_ppm == pytest.approx(60.0)
    assert rows_raw[0].e1_mpa == pytest.approx(16928.0)
    assert rows_raw[0].cte_x_ppm == pytest.approx(17.132)

    bundle_rows = [
        FiberBundleRow("S8NE", 4600.0, 6.0, 0.36, 56000, 28000, 28000, 0.21, 0.21, 0.28, 14000, 14000, 13000, 4e-6, 9e-6, 9e-6),
        FiberBundleRow("H350", 4000.0, 5.2, 0.40, 55000, 26000, 26000, 0.21, 0.21, 0.28, 13000, 13000, 12000, 4e-6, 8e-6, 8e-6),
    ]
    bundle_csv = export_fiber_bundle_csv(bundle_rows, tmp_path / "fiber_bundle.csv")
    rows_matched, warnings2 = summarize_rpt_dir(rpt_root, fiber_csv=bundle_csv)
    assert len(warnings2) == 1
    assert [(r.resin, r.resin_nu, r.thickness_um) for r in rows_matched] == [
        ("S8NE", 0.36, 60.0),
        ("H350", 0.40, 80.0),
    ]

    csv_out = export_rpt_summary_csv(rows_matched, tmp_path / "summary.csv")
    df = pd.read_csv(csv_out)
    assert list(df.columns) == list(RPT_SUMMARY_COLUMNS)
    assert len(df) == 2

    app = _qt_app()
    window = ToolboxWindow(default_output=tmp_path)
    try:
        window.select_module("meso_solve")
        meso_page = window.page("meso_solve")
        assert isinstance(meso_page, MesoSolvePage)
        meso_page.load_rpt_dir(rpt_root)
        meso_page.load_fiber_csv(bundle_csv)
        assert meso_page.preview_table.rowCount() == 2
        assert "已解析 2 条 RPT" in meso_page.rpt_badge.text()

        exported = meso_page.generate_summary()
        assert exported is not None and exported.is_file()
        assert exported == (tmp_path / "effective_properties_summary.csv").resolve()
        assert len(pd.read_csv(exported)) == 2
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
