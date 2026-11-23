import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QHeaderView, QLabel, QPushButton, QSpinBox

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from multiscale.common.resources import app_root, is_frozen, package_resource
from multiscale.fiber.rve import (
    BATCH_SCRIPT_NAME,
    INP_NAMES,
    REQUIRED_TEMPLATE_FILES,
    SAMPLE_RESIN_ROWS,
    UMAT_CTE_SUBROUTINE,
    UMAT_SUBROUTINE,
    FiberTemplate,
    ResinProperty,
    default_fiber_templates_root,
    default_sample_resin_csv,
    discover_fiber_templates,
    generate_fiber_cases,
    parse_resin_text,
    read_resin_csv,
    resin_stiffness_row,
    write_sample_resin_csv,
)
from multiscale.ui import _run_self_check
from multiscale.ui.pages.fiber_page import FiberRvePage, PasteResinDialog
from multiscale.ui.window import ToolboxWindow


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    return app


def _make_synthetic_template(root: Path, group: str, name: str, cell_mm: str) -> Path:
    tmpl_dir = root / group / name
    tmpl_dir.mkdir(parents=True, exist_ok=True)
    (tmpl_dir / BATCH_SCRIPT_NAME).write_text(
        f"L = {cell_mm}\nH = {cell_mm}\nW = {cell_mm}\n",
        encoding="utf-8",
    )
    umat_body = """      SUBROUTINE UMAT
      DOUBLE PRECISION D11(100),D12(100),D13(100),D22(100),D23(100),
     + D33(100),D44(100),D55(100),D66(100),C_0(NTENS,NTENS),
     + C_INF(NTENS,NTENS),C_DEF(NTENS,NTENS),STREND(NTENS)
C --- D11 ---
      D11(1) = 1.0e+03
C --- D12 ---
      D12(1) = 5.0e+02
C --- D13 ---
      D13(1) = 5.0e+02
C --- D22 ---
      D22(1) = 1.0e+03
C --- D23 ---
      D23(1) = 5.0e+02
C --- D33 ---
      D33(1) = 1.0e+03
C --- D44 ---
      D44(1) = 2.5e+02
C --- D55 ---
      D55(1) = 2.5e+02
C --- D66 ---
      D66(1) = 2.5e+02
      END
"""
    (tmpl_dir / UMAT_SUBROUTINE).write_text(umat_body, encoding="utf-8")
    cte_extra = """      SUBROUTINE UEXPAN
       DOUBLE PRECISION CTE_X(100),CTE_Y(100),CTE_Z(100)
C --- CTE_X ---
      CTE_X(1) = 5.0e-05
C --- CTE_Y ---
      CTE_Y(1) = 5.0e-05
C --- CTE_Z ---
      CTE_Z(1) = 5.0e-05
      END
"""
    (tmpl_dir / UMAT_CTE_SUBROUTINE).write_text(umat_body + cte_extra, encoding="utf-8")
    inp_body = """*Heading
*Amplitude, name=Amp-1
             0.,              1.,             2.,              1.
*Step, name=Step-1, nlgeom=NO
*Static, direct
1., 2.,
*End Step
"""
    for inp_name in INP_NAMES:
        (tmpl_dir / inp_name).write_text(inp_body, encoding="utf-8")
    # Extra stray file that must NOT be copied into generated directories.
    (tmpl_dir / "old_junk.zip").write_bytes(b"zip")
    return tmpl_dir


def test_default_fiber_templates_repository_is_clean() -> None:
    root = default_fiber_templates_root()
    templates = discover_fiber_templates(root)
    assert [(t.group, t.name) for t in templates] == [
        ("5", "20260525-fiber-rve-6300"),
        ("6", "3313-fiber-origin-6300-ccs"),
        ("7", "2116-fiber-6300-ccs"),
    ]
    assert [t.cell_size_um for t in templates] == pytest.approx([7.12, 8.551, 9.976])
    for tmpl in templates:
        assert tmpl.inp_count == 7
        files = {p.name for p in tmpl.path.iterdir()}
        assert files == set(REQUIRED_TEMPLATE_FILES)
        assert not (tmpl.path / "Mechanical_constants.txt").exists()


def test_resin_constitutive_math_and_validation() -> None:
    row = resin_stiffness_row(ResinProperty(4604.51, 6.04967, 0.38, "S8NE"))
    g_expected = 4604.51 / (2 * 1.38)
    coupling_expected = 0.38 * 4604.51 / (1.38 * (1 - 0.76))
    assert row["D44"] == pytest.approx(g_expected)
    assert row["D12"] == pytest.approx(coupling_expected)
    assert row["D11"] == pytest.approx(coupling_expected + 2 * g_expected)
    assert row["CTE_X"] == pytest.approx(6.04967e-5)

    with pytest.raises(ValueError, match="大于 0"):
        ResinProperty(0.0, 5.0, 0.38)
    with pytest.raises(ValueError, match="泊松比"):
        ResinProperty(3000.0, 5.0, 0.5)


def test_parse_resin_text_and_csv(tmp_path: Path) -> None:
    tuple_text = """
    (4604.51, 6.04967, 0.38),
    (4030.56, 5.27133, 0.38),
    """
    parsed = parse_resin_text(tuple_text)
    assert parsed == [
        ResinProperty(4604.51, 6.04967, 0.38),
        ResinProperty(4030.56, 5.27133, 0.38),
    ]

    tabular_text = """
    Resin Name, E (MPa), CTE (1e-5/K), Poisson
    S8NE, 4604.51, 6.04967, 0.38
    H350\t4030.56\t5.27133\t0.38
    """
    parsed_tab = parse_resin_text(tabular_text)
    assert parsed_tab == [
        ResinProperty(4604.51, 6.04967, 0.38, "S8NE"),
        ResinProperty(4030.56, 5.27133, 0.38, "H350"),
    ]

    csv_path = tmp_path / "resins.csv"
    csv_path.write_text(
        "树脂牌号,E1MPa,CTE/e-5,泊松比\nS8NE,4604.51,6.04967,0.38\nH350,4030.56,5.27133,0.38\n",
        encoding="utf-8-sig",
    )
    assert read_resin_csv(csv_path) == parsed_tab


def test_generate_fiber_cases_and_large_batch_fortran_resizing(tmp_path: Path) -> None:
    tmpl_root = tmp_path / "templates"
    _make_synthetic_template(tmpl_root, "5", "rve-5a", "7.12E-03")
    _make_synthetic_template(tmpl_root, "5", "rve-5b", "7.15E-03")
    _make_synthetic_template(tmpl_root, "6", "rve-6a", "8.551E-03")

    templates = discover_fiber_templates(tmpl_root)
    assert len(templates) == 3

    out_dir = tmp_path / "out"
    resins_120 = [ResinProperty(1000.0 + i * 25.0, 5.0 + i * 0.05, 0.38, f"R{i}") for i in range(120)]
    results = generate_fiber_cases(templates, resins_120, out_dir, batch_name="big_batch")
    assert [r.output_dir.name for r in results] == [
        "fiber_5um_rve-5a_big_batch",
        "fiber_5um_rve-5b_big_batch",
        "fiber_6um_big_batch",
    ]

    for res in results:
        assert not (res.output_dir / "old_junk.zip").exists()
        assert not (res.output_dir / BATCH_SCRIPT_NAME).exists()
        assert (res.output_dir / "run_fiber_parallel.py").is_file()
        assert (res.output_dir / "resin_inputs.csv").is_file()
        table = pd.read_csv(res.output_dir / "resin_inputs.csv")
        assert len(table) == 120
        assert list(table.columns) == ["Row", "Resin Name", "E (MPa)", "CTE (1e-5/K)", "Poisson"]

        umat_text = (res.output_dir / UMAT_SUBROUTINE).read_text(encoding="utf-8")
        assert "D11(120)" in umat_text
        assert "D11(120) =" in umat_text
        assert "CTE_X" not in umat_text
        for line in umat_text.splitlines():
            assert len(line) <= 72

        cte_text = (res.output_dir / UMAT_CTE_SUBROUTINE).read_text(encoding="utf-8")
        assert "CTE_X(120)" in cte_text
        assert "CTE_X(120) =" in cte_text
        for line in cte_text.splitlines():
            assert len(line) <= 72

        for inp_name in INP_NAMES:
            inp_text = (res.output_dir / inp_name).read_text(encoding="utf-8")
            assert re.search(r"\*Step,[^\n]*\binc=140\b", inp_text)
            assert "120.,              1." in inp_text
            assert "1., 120.," in inp_text

    # Re-running with the same batch_name rejects collision instead of overwriting.
    with pytest.raises(ValueError, match="目标目录已存在"):
        generate_fiber_cases([templates[2]], resins_120[:2], out_dir, batch_name="big_batch")


def test_real_fiber_template_smoke_generation(tmp_path: Path) -> None:
    templates = discover_fiber_templates(default_fiber_templates_root())
    assert len(templates) == 3
    resins = [
        ResinProperty(4604.51, 6.04967, 0.38, "S8NE"),
        ResinProperty(4030.56, 5.27133, 0.38, "H350"),
    ]
    results = generate_fiber_cases(templates, resins, tmp_path, batch_name="smoke")
    assert [r.output_dir.name for r in results] == [
        "fiber_5um_smoke",
        "fiber_6um_smoke",
        "fiber_7um_smoke",
    ]
    for res in results:
        out = res.output_dir
        cte_sub = (out / UMAT_CTE_SUBROUTINE).read_text(encoding="utf-8")
        assert "D11(100)" in cte_sub
        assert "CTE_X(1) = 6.0496700000e-05" in cte_sub
        assert "CTE_X(2) = 5.2713300000e-05" in cte_sub
        assert "CTE_X(3)" not in cte_sub
        for inp_name in INP_NAMES:
            inp_text = (out / inp_name).read_text(encoding="utf-8")
            assert "1., 2.," in inp_text


def test_sample_resin_csv_roundtrip(tmp_path: Path) -> None:
    builtin_sample = default_sample_resin_csv()
    assert builtin_sample.is_file()
    assert read_resin_csv(builtin_sample) == list(SAMPLE_RESIN_ROWS)

    exported = write_sample_resin_csv(tmp_path / "sample.csv")
    assert exported.is_file()
    assert read_resin_csv(exported) == list(SAMPLE_RESIN_ROWS)


def test_fiber_rve_ui_workflow_offscreen(tmp_path: Path) -> None:
    app = _qt_app()

    synth_root = tmp_path / "fiber_templates"
    _make_synthetic_template(synth_root, "5", "rve-5", "7.12E-03")
    _make_synthetic_template(synth_root, "7", "rve-7", "9.976E-03")

    out_dir = tmp_path / "outputs"
    window = ToolboxWindow(default_output=out_dir)
    try:
        assert window.current_module_id == "fiber_rve"
        assert not window.windowIcon().isNull()
        page = window.page("fiber_rve")
        assert isinstance(page, FiberRvePage)

        # Template paths and manual solver-config clutter are hidden from the Step 1 UI.
        button_texts = [btn.text() for btn in page.findChildren(QPushButton)]
        label_texts = [lbl.text() for lbl in page.findChildren(QLabel)]
        assert "更换模板库…" not in button_texts
        assert "合成已有 CSV…" not in button_texts
        assert "模板库目录" not in label_texts
        assert "7 方向并发求解与内存安全配置" not in label_texts
        assert page.findChildren(QSpinBox) == []
        assert "示例 CSV…" in button_texts
        assert Path(page.output_path.text()) == (out_dir / "01_fiber")

        # Resin name column is compact (Interactive <= 140), while numerical columns Stretch.
        headings = page.resin_editor.table.horizontalHeader()
        assert headings.sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
        assert page.resin_editor.table.columnWidth(1) <= 140
        for col in (2, 3, 4):
            assert headings.sectionResizeMode(col) == QHeaderView.ResizeMode.Stretch

        page.template_selector.set_templates_root(synth_root)
        assert len(page.template_selector.templates()) == 2
        for box in page.template_selector.findChildren(QCheckBox):
            assert "\\" not in box.toolTip() and "/" not in box.toolTip()

        page.template_selector.select_all()
        assert len(page.template_selector.selected_templates()) == 2
        assert page.batch_prefix.text() == "fiber_5um/7um_"

        # Exercise batch paste parsing programmatically via PasteResinDialog.
        dlg = PasteResinDialog(page)
        dlg.editor.setPlainText("(4604.51, 6.04967, 0.38),\n(4030.56, 5.27133, 0.38),")
        dlg.replace_btn.click()
        assert len(dlg.parsed) == 2
        page.resin_editor.set_resins(dlg.parsed, replace=True)
        assert "已选 2 个模板   /   2 组树脂" in page.summary_badge.text()

        page.batch_name.setText("ui_test")
        page.generate_btn.click()
        app.processEvents()

        fiber_out = out_dir / "01_fiber"
        assert len(page.last_results) == 2
        assert (fiber_out / "fiber_5um_ui_test" / "resin_inputs.csv").is_file()
        assert (fiber_out / "fiber_5um_ui_test" / "run_fiber_parallel.py").is_file()
        assert not (fiber_out / "fiber_5um_ui_test" / BATCH_SCRIPT_NAME).exists()
        assert (fiber_out / "fiber_7um_ui_test" / "resin_inputs.csv").is_file()
        assert (fiber_out / "fiber_7um_ui_test" / "run_fiber_parallel.py").is_file()
        assert not (fiber_out / "fiber_7um_ui_test" / BATCH_SCRIPT_NAME).exists()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_resources_frozen_and_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not is_frozen()
    assert app_root() == Path(__file__).resolve().parents[1]
    assert package_resource("texgen/templates/batch.py.tpl").is_file()
    assert package_resource("ui/assets/app_icon.png").is_file()
    assert package_resource("ui/assets/app_icon.ico").is_file()

    fake_mei = tmp_path / "mei"
    (fake_mei / "multiscale" / "ui").mkdir(parents=True)
    frozen_qss = fake_mei / "multiscale" / "ui" / "theme.qss"
    frozen_qss.write_text("/* frozen */", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_mei), raising=False)

    assert is_frozen()
    assert app_root() == fake_mei
    assert package_resource("ui/theme.qss") == frozen_qss
    # Falls back to package_dir if a file is not present under _MEIPASS/multiscale.
    assert package_resource("texgen/templates/batch.py.tpl").is_file()
    assert package_resource("ui/assets/app_icon.png").is_file()
    assert package_resource("ui/assets/app_icon.ico").is_file()


def test_self_check_report_in_dev_mode(tmp_path: Path) -> None:
    app = _qt_app()
    window = ToolboxWindow(default_output=tmp_path)
    report_path = tmp_path / "self_check.json"
    try:
        exit_code = _run_self_check(window, report_path)
        assert exit_code == 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report == {
            "frozen": False,
            "modules": [
                "fiber_rve",
                "texgen_modeling",
                "meso_solve",
            ],
            "templates": ["5", "6", "7"],
            "fabric_presets": ["PP1080", "PP2116", "PP3313", "PP1035", "PP1027"],
            "sample_csv_count": 2,
            "fiber_generated": True,
            "texgen_generated": True,
            "abaqus_runner_exported": True,
            "rpt_summary_exported": True,
        }
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
