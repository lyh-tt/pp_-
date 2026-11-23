from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...common.utils import format_number
from ...post.rpt import (
    RptSummaryRow,
    export_rpt_summary_csv,
    summarize_rpt_dir,
)
from ..widgets import card_layout, initial_output_dir, label, page_header, scroll_workspace

_PREVIEW_COLUMNS = (
    "序号",
    "case",
    "model",
    "thickness_um",
    "resin",
    "resin_E_MPa",
    "resin_CTE_ppm_per_K",
    "E1_MPa",
    "E2_MPa",
    "E3_MPa",
    "v12",
    "v13",
    "v23",
    "G12_MPa",
    "G13_MPa",
    "G23_MPa",
    "CTE_X_ppm_per_K",
    "CTE_Y_ppm_per_K",
    "CTE_Z_ppm_per_K",
)


class RptPostPage(QWidget):
    def __init__(self, default_output: Path | None = None) -> None:
        super().__init__(objectName="RptPostPage")
        self.settings = QSettings()
        self._default_output = default_output
        self._rows: list[RptSummaryRow] = []
        self._fiber_csv: Path | None = None
        self.last_csv: Path | None = None

        self.summary_badge = label("未加载 RPT", "MeshInfo")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(
            page_header(
                "5. RPT 后处理",
                "批量解析各厚度与树脂工况 .rpt 等效性能（Pa→MPa、/K→ppm/K），导出汇总 CSV",
                self.summary_badge,
            )
        )
        root.addWidget(
            scroll_workspace(self._source_card(), self._preview_card()),
            1,
        )
        root.addWidget(self._footer(default_output))

    def _source_card(self) -> QFrame:
        card = QFrame()
        layout = card_layout(card)
        layout.addWidget(label("RPT 输入目录", "Section"))

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(label("RPT 结果目录", "FieldCaption"))
        self.rpt_dir_edit = QLineEdit(
            readOnly=True,
            placeholderText="选择第 4 步输出的 RPT 根目录或单个模型目录（自动递归扫描 *.rpt）",
        )
        self.rpt_dir_edit.setAccessibleName("RPT 结果目录")
        dir_row.addWidget(self.rpt_dir_edit, 1)

        choose_dir = QPushButton("选择目录…")
        choose_dir.clicked.connect(self._choose_rpt_dir)
        dir_row.addWidget(choose_dir)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._reload_rpt_dir)
        dir_row.addWidget(refresh_btn)
        layout.addLayout(dir_row)

        csv_row = QHBoxLayout()
        csv_row.setSpacing(8)
        csv_row.addWidget(label("纤维束 CSV（可选）", "FieldCaption"))
        self.csv_path_edit = QLineEdit(
            readOnly=True,
            placeholderText="可选导入第 3 步纤维束 CSV，以补充匹配的树脂牌号与泊松比",
        )
        self.csv_path_edit.setAccessibleName("RPT 匹配纤维束 CSV")
        csv_row.addWidget(self.csv_path_edit, 1)

        choose_csv = QPushButton("导入 CSV…")
        choose_csv.clicked.connect(self._choose_fiber_csv)
        csv_row.addWidget(choose_csv)

        clear_csv = QPushButton("清除")
        clear_csv.clicked.connect(self._clear_fiber_csv)
        csv_row.addWidget(clear_csv)
        layout.addLayout(csv_row)

        self.input_status = label(
            "选择第 4 步求解输出目录即可自动解析全部 pp*.rpt 文件；若未导入纤维束 CSV，也会从文件名自动反推树脂 E 与 CTE。",
            "Hint",
            wrap=True,
        )
        layout.addWidget(self.input_status)
        return card

    def _preview_card(self) -> QFrame:
        card = QFrame()
        layout = card_layout(card)
        layout.addWidget(label("汇总结果预览", "Section"))

        self.preview_table = QTableWidget(0, len(_PREVIEW_COLUMNS), objectName="RptPreview")
        self.preview_table.setHorizontalHeaderLabels(_PREVIEW_COLUMNS)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setShowGrid(False)
        self.preview_table.setMinimumHeight(210)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.verticalHeader().setDefaultSectionSize(32)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.preview_table, 1)
        return card

    def _footer(self, default_output: Path | None) -> QFrame:
        frame = QFrame(objectName="Footer")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(7)

        initial_dir = initial_output_dir(self.settings, "rpt_output_dir", default_output)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(label("汇总 CSV", "FieldCaption"))
        self.output_csv_edit = QLineEdit(
            str(initial_dir / "effective_properties_summary.csv"), readOnly=True
        )
        self.output_csv_edit.setAccessibleName("RPT 汇总 CSV 输出路径")
        out_row.addWidget(self.output_csv_edit, 1)

        choose_out = QPushButton("更改…")
        choose_out.clicked.connect(self._choose_output_csv)
        out_row.addWidget(choose_out)

        export_btn = QPushButton("导出汇总 CSV", objectName="Primary")
        export_btn.clicked.connect(self.generate)
        out_row.addWidget(export_btn)
        layout.addLayout(out_row)

        status_row = QHBoxLayout()
        self.notice = label("加载 RPT 目录后即可一键导出结构化汇总表。", "Notice", wrap=True)
        status_row.addWidget(self.notice, 1)

        self.result = QWidget()
        buttons = QHBoxLayout(self.result)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        for title, handler in (
            ("打开所在目录", self._open_output_dir),
            ("复制 CSV 路径", self._copy_csv_path),
        ):
            btn = QPushButton(title)
            btn.clicked.connect(handler)
            buttons.addWidget(btn)
        self.result.hide()
        status_row.addWidget(self.result)
        layout.addLayout(status_row)
        return frame

    def _choose_rpt_dir(self) -> None:
        initial = self.settings.value("rpt_source_dir", str(Path.home()), type=str)
        selected = QFileDialog.getExistingDirectory(self, "选择 RPT 结果目录", initial)
        if selected:
            self.load_rpt_dir(Path(selected))

    def _reload_rpt_dir(self) -> None:
        text = self.rpt_dir_edit.text().strip()
        if text:
            self.load_rpt_dir(Path(text))

    def load_rpt_dir(self, path: Path) -> None:
        resolved = Path(path).resolve()
        self.rpt_dir_edit.setText(str(resolved))
        self.settings.setValue("rpt_source_dir", str(resolved))
        if self._default_output is None:
            self.output_csv_edit.setText(str(resolved / "effective_properties_summary.csv"))
        else:
            self.output_csv_edit.setText(str(self._default_output / "effective_properties_summary.csv"))
        self._refresh()

    def _choose_fiber_csv(self) -> None:
        initial = self.rpt_dir_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择纤维束 CSV（可选）", initial, "CSV (*.csv);;All Files (*.*)"
        )
        if path:
            self.load_fiber_csv(Path(path))

    def load_fiber_csv(self, path: Path) -> None:
        resolved = Path(path).resolve()
        self._fiber_csv = resolved
        self.csv_path_edit.setText(str(resolved))
        self._refresh()

    def _clear_fiber_csv(self) -> None:
        self._fiber_csv = None
        self.csv_path_edit.clear()
        self._refresh()

    def _refresh(self) -> None:
        self.preview_table.setRowCount(0)
        self._rows = []

        dir_text = self.rpt_dir_edit.text().strip()
        if not dir_text:
            self.summary_badge.setText("未加载 RPT")
            return

        try:
            rows, warnings = summarize_rpt_dir(Path(dir_text), fiber_csv=self._fiber_csv)
        except (OSError, ValueError) as error:
            self.summary_badge.setText("加载失败")
            self._set_input_status(str(error), error=True)
            return

        if not rows:
            self.summary_badge.setText("未找到有效 RPT")
            msg = "未在该目录下发现有效 .rpt 文件。"
            if warnings:
                msg += f" 跳过 {len(warnings)} 个异常文件：{warnings[0]}"
            self._set_input_status(msg, error=bool(warnings))
            return

        self._rows = rows
        self.summary_badge.setText(f"已解析 {len(rows)} 条 RPT")
        status_msg = f"解析成功：共 {len(rows)} 条 RPT 记录"
        if self._fiber_csv is not None:
            matched_count = sum(1 for r in rows if r.resin)
            status_msg += f"（已匹配纤维束牌号 {matched_count} 条）"
        if warnings:
            status_msg += f"；另跳过 {len(warnings)} 个格式异常文件"
        self._set_input_status(status_msg + "。", error=False)

        self.preview_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                str(row_idx + 1),
                row.case,
                row.model,
                "—" if row.thickness_um is None else format_number(row.thickness_um),
                row.resin or "—",
                "—" if row.resin_e_mpa is None else format_number(row.resin_e_mpa),
                "—" if row.resin_cte_ppm is None else format_number(row.resin_cte_ppm),
                *(f"{val:.6g}" for val in (
                    row.e1_mpa, row.e2_mpa, row.e3_mpa,
                    row.v12, row.v13, row.v23,
                    row.g12_mpa, row.g13_mpa, row.g23_mpa,
                    row.cte_x_ppm, row.cte_y_ppm, row.cte_z_ppm,
                )),
            ]
            for col_idx, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.preview_table.setItem(row_idx, col_idx, item)

    def _choose_output_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 RPT 汇总 CSV", self.output_csv_edit.text(), "CSV (*.csv)"
        )
        if path:
            target = Path(path)
            if target.suffix.lower() != ".csv":
                target = target.with_suffix(".csv")
            self.output_csv_edit.setText(str(target))
            self.settings.setValue("rpt_output_dir", str(target.parent))

    def generate(self) -> Path | None:
        try:
            if not self._rows:
                raise ValueError("请先加载包含有效 .rpt 文件的目录。")
            target = export_rpt_summary_csv(self._rows, Path(self.output_csv_edit.text()))
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return None

        self.last_csv = target
        self.result.show()
        self._notify(f"已导出 RPT 汇总 CSV（共 {len(self._rows)} 条）：{target.name}")
        return target

    def _set_input_status(self, text: str, *, error: bool) -> None:
        self.input_status.setStyleSheet("color: #a43d30;" if error else "")
        self.input_status.setText(text)

    def _notify(self, text: str, *, error: bool = False) -> None:
        self.notice.setStyleSheet("color: #a43d30;" if error else "")
        self.notice.setText(text)

    def _open_output_dir(self) -> None:
        if self.last_csv is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_csv.parent)))

    def _copy_csv_path(self) -> None:
        if self.last_csv is not None:
            QApplication.clipboard().setText(str(self.last_csv))
            self._notify(f"已复制汇总 CSV 路径：{self.last_csv.name}")
