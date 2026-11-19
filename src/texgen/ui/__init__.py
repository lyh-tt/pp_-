import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ..fiber_rve import default_sample_resin_csv, read_resin_csv
from ..resources import is_frozen, package_resource
from .window import ToolboxWindow


def _run_self_check(window: ToolboxWindow, report_path: Path) -> int:
    modules = [module.module_id for module in window.registered_modules()]
    if window.current_module_id != "fiber_rve":
        raise RuntimeError(f"Unexpected initial module: {window.current_module_id}")

    fiber_page = window.page("fiber_rve")
    templates = fiber_page.template_selector.templates()
    template_groups = list(dict.fromkeys(tmpl.group for tmpl in templates))
    sample_resins = read_resin_csv(default_sample_resin_csv())

    with TemporaryDirectory(prefix="toolbox-self-check-") as tmp:
        tmp_root = Path(tmp)
        fiber_out = tmp_root / "fiber_out"
        texgen_out = tmp_root / "texgen_out"

        fiber_page.template_selector.select_all()
        fiber_page.resin_editor.set_resins(sample_resins, replace=True)
        fiber_page.output_path.setText(str(fiber_out))
        fiber_page.batch_name.setText("check")
        fiber_page._generate()
        fiber_generated = bool(fiber_page.last_results) and all(
            (res.output_dir / "resin_inputs.csv").is_file()
            for res in fiber_page.last_results
        )
        if not fiber_generated:
            raise RuntimeError(f"Fiber generation self-check failed: {fiber_page.notice.text()}")

        window.select_module("texgen_modeling")
        texgen_page = window.page("texgen_modeling")
        texgen_page.geometry_editor.fields["name"].setText("PP1080")
        texgen_page.geometry_editor.fields["fabric_thickness_um"].setText("53")
        texgen_page.geometry_editor.fields["warp_width_mm"].setText("0.269")
        texgen_page.geometry_editor.fields["warp_spacing_mm"].setText("0.424")
        texgen_page.geometry_editor.fields["weft_width_mm"].setText("0.269")
        texgen_page.geometry_editor.fields["weft_spacing_mm"].setText("0.541")
        texgen_page.cases.table.item(0, 1).setText("83.5")
        texgen_page.output_path.setText(str(texgen_out))
        texgen_page.batch_suffix.setText("check")
        texgen_page._generate()
        texgen_generated = (
            texgen_page.script is not None
            and texgen_page.script.is_file()
            and (texgen_page.script.parent / "yarn_parameter_combinations.csv").is_file()
        )
        if not texgen_generated:
            raise RuntimeError(f"TexGen generation self-check failed: {texgen_page.notice.text()}")

        # Step 3 self-check: combine resin_inputs.csv with sample Mechanical_constants.txt.
        window.select_module("fiber_csv_export")
        bundle_page = window.page("fiber_csv_export")
        first_fiber_dir = fiber_page.last_results[0].output_dir
        (first_fiber_dir / "Mechanical_constants.txt").write_text(
            "\n".join(
                "57000 27400 27500 0.24 0.24 0.41 12000 12100 14000 5e-6 1.2e-5 1.3e-5"
                for _ in sample_resins
            )
            + "\n",
            encoding="utf-8",
        )
        bundle_page.load_result_dir(first_fiber_dir)
        bundle_csv = bundle_page.generate()
        bundle_generated = bundle_csv is not None and bundle_csv.is_file()
        if not bundle_generated:
            raise RuntimeError(f"Fiber bundle CSV self-check failed: {bundle_page.notice.text()}")

        # Step 4 self-check: load a TexGen model + bundle CSV and export standalone runner.
        window.select_module("abaqus_batch")
        abaqus_page = window.page("abaqus_batch")
        model_dir = texgen_page.script.parent / "PP1080_83p5"
        (model_dir / "1.inp").write_text("*Heading\n", encoding="utf-8")
        (model_dir / "1.ori").write_text("", encoding="utf-8")
        abaqus_page.load_models_dir(texgen_page.script.parent)
        abaqus_page.load_fiber_csv(bundle_csv)
        if not abaqus_page.abaqus_edit.text().strip():
            abaqus_page.abaqus_edit.setText("abaqus")
        runner_path = abaqus_page.export_script_to(tmp_root / "run_abaqus_batch.py")
        abaqus_runner_exported = runner_path.is_file()

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "frozen": is_frozen(),
        "modules": modules,
        "templates": template_groups,
        "sample_csv_count": len(sample_resins),
        "fiber_generated": fiber_generated,
        "texgen_generated": texgen_generated,
        "bundle_generated": bundle_generated,
        "abaqus_runner_exported": abaqus_runner_exported,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    window.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="材料多尺度建模处理工具箱")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-check", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    app = QApplication([sys.argv[0]])
    app.setOrganizationName("PPTools")
    app.setApplicationName("MaterialMultiscaleToolbox")
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    if sys.platform == "win32":
        app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(package_resource("ui/theme.qss").read_text(encoding="utf-8"))
    window = ToolboxWindow(args.output_dir)
    if args.self_check is not None:
        return _run_self_check(window, args.self_check)
    window.show()
    return app.exec()
