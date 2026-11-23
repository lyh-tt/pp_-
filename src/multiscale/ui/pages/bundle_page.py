from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
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
from ...fiber.bundle import (
    BUNDLE_CSV_COLUMNS,
    FiberBundleRow,
    build_fiber_bundle_rows,
    export_fiber_bundle_csv,
    inspect_fiber_result_dir,
    read_mechanical_constants,
)
from ...fiber.rve import ResinProperty, read_resin_csv
from ..widgets import card_layout, initial_output_dir, label, page_header, scroll_workspace
from .fiber_page import PasteResinDialog


class FiberBundlePage(QWidget):
    def __init__(self, default_output: Path | None = None) -> None:
        super().__init__(objectName="FiberBundlePage")
        self.settings = QSettings()
        self._default_output = default_output
        self._constants: list[tuple[float, ...]] = []
        self._resins: list[ResinProperty] = []
        self._rows: list[FiberBundleRow] = []
        self._constants_path: Path | None = None
        self._resin_source_label = "未检测到 resin_inputs.csv"
        self.last_csv: Path | None = None

        self.summary_badge = label("未加载数据", "MeshInfo")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(
            page_header(
                "3. 纤维束 CSV 生成",
                "读取第 1 步 Fiber RVE 求解完成的 Mechanical_constants.txt，生成单份纤维束等效性能 CSV",
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
        layout.addWidget(label("输入来源", "Section"))

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(label("Fiber 结果目录", "FieldCaption"))
        self.result_dir_edit = QLineEdit(
            readOnly=True,
            placeholderText="选择包含 Mechanical_constants.txt（及可选 resin_inputs.csv）的目录",
        )
        self.result_dir_edit.setAccessibleName("Fiber 结果目录")
        dir_row.addWidget(self.result_dir_edit, 1)
        choose_dir = QPushButton("选择目录…")
        choose_dir.clicked.connect(self._choose_result_dir)
        dir_row.addWidget(choose_dir)
        layout.addLayout(dir_row)

        resin_row = QHBoxLayout()
        resin_row.setSpacing(8)
        resin_row.addWidget(label("树脂参数来源", "FieldCaption"))
        self.resin_info = QLineEdit(
            readOnly=True,
            placeholderText="自动读取目录中的 resin_inputs.csv；旧目录可手动选择或粘贴",
        )
        self.resin_info.setAccessibleName("树脂参数来源")
        resin_row.addWidget(self.resin_info, 1)

        choose_resin = QPushButton("选择树脂 CSV…")
        choose_resin.clicked.connect(self._choose_resin_csv)
        resin_row.addWidget(choose_resin)

        paste_resin = QPushButton("粘贴树脂参数…")
        paste_resin.clicked.connect(self._paste_resins)
        resin_row.addWidget(paste_resin)
        layout.addLayout(resin_row)

        self.input_status = label(
            "请选择第 1 步计算完成后的 Fiber 结果目录，程序会自动匹配树脂输入与 12 列等效性能。",
            "Hint",
            wrap=True,
        )
        layout.addWidget(self.input_status)
        return card

    def _preview_card(self) -> QFrame:
        card = QFrame()
        layout = card_layout(card)
        layout.addWidget(label("合并结果预览", "Section"))

        headers = ("序号", *BUNDLE_CSV_COLUMNS)
        self.preview_table = QTableWidget(0, len(headers), objectName="BundlePreview")
        self.preview_table.setHorizontalHeaderLabels(headers)
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

        initial_dir = initial_output_dir(self.settings, "bundle_output_dir", default_output)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(label("输出 CSV", "FieldCaption"))
        self.output_csv_edit = QLineEdit(str(initial_dir / "fiber_bundle.csv"), readOnly=True)
        self.output_csv_edit.setAccessibleName("纤维束 CSV 输出路径")
        out_row.addWidget(self.output_csv_edit, 1)

        choose_out = QPushButton("更改…")
        choose_out.clicked.connect(self._choose_output_csv)
        out_row.addWidget(choose_out)

        generate_btn = QPushButton("生成纤维束 CSV", objectName="Primary")
        generate_btn.clicked.connect(self.generate)
        out_row.addWidget(generate_btn)
        layout.addLayout(out_row)

        status_row = QHBoxLayout()
        self.notice = label("加载有效的 Fiber 结果后即可一键导出。", "Notice", wrap=True)
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

    def _choose_result_dir(self) -> None:
        initial = self.settings.value("bundle_result_dir", str(Path.home()), type=str)
        selected = QFileDialog.getExistingDirectory(self, "选择 Fiber RVE 结果目录", initial)
        if selected:
            self.load_result_dir(Path(selected))

    def load_result_dir(self, path: Path) -> None:
        resolved = Path(path).resolve()
        self.result_dir_edit.setText(str(resolved))
        self.settings.setValue("bundle_result_dir", str(resolved))

        constants_path, resin_path = inspect_fiber_result_dir(resolved)
        self._constants_path = constants_path
        self._constants = []
        if constants_path is None:
            self._set_input_status("未在该目录找到 Mechanical_constants.txt。", error=True)
            self._rebuild_preview()
            return

        try:
            self._constants = read_mechanical_constants(constants_path)
        except (OSError, ValueError) as error:
            self._set_input_status(str(error), error=True)
            self._rebuild_preview()
            return

        if resin_path is not None:
            try:
                self._resins = read_resin_csv(resin_path)
                self._resin_source_label = f"{resin_path.name}（{len(self._resins)} 组）"
                self.resin_info.setText(self._resin_source_label)
            except (OSError, ValueError) as error:
                self._resins = []
                self._resin_source_label = f"{resin_path.name} 读取失败"
                self.resin_info.setText(self._resin_source_label)
                self._set_input_status(str(error), error=True)
                self._rebuild_preview()
                return
        elif not self._resins:
            self.resin_info.setText("未找到 resin_inputs.csv，请手动选择或粘贴树脂参数")

        if self._default_output is not None:
            target_dir = self._default_output
        else:
            target_dir = resolved
        self.output_csv_edit.setText(str(target_dir / f"{resolved.name}_fiber_bundle.csv"))
        self._rebuild_preview()

    def _choose_resin_csv(self) -> None:
        initial = self.result_dir_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择树脂参数 CSV", initial, "CSV (*.csv);;All Files (*.*)"
        )
        if path:
            self.load_resin_csv(Path(path))

    def load_resin_csv(self, path: Path) -> None:
        try:
            resins = read_resin_csv(Path(path))
        except (OSError, ValueError) as error:
            self._set_input_status(str(error), error=True)
            return
        self._resins = resins
        self._resin_source_label = f"{Path(path).name}（{len(resins)} 组）"
        self.resin_info.setText(self._resin_source_label)
        self._rebuild_preview()

    def _paste_resins(self) -> None:
        dialog = PasteResinDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.parsed:
            self.set_resins(dialog.parsed)

    def set_resins(self, resins: list[ResinProperty]) -> None:
        self._resins = list(resins)
        self._resin_source_label = f"手动粘贴（{len(self._resins)} 组）"
        self.resin_info.setText(self._resin_source_label)
        self._rebuild_preview()

    def _choose_output_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存纤维束等效性能 CSV", self.output_csv_edit.text(), "CSV (*.csv)"
        )
        if path:
            target = Path(path)
            if target.suffix.lower() != ".csv":
                target = target.with_suffix(".csv")
            self.output_csv_edit.setText(str(target))
            self.settings.setValue("bundle_output_dir", str(target.parent))

    def _rebuild_preview(self) -> None:
        self.preview_table.setRowCount(0)
        self._rows = []

        if self._constants_path is None:
            self.summary_badge.setText("未加载数据")
            return
        if not self._constants:
            self.summary_badge.setText("等效性能为空")
            return
        if not self._resins:
            self.summary_badge.setText(f"已有 {len(self._constants)} 行结果 · 待匹配树脂")
            self._set_input_status(
                f"已读取 Mechanical_constants.txt（{len(self._constants)} 行），请提供对应的 {len(self._constants)} 组树脂参数。",
                error=False,
            )
            return

        try:
            rows = build_fiber_bundle_rows(self._resins, self._constants)
        except ValueError as error:
            self.summary_badge.setText("行数不匹配")
            self._set_input_status(str(error), error=True)
            return

        self._rows = rows
        self.summary_badge.setText(f"已匹配 {len(rows)} 组纤维束参数")
        self._set_input_status(
            f"匹配成功：{len(rows)} 组树脂参数与 Mechanical_constants.txt 一一对应。",
            error=False,
        )

        self.preview_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                str(row_idx + 1),
                row.resin_name or "—",
                format_number(row.e_mpa),
                format_number(row.cte_e5),
                format_number(row.poisson),
                *(f"{val:.6g}" for val in (
                    row.e1, row.e2, row.e3,
                    row.v12, row.v13, row.v23,
                    row.g12, row.g13, row.g23,
                    row.cte_x, row.cte_y, row.cte_z,
                )),
            ]
            for col_idx, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.preview_table.setItem(row_idx, col_idx, item)

    def _set_input_status(self, text: str, *, error: bool) -> None:
        self.input_status.setStyleSheet("color: #a43d30;" if error else "")
        self.input_status.setText(text)

    def _notify(self, text: str, *, error: bool = False) -> None:
        self.notice.setStyleSheet("color: #a43d30;" if error else "")
        self.notice.setText(text)

    def generate(self) -> Path | None:
        try:
            if not self._rows:
                if self._constants_path is None:
                    raise ValueError("请先选择包含 Mechanical_constants.txt 的 Fiber 结果目录。")
                if not self._resins:
                    raise ValueError("请先提供与计算行数对应的树脂性能参数。")
                self._rows = build_fiber_bundle_rows(self._resins, self._constants)
            target = export_fiber_bundle_csv(self._rows, Path(self.output_csv_edit.text()))
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return None

        self.last_csv = target
        self.result.show()
        self._notify(f"已导出纤维束等效性能 CSV（共 {len(self._rows)} 组）：{target.name}")
        return target

    def _open_output_dir(self) -> None:
        if self.last_csv is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_csv.parent)))

    def _copy_csv_path(self) -> None:
        if self.last_csv is not None:
            QApplication.clipboard().setText(str(self.last_csv))
            self._notify(f"已复制 CSV 路径：{self.last_csv.name}")
