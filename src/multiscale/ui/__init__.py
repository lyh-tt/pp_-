import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ..common.resources import is_frozen, package_resource
from ..fiber.bundle import BUNDLE_CSV_COLUMNS
from ..fiber.rve import default_sample_resin_csv, read_resin_csv
from ..texgen.presets import BUILTIN_FABRIC_PRESETS
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
        project_root = Path(tmp) / "project_check"
        window.set_project_dir(project_root)

        # 第 1 步：生成 Fiber 求解文件，并直接准备标准 16 列 *_fiber_bundle.csv 联动下游
        fiber_page.template_selector.select_all()
        fiber_page.resin_editor.set_resins(sample_resins, replace=True)
        fiber_page.batch_name.setText("check")
        fiber_page._generate()
        fiber_generated = bool(fiber_page.last_results) and all(
            (res.output_dir / "resin_inputs.csv").is_file()
            for res in fiber_page.last_results
        )
        if not fiber_generated:
            raise RuntimeError(f"Fiber generation self-check failed: {fiber_page.notice.text()}")

        bundle_csv = window.workspace.layout.fiber_dir / "fiber_5um_check_fiber_bundle.csv"
        lines = [",".join(BUNDLE_CSV_COLUMNS)]
        for resin in sample_resins:
            lines.append(
                f"{resin.name},{resin.e_mpa},{resin.cte_e5},{resin.poisson},"
                "57000,27400,27500,0.24,0.24,0.41,12000,12100,14000,5e-6,1.2e-5,1.3e-5"
            )
        bundle_csv.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        window.workspace.notify_fiber_csv(bundle_csv)

        # 第 2 步：选择内置 PP1080 织物预设并生成 TexGen 批量建模脚本
        window.select_module("texgen_modeling")
        texgen_page = window.page("texgen_modeling")
        if not texgen_page.geometry_editor.select_preset("PP1080"):
            raise RuntimeError("Missing builtin PP1080 preset")
        texgen_page.cases.table.item(0, 1).setText("83.5")
        texgen_page.batch_suffix.setText("check")
        texgen_page._generate()
        texgen_generated = (
            texgen_page.script is not None
            and texgen_page.script.is_file()
            and (texgen_page.script.parent / "yarn_parameter_combinations.csv").is_file()
        )
        if not texgen_generated:
            raise RuntimeError(f"TexGen generation self-check failed: {texgen_page.notice.text()}")

        # 模拟 TexGenGUI 生成 .inp/.ori 后切换到第 3 步：自动发现模型与纤维束 CSV
        model_dir = texgen_page.script.parent / "PP1080_83p5"
        (model_dir / "1.inp").write_text("*Heading\n", encoding="utf-8")
        (model_dir / "1.ori").write_text("", encoding="utf-8")

        window.select_module("meso_solve")
        meso_page = window.page("meso_solve")
        if not meso_page.abaqus_edit.text().strip():
            meso_page.abaqus_edit.setText("abaqus")
        runner_path = meso_page.export_script_to(project_root / "run_abaqus_batch.py")
        abaqus_runner_exported = runner_path.is_file()

        # Tab 1：解析样本 .rpt 并导出汇总 CSV
        sample_rpt_dir = window.workspace.layout.rpt_dir / "PP1080_83p5"
        sample_rpt_dir.mkdir(parents=True, exist_ok=True)
        (sample_rpt_dir / "pp4p60451_6p04967e_5.rpt").write_text(
            "Effective material properties:\n"
            "E1: 1.6928e+10\nv12: 0.1215\nv13: 0.2096\n"
            "E2: 1.8713e+10\nv21: 0.1343\nv23: 0.2058\n"
            "E3: 8.2307e+09\nv31: 0.1019\nv32: 0.0905\n"
            "G23: 3.2066e+09\nG31: 3.2481e+09\nG12: 4.8510e+09\n"
            "CTE_1: 1.7132e-05\nCTE_2: 1.5303e-05\nCTE_3: 4.2350e-05\n",
            encoding="utf-8",
        )
        meso_page.load_rpt_dir(window.workspace.layout.rpt_dir)
        summary_csv = meso_page.generate_summary()
        rpt_summary_exported = summary_csv is not None and summary_csv.is_file()
        if not rpt_summary_exported:
            raise RuntimeError(f"RPT summary self-check failed: {meso_page.rpt_notice.text()}")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "frozen": is_frozen(),
        "modules": modules,
        "templates": template_groups,
        "fabric_presets": [p.fabric.name for p in BUILTIN_FABRIC_PRESETS],
        "sample_csv_count": len(sample_resins),
        "fiber_generated": fiber_generated,
        "texgen_generated": texgen_generated,
        "abaqus_runner_exported": abaqus_runner_exported,
        "rpt_summary_exported": rpt_summary_exported,
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
