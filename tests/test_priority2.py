import os
from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from multiscale.abaqus.batch import BatchSummary
from multiscale.common.workspace import (
    ProjectLayout,
    find_latest_fiber_bundle_csv,
    find_latest_texgen_batch_dir,
)
from multiscale.fiber.bundle import FiberBundleRow, export_fiber_bundle_csv
from multiscale.texgen import presets as preset_mod
from multiscale.texgen.geometry import PlainWeave
from multiscale.texgen.presets import (
    BUILTIN_FABRIC_PRESETS,
    delete_user_preset,
    list_all_presets,
    load_user_presets,
    save_user_preset,
)
from multiscale.ui.pages.fiber_page import FiberRvePage
from multiscale.ui.pages.meso_page import MesoSolvePage
from multiscale.ui.pages.texgen_editor import GeometryEditor
from multiscale.ui.pages.texgen_page import TexGenPage
from multiscale.ui.window import ToolboxWindow


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    return app


def _sample_rpt_text() -> str:
    return (
        "Effective material properties:\n"
        "E1: 1.6928e+10\nv12: 0.1215\nv13: 0.2096\n"
        "E2: 1.8713e+10\nv21: 0.1343\nv23: 0.2058\n"
        "E3: 8.2307e+09\nv31: 0.1019\nv32: 0.0905\n"
        "G23: 3.2066e+09\nG31: 3.2481e+09\nG12: 4.8510e+09\n"
        "CTE_1: 1.7132e-05\nCTE_2: 1.5303e-05\nCTE_3: 4.2350e-05\n"
    )


def test_project_layout_and_artifact_discovery(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    assert layout.fiber_dir == tmp_path / "01_fiber"
    assert layout.texgen_dir == tmp_path / "02_texgen"
    assert layout.rpt_dir == tmp_path / "03_rpt"
    assert layout.summary_csv == tmp_path / "effective_properties_summary.csv"

    assert find_latest_fiber_bundle_csv(layout) is None
    assert find_latest_texgen_batch_dir(layout) is None

    row = FiberBundleRow(
        "S8NE", 4604.51, 6.04967, 0.38,
        56800.0, 28300.0, 28300.0, 0.216, 0.216, 0.278,
        14350.0, 14350.0, 13905.0, 4.42e-6, 9.46e-6, 9.46e-6,
    )
    old_csv = export_fiber_bundle_csv(
        [row], layout.fiber_dir / "fiber_5um_a" / "fiber_5um_a_fiber_bundle.csv"
    )
    new_csv = export_fiber_bundle_csv(
        [row], layout.fiber_dir / "fiber_6um_b" / "fiber_6um_b_fiber_bundle.csv"
    )
    too_deep = export_fiber_bundle_csv(
        [row], layout.fiber_dir / "sub" / "nested" / "deep_fiber_bundle.csv"
    )
    corrupt_csv = layout.fiber_dir / "bad_fiber_bundle.csv"
    corrupt_csv.write_text("bad,header\n1,2\n", encoding="utf-8")

    os.utime(old_csv, (1_000_000, 1_000_000))
    os.utime(new_csv, (2_000_000, 2_000_000))
    os.utime(too_deep, (3_000_000, 3_000_000))
    os.utime(corrupt_csv, (4_000_000, 4_000_000))
    assert find_latest_fiber_bundle_csv(layout) == new_csv.resolve()

    # TexGen discovery: script-only fallback first, then prioritize batch with real models.
    script_only = layout.texgen_dir / "PP1080_script"
    script_only.mkdir(parents=True)
    (script_only / "generated_scripts_parametric_modeling.py").write_text("", encoding="utf-8")
    assert find_latest_texgen_batch_dir(layout) == script_only.resolve()

    solved_batch = layout.texgen_dir / "PP1080_solved"
    model_dir = solved_batch / "PP1080_80"
    model_dir.mkdir(parents=True)
    (model_dir / "1.inp").write_text("*Heading\n", encoding="utf-8")
    (model_dir / "1.ori").write_text("1, 1, 0, 0\n", encoding="utf-8")
    assert find_latest_texgen_batch_dir(layout) == solved_batch.resolve()


def test_fabric_presets_builtin_and_custom_crud(tmp_path: Path) -> None:
    assert [p.fabric.name for p in BUILTIN_FABRIC_PRESETS] == [
        "PP1080",
        "PP2116",
        "PP3313",
        "PP1035",
        "PP1027",
    ]
    assert all(p.builtin for p in BUILTIN_FABRIC_PRESETS)

    presets_file = tmp_path / "fabric_presets.json"
    # Corrupt JSON gracefully degrades to builtin-only list.
    presets_file.write_text("{corrupt json", encoding="utf-8")
    assert load_user_presets(presets_file) == []
    assert len(list_all_presets(presets_file)) == 5

    custom_7628 = PlainWeave("PP7628", 0.35, 0.58, 0.35, 0.56, 180.0)
    added = save_user_preset(custom_7628, note="custom test", path=presets_file)
    assert [p.fabric.name for p in added][-1] == "PP7628"
    assert not added[-1].builtin

    # Override builtin PP1080 thickness, then delete user override -> builtin restores cleanly.
    override_1080 = PlainWeave("PP1080", 0.27, 0.424, 0.27, 0.541, 55.0)
    overridden = save_user_preset(override_1080, path=presets_file)
    assert len(overridden) == 6
    p1080 = next(p for p in overridden if p.fabric.name == "PP1080")
    assert not p1080.builtin
    assert p1080.fabric.fabric_thickness_um == pytest.approx(55.0)

    restored = delete_user_preset("pp1080", path=presets_file)
    p1080_restored = next(p for p in restored if p.fabric.name == "PP1080")
    assert p1080_restored.builtin
    assert p1080_restored.fabric.fabric_thickness_um == pytest.approx(53.0)

    final_list = delete_user_preset("PP7628", path=presets_file)
    assert len(final_list) == 5


def test_geometry_editor_presets_ui(tmp_path: Path) -> None:
    app = _qt_app()
    presets_file = tmp_path / "fabric_presets.json"
    page = TexGenPage(default_output=tmp_path, presets_path=presets_file)
    try:
        editor = page.geometry_editor
        assert isinstance(editor, GeometryEditor)
        assert editor.preset_combo.currentIndex() == 0
        assert editor.is_blank()
        assert not editor.delete_preset_btn.isEnabled()

        # Select builtin PP1080 preset -> fills geometry fields and recommends fiber diameter.
        assert editor.select_preset("PP1080")
        assert editor.fields["name"].text() == "PP1080"
        assert editor.fields["fabric_thickness_um"].text() == "53"
        assert editor.fields["warp_width_mm"].text() == "0.269"
        assert "建议单丝模板：5 μm" in editor.measure.text()
        assert not editor.delete_preset_btn.isEnabled()

        # Modify into custom preset PP7628 and persist via UI button.
        editor.fields["name"].setText("PP7628")
        editor.fields["fabric_thickness_um"].setText("180")
        editor.fields["warp_width_mm"].setText("0.35")
        editor.fields["warp_spacing_mm"].setText("0.58")
        editor.fields["weft_width_mm"].setText("0.35")
        editor.fields["weft_spacing_mm"].setText("0.56")
        editor.save_preset_btn.click()
        app.processEvents()

        assert presets_file.is_file()
        assert editor.delete_preset_btn.isEnabled()
        assert "已保存本地织物预设：PP7628" in page.notice.text()

        # Reload into a second editor instance and verify persistence, then delete via UI.
        reloaded_editor = GeometryEditor(presets_path=presets_file)
        assert reloaded_editor.select_preset("PP7628")
        assert reloaded_editor.fields["fabric_thickness_um"].text() == "180"
        reloaded_editor.deleteLater()

        editor.delete_preset_btn.click()
        app.processEvents()
        assert not editor.delete_preset_btn.isEnabled()
        assert "PP7628" not in [p.fabric.name for p in load_user_presets(presets_file)]
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_unified_project_dir_switching_and_3step_linkage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        preset_mod,
        "default_user_presets_path",
        lambda: tmp_path / "isolated_user_presets.json",
    )
    app = _qt_app()

    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "中文项目目录"
    proj_c = tmp_path / "proj_c"

    window = ToolboxWindow(default_output=proj_a)
    try:
        fiber_page = window.page("fiber_rve")
        texgen_page = window.page("texgen_modeling")
        meso_page = window.page("meso_solve")
        assert isinstance(fiber_page, FiberRvePage)
        assert isinstance(texgen_page, TexGenPage)
        assert isinstance(meso_page, MesoSolvePage)

        assert window.project_path_edit.text() == str(proj_a.resolve())
        assert window.project_path_warning.isHidden()
        assert Path(fiber_page.output_path.text()) == (proj_a / "01_fiber").resolve()
        assert Path(texgen_page.output_path.text()) == (proj_a / "02_texgen").resolve()
        assert Path(meso_page.output_dir_edit.text()) == (proj_a / "03_rpt").resolve()
        assert Path(meso_page.summary_csv_edit.text()) == (
            proj_a / "effective_properties_summary.csv"
        ).resolve()

        # Step 1: direct bundle CSV in 01_fiber notifies Step 3 CSV (Tab 0 & Tab 1).
        step1_rows = [
            FiberBundleRow(
                "S8NE", 4604.51, 6.04967, 0.38,
                57000.0, 27400.0, 27500.0, 0.24, 0.24, 0.41,
                12000.0, 12100.0, 14000.0, 5e-6, 1.2e-5, 1.3e-5,
            ),
            FiberBundleRow(
                "H350", 4030.56, 5.27133, 0.38,
                56000.0, 26400.0, 26500.0, 0.23, 0.23, 0.42,
                11000.0, 11100.0, 13000.0, 4e-6, 1.1e-5, 1.2e-5,
            ),
        ]
        bundle_csv = export_fiber_bundle_csv(
            step1_rows, proj_a / "01_fiber" / "fiber_5um_demo_fiber_bundle.csv"
        )
        window.workspace.notify_fiber_csv(bundle_csv)
        assert Path(meso_page.csv_path_edit.text()) == bundle_csv.resolve()
        assert Path(meso_page.rpt_csv_edit.text()) == bundle_csv.resolve()

        # Step 2: select PP1080 preset, generate TexGen batch -> quiet link to Step 3.
        window.select_module("texgen_modeling")
        assert texgen_page.geometry_editor.select_preset("PP1080")
        texgen_page.cases.table.item(0, 1).setText("83.5")
        texgen_page.batch_suffix.setText("run1")
        texgen_page._generate()
        batch_dir = texgen_page.script.parent
        assert Path(meso_page.models_dir_edit.text()) == batch_dir.resolve()
        assert "batch_dir" not in meso_page.notice.text()

        # Simulate TexGenGUI producing .inp/.ori, then switch to Step 3 -> auto-discovers models.
        model_dir = batch_dir / "PP1080_83p5"
        (model_dir / "1.inp").write_text("*Heading\n", encoding="utf-8")
        (model_dir / "1.ori").write_text("1, 1, 0, 0\n", encoding="utf-8")
        window.select_module("meso_solve")
        assert meso_page.summary_badge.text() == "1 模型 × 2 材料 = 2 任务"

        # Simulate Step 3 solve finish with an .rpt file -> auto-exports summary & fills Tab 1 table.
        rpt_case_dir = proj_a / "03_rpt" / "PP1080_83p5"
        rpt_case_dir.mkdir(parents=True, exist_ok=True)
        (rpt_case_dir / "pp4p60451_6p04967e_5.rpt").write_text(_sample_rpt_text(), encoding="utf-8")
        meso_page._on_finished(
            BatchSummary(total=1, succeeded=1, skipped=0, failed=0, cancelled=0, results=())
        )
        assert meso_page.last_summary_csv == (proj_a / "effective_properties_summary.csv").resolve()
        assert meso_page.last_summary_csv.is_file()
        assert meso_page.preview_table.rowCount() == 1
        assert meso_page.tabs.tabText(1) == "RPT 汇总表 (1)"
        df_a = pd.read_csv(meso_page.last_summary_csv)
        assert df_a.at[0, "resin"] == "S8NE"

        # Switch to empty Chinese project dir -> resets stale inputs and shows non-ASCII warning.
        proj_b.mkdir(parents=True)
        window.set_project_dir(proj_b)
        assert not window.project_path_warning.isHidden()
        assert "中文路径" in window.project_path_warning.text()
        assert Path(fiber_page.output_path.text()) == (proj_b / "01_fiber").resolve()
        assert Path(texgen_page.output_path.text()) == (proj_b / "02_texgen").resolve()
        assert Path(meso_page.output_dir_edit.text()) == (proj_b / "03_rpt").resolve()
        assert meso_page.models_dir_edit.text() == ""
        assert meso_page.csv_path_edit.text() == ""
        assert meso_page.preview_table.rowCount() == 0
        assert meso_page.summary_badge.text() == "0 模型 × 0 材料 = 0 任务"

        # Switch to pre-populated proj_c -> auto-loads fiber CSV, TexGen models, and RPT preview.
        pre_row = FiberBundleRow(
            "S8NE", 4604.51, 6.04967, 0.38,
            56800.0, 28300.0, 28300.0, 0.216, 0.216, 0.278,
            14350.0, 14350.0, 13905.0, 4.42e-6, 9.46e-6, 9.46e-6,
        )
        export_fiber_bundle_csv([pre_row], proj_c / "01_fiber" / "fiber_5um_pre" / "fiber_5um_pre_fiber_bundle.csv")
        pre_model = proj_c / "02_texgen" / "PP1080_pre" / "PP1080_80"
        pre_model.mkdir(parents=True)
        (pre_model / "1.inp").write_text("*Heading\n", encoding="utf-8")
        (pre_model / "1.ori").write_text("1, 1, 0, 0\n", encoding="utf-8")
        pre_rpt = proj_c / "03_rpt" / "PP1080_80"
        pre_rpt.mkdir(parents=True)
        (pre_rpt / "pp4p60451_6p04967e_5.rpt").write_text(_sample_rpt_text(), encoding="utf-8")

        window.set_project_dir(proj_c)
        assert window.project_path_warning.isHidden()
        assert meso_page.summary_badge.text() == "1 模型 × 1 材料 = 1 任务"
        assert meso_page.preview_table.rowCount() == 1
        assert meso_page.tabs.tabText(1) == "RPT 汇总表 (1)"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
