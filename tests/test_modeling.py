import ast
import os
from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from multiscale.texgen.export import export_batch
from multiscale.texgen.geometry import PlainWeave, calculate_geometry
from multiscale.texgen.workflow import (
    PLACEHOLDER_MATRIX_PROPERTIES,
    PLACEHOLDER_YARN_PROPERTIES,
    CaseInput,
    build_prefixed_dir_name,
    prepare_batch,
    resolve_thicknesses,
)
from multiscale.ui.pages.texgen_page import TexGenPage
from multiscale.ui.window import ToolboxWindow


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    return app


def test_plain_weave_geometry_validation() -> None:
    fabric = PlainWeave("PP2116", 0.30, 0.4237, 0.32, 0.4386, 94.0)
    assert fabric.cell_size_mm == pytest.approx((0.8474, 0.8772))
    assert fabric.yarn_height_um == pytest.approx(47.0)

    geom = calculate_geometry(fabric)
    assert geom.warp_width_m == pytest.approx(0.30e-3)
    assert geom.weft_width_m == pytest.approx(0.32e-3)
    assert geom.fabric_thickness_m == pytest.approx(94.0e-6)
    assert geom.yarn_height_m == pytest.approx(47.0e-6)

    with pytest.raises(ValueError, match="PP 型号"):
        PlainWeave("_invalid", 0.25, 0.4, 0.25, 0.4, 50.0)
    with pytest.raises(ValueError, match="经纱宽度"):
        PlainWeave("PP1080", 0.5, 0.4, 0.25, 0.4, 50.0)
    with pytest.raises(ValueError, match="大于 0"):
        PlainWeave("PP1080", 0.25, 0.4, 0.25, 0.4, 0.0)


def test_thickness_resolution_and_prefixed_naming() -> None:
    assert resolve_thicknesses(83.4, "total", 53.0) == (83.4, 30.4)
    assert resolve_thicknesses(30.4, "resin", 53.0) == (83.4, 30.4)

    assert build_prefixed_dir_name("PP1080", "batch1", field_label="批次目录名") == "PP1080_batch1"
    assert (
        build_prefixed_dir_name("PP1080", "pp1080_custom", field_label="批次目录名")
        == "PP1080_custom"
    )
    with pytest.raises(ValueError, match="不能为空"):
        build_prefixed_dir_name("PP1080", "PP1080_", field_label="批次目录名")
    with pytest.raises(ValueError, match="仅支持"):
        build_prefixed_dir_name("PP1080", "bad name", field_label="批次目录名")

    fabric = PlainWeave("PP1080", 0.269, 0.424, 0.269, 0.541, 53.0)
    batch = prepare_batch(
        [
            CaseInput(83.5, "total"),
            CaseInput(31.0, "resin"),
            CaseInput(17.0, "resin", dir_suffix="thin"),
            CaseInput(27.0, "resin", dir_suffix="PP1080_custom"),
        ],
        fabric,
        batch_suffix="run01",
    )
    assert batch.batch_name == "PP1080_run01"
    assert [case.name for case in batch.cases] == [
        "PP1080_83p5",
        "PP1080_84",
        "PP1080_thin",
        "PP1080_custom",
    ]
    assert [case.total_thickness_um for case in batch.cases] == [83.5, 84.0, 70.0, 80.0]
    assert [case.resin_thickness_um for case in batch.cases] == [30.5, 31.0, 17.0, 27.0]

    with pytest.raises(ValueError, match="至少一个"):
        prepare_batch([], fabric)
    with pytest.raises(ValueError, match="PP 总厚度须大于织物厚度"):
        prepare_batch([CaseInput(53.0, "total")], fabric)
    with pytest.raises(ValueError, match="剩余树脂厚度须大于 0"):
        prepare_batch([CaseInput(0.0, "resin")], fabric)
    with pytest.raises(ValueError, match="重复"):
        prepare_batch([CaseInput(83.5, "total"), CaseInput(30.5, "resin")], fabric)


def test_export_batch_creates_flat_dirs_and_compact_csv(tmp_path: Path) -> None:
    fabric = PlainWeave("PP-custom", 0.235, 0.46, 0.31, 0.61, 60.0)

    # Default timestamped batch directory.
    auto_batch = prepare_batch(
        [CaseInput(102.5, "total"), CaseInput(25.0, "resin", dir_suffix="thin")],
        fabric,
    )
    script_a = export_batch(auto_batch, tmp_path)
    script_b = export_batch(auto_batch, tmp_path)
    assert script_b.parent != script_a.parent
    assert script_a.parent.name.startswith("PP-custom_TexGen_")
    assert script_b.read_bytes() == script_a.read_bytes()

    # No batch.json emitted; only script, compact CSV, and flat case directories.
    assert not (script_a.parent / "batch.json").exists()
    assert (script_a.parent / "PP-custom_102p5").is_dir()
    assert (script_a.parent / "PP-custom_thin").is_dir()
    assert not (script_a.parent / "PP-custom_102p5" / "texgen").exists()
    assert not (script_a.parent / "PP-custom_thin" / "texgen").exists()

    table = pd.read_csv(script_a.parent / "yarn_parameter_combinations.csv")
    assert list(table.columns) == [
        "serial number",
        "Case Name",
        "PP Model",
        "Warp Width (mm)",
        "Warp Spacing (mm)",
        "Weft Width (mm)",
        "Weft Spacing (mm)",
        "Fabric Thickness (um)",
        "Extra Resin Thickness (um)",
        "PP Total Thickness (um)",
    ]
    assert list(table["Case Name"]) == ["PP-custom_102p5", "PP-custom_thin"]
    assert list(table["PP Model"]) == ["PP-custom", "PP-custom"]
    assert list(table["PP Total Thickness (um)"]) == pytest.approx([102.5, 85.0])
    assert list(table["Extra Resin Thickness (um)"]) == pytest.approx([42.5, 25.0])
    assert table.at[0, "Fabric Thickness (um)"] == pytest.approx(60.0)
    assert table.at[0, "Warp Width (mm)"] == pytest.approx(0.235)
    assert table.at[0, "Weft Spacing (mm)"] == pytest.approx(0.61)

    module = ast.parse(script_a.read_text(encoding="utf-8"), feature_version=(3, 9))
    script_data = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "BATCH"
    )
    assert script_data["placeholder_materials"] == {
        "matrix": PLACEHOLDER_MATRIX_PROPERTIES,
        "yarn": PLACEHOLDER_YARN_PROPERTIES,
    }
    assert [case["mesh_dir"] for case in script_data["cases"]] == [
        "PP-custom_102p5",
        "PP-custom_thin",
    ]

    # Custom prefixed batch directory name and collision protection.
    named_batch = prepare_batch([CaseInput(80.0, "total")], fabric, batch_suffix="groupA")
    named_script = export_batch(named_batch, tmp_path)
    assert named_script.parent == (tmp_path / "PP-custom_groupA")
    assert (named_script.parent / "PP-custom_80").is_dir()
    with pytest.raises(ValueError, match="目标目录已存在"):
        export_batch(named_batch, tmp_path)


def test_sidebar_toolbox_ui_and_end_to_end_generation(tmp_path: Path) -> None:
    app = _qt_app()
    window = ToolboxWindow(default_output=tmp_path)
    try:
        assert window.windowTitle() == "材料多尺度建模处理工具箱"
        assert window.current_module_id == "fiber_rve"
        assert window.findChildren(QLabel, "NavCategory") == []

        module_ids = [m.module_id for m in window.registered_modules()]
        assert module_ids == [
            "fiber_rve",
            "texgen_modeling",
            "meso_solve",
        ]

        window.select_module("meso_solve")
        assert window.current_module_id == "meso_solve"
        window.select_module("texgen_modeling")
        assert window.current_module_id == "texgen_modeling"

        page = window.page("texgen_modeling")
        assert isinstance(page, TexGenPage)
        button_texts = [btn.text() for btn in page.findChildren(QPushButton)]
        label_texts = [lbl.text() for lbl in page.findChildren(QLabel)]
        assert "恢复 PP1080 预设" not in button_texts
        assert "导入 CSV…" not in button_texts
        assert "单胞预览" not in label_texts
        assert "选中工况材料" not in label_texts
        assert Path(page.output_path.text()) == (tmp_path / "02_texgen")

        assert page.geometry_editor.is_blank()
        assert page.batch_prefix.text() == "<PP型号>_"

        # Enter custom PP model and geometry parameters.
        page.geometry_editor.fields["name"].setText("PP2116")
        assert page.batch_prefix.text() == "PP2116_"
        page.geometry_editor.fields["fabric_thickness_um"].setText("94")
        page.geometry_editor.fields["warp_width_mm"].setText("0.35")
        page.geometry_editor.fields["warp_spacing_mm"].setText("0.424")
        page.geometry_editor.fields["weft_width_mm"].setText("0.36")
        page.geometry_editor.fields["weft_spacing_mm"].setText("0.439")
        assert "47 μm" in page.geometry_editor.measure.text()

        # Fill thicknesses in total mode, then toggle to resin mode and verify conversion.
        cases = page.cases
        cases.table.item(0, 1).setText("120")
        assert cases.table.item(0, 2).text() == "26"
        assert cases.table.item(0, 4).text() == "PP2116_120/1.inp"

        cases.add_case("140", dir_suffix="thick")
        assert cases.table.item(1, 2).text() == "46"
        assert cases.table.item(1, 4).text() == "PP2116_thick/2.inp"

        cases.set_mode("resin")
        assert cases.mode == "resin"
        assert cases.table.item(0, 1).text() == "26"
        assert cases.table.item(0, 2).text() == "120"
        assert cases.table.item(1, 1).text() == "46"
        assert cases.table.item(1, 2).text() == "140"

        # Custom batch directory suffix automatically gets PP2116_ prefix.
        page.batch_suffix.setText("batch1")

        generate_button = page.findChild(QPushButton, "Primary")
        assert generate_button is not None
        generate_button.click()
        app.processEvents()

        assert page.script is not None and page.script.is_file()
        assert page.script.parent == (tmp_path / "02_texgen" / "PP2116_batch1")
        assert not (page.script.parent / "batch.json").exists()
        assert (page.script.parent / "PP2116_120").is_dir()
        assert (page.script.parent / "PP2116_thick").is_dir()
        assert not (page.script.parent / "PP2116_120" / "texgen").exists()

        table = pd.read_csv(page.script.parent / "yarn_parameter_combinations.csv")
        assert list(table["Case Name"]) == ["PP2116_120", "PP2116_thick"]
        assert list(table["PP Total Thickness (um)"]) == pytest.approx([120.0, 140.0])
        assert list(table["Extra Resin Thickness (um)"]) == pytest.approx([26.0, 46.0])

        # Re-generating into the same custom directory reports an error without overwriting.
        generate_button.click()
        app.processEvents()
        assert "目标目录已存在" in page.notice.text()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
