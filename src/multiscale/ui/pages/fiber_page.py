import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...common.abaqus_utils import detect_abaqus_command, non_ascii_path_warning
from ...common.resources import is_frozen
from ...common.utils import format_number
from ...fiber.rve import (
    FiberTemplate,
    ResinProperty,
    default_fiber_templates_root,
    default_sample_resin_csv,
    discover_fiber_templates,
    generate_fiber_cases,
    parse_resin_text,
    plan_fiber_output_dirs,
    read_resin_csv,
    write_sample_resin_csv,
)
from ...fiber.solver import FiberBatchSummary, FiberSolveConfig, run_fiber_solve_batch
from ..widgets import card_layout, label
from ..workspace import ProjectContext


class FiberTemplateSelector(QFrame):
    changed = Signal()

    def __init__(self, initial_root: Path | None = None) -> None:
        super().__init__()
        self._templates_root = (
            Path(initial_root) if initial_root is not None else default_fiber_templates_root()
        )
        self._templates: list[FiberTemplate] = []
        self._checkboxes: list[tuple[FiberTemplate, QCheckBox]] = []

        layout = card_layout(self)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label("RVE 模板分组", "Section"))
        header.addWidget(label("内置纤维 RVE 模板库，按单丝直径分组", "Hint"))
        header.addStretch()

        for title, handler in (
            ("全选", self.select_all),
            ("清空选择", self.clear_selection),
            ("刷新", self.reload),
        ):
            btn = QPushButton(title)
            btn.clicked.connect(handler)
            header.addWidget(btn)
        layout.addLayout(header)

        self.groups_host = QWidget()
        self.groups_layout = QGridLayout(self.groups_host)
        self.groups_layout.setContentsMargins(0, 2, 0, 0)
        self.groups_layout.setHorizontalSpacing(10)
        self.groups_layout.setVerticalSpacing(10)
        layout.addWidget(self.groups_host)

        self.empty_note = label("", "Hint", wrap=True)
        layout.addWidget(self.empty_note)

        self.reload()

    def templates(self) -> list[FiberTemplate]:
        return list(self._templates)

    def selected_templates(self) -> list[FiberTemplate]:
        return [tmpl for tmpl, box in self._checkboxes if box.isChecked()]

    def select_all(self) -> None:
        for _tmpl, box in self._checkboxes:
            with QSignalBlocker(box):
                box.setChecked(True)
        self.changed.emit()

    def clear_selection(self) -> None:
        for _tmpl, box in self._checkboxes:
            with QSignalBlocker(box):
                box.setChecked(False)
        self.changed.emit()

    def set_templates_root(self, path: Path) -> None:
        self._templates_root = Path(path).resolve()
        self.reload()

    def reload(self) -> None:
        previously_checked = {
            str(tmpl.path) for tmpl, box in self._checkboxes if box.isChecked()
        }
        while self.groups_layout.count():
            item = self.groups_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checkboxes.clear()

        self._templates = discover_fiber_templates(self._templates_root)

        if not self._templates:
            self.groups_host.hide()
            self.empty_note.show()
            self.empty_note.setText(
                "未检测到内置 Fiber RVE 模板，请联系维护人员检查工具箱模板配置。"
            )
            self.changed.emit()
            return

        self.empty_note.hide()
        self.groups_host.show()

        grouped: dict[str, list[FiberTemplate]] = {}
        for tmpl in self._templates:
            grouped.setdefault(tmpl.group, []).append(tmpl)

        columns = min(3, len(grouped))
        for col in range(columns):
            self.groups_layout.setColumnStretch(col, 1)

        for idx, (group_name, items) in enumerate(grouped.items()):
            card = QFrame(objectName="TemplateGroup")
            box = QVBoxLayout(card)
            box.setContentsMargins(12, 10, 12, 10)
            box.setSpacing(6)
            box.addWidget(label(f"{group_name} μm 纤维模板", "FieldCaption"))

            for tmpl in items:
                size_text = (
                    f"单胞 {format_number(tmpl.cell_size_um)} μm"
                    if tmpl.cell_size_um is not None
                    else "单胞尺寸未知"
                )
                checkbox = QCheckBox(f"{tmpl.name}（{size_text} · {tmpl.inp_count} 个 INP）")
                checkbox.setChecked(str(tmpl.path) in previously_checked)
                checkbox.setToolTip(
                    f"{group_name} μm 纤维模板 · {size_text} · {tmpl.inp_count} 个 INP"
                )
                checkbox.toggled.connect(self.changed)
                self._checkboxes.append((tmpl, checkbox))
                box.addWidget(checkbox)

            row, col = divmod(idx, columns)
            self.groups_layout.addWidget(card, row, col)

        self.changed.emit()


class PasteResinDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量粘贴树脂性能")
        self.resize(540, 380)
        self.parsed: list[ResinProperty] = []
        self.replace_existing = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        layout.addWidget(
            label(
                "支持直接粘贴旧 APP 的元组列表或 Excel/文本表格（3 列：E, CTE, ν；或 4 列：树脂牌号, E, CTE, ν）：",
                "FieldCaption",
                wrap=True,
            )
        )
        self.editor = QPlainTextEdit(objectName="ResinPasteInput")
        self.editor.setPlaceholderText(
            "示例 1（旧 APP 格式）：\n"
            "(4604.51, 6.04967, 0.38),\n"
            "(4030.56, 5.27133, 0.38),\n\n"
            "示例 2（带牌号，逗号/Tab/空格分隔）：\n"
            "S8NE, 4604.51, 6.04967, 0.38\n"
            "H350, 4030.56, 5.27133, 0.38"
        )
        layout.addWidget(self.editor, 1)

        self.error_label = label("", "Hint", wrap=True)
        self.error_label.setStyleSheet("color: #a43d30;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)

        self.append_btn = QPushButton("追加到末尾")
        self.append_btn.clicked.connect(lambda: self._confirm(replace=False))
        actions.addWidget(self.append_btn)

        self.replace_btn = QPushButton("替换当前列表", objectName="Primary")
        self.replace_btn.clicked.connect(lambda: self._confirm(replace=True))
        actions.addWidget(self.replace_btn)
        layout.addLayout(actions)

    def _confirm(self, *, replace: bool) -> None:
        try:
            self.parsed = parse_resin_text(self.editor.toPlainText())
        except ValueError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.replace_existing = replace
        self.accept()


class ResinTableEditor(QFrame):
    changed = Signal()
    import_requested = Signal()
    sample_csv_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = card_layout(self)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label("树脂性能参数", "Section"))
        header.addStretch()

        add_btn = QPushButton("添加行")
        add_btn.clicked.connect(self._append_row)
        header.addWidget(add_btn)

        self.remove_btn = QPushButton("删除选中")
        self.remove_btn.clicked.connect(self._remove_row)
        header.addWidget(self.remove_btn)

        paste_btn = QPushButton("批量粘贴…")
        paste_btn.clicked.connect(self._open_paste_dialog)
        header.addWidget(paste_btn)

        import_btn = QPushButton("导入 CSV…")
        import_btn.clicked.connect(self.import_requested)
        header.addWidget(import_btn)

        sample_btn = QPushButton("示例 CSV…")
        sample_btn.clicked.connect(self.sample_csv_requested)
        header.addWidget(sample_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self.clear_rows)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        layout.addWidget(
            label(
                "说明：微观 Fiber RVE 与细观 TexGen 使用同一种树脂体系。E 单位为 MPa，"
                "CTE 单位为 10⁻⁵ /K（例如 6.04967 表示 6.04967×10⁻⁵ /K），泊松比 ν 须介于 -1 和 0.5 之间。",
                "Hint",
                wrap=True,
            )
        )

        self.table = QTableWidget(0, 5, objectName="ResinCases")
        self.table.setHorizontalHeaderLabels(
            ("序号", "树脂牌号（可选）", "模量 E（MPa）", "CTE（10⁻⁵ /K）", "泊松比 ν")
        )
        self.table.setMinimumHeight(200)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)

        headings = self.table.horizontalHeader()
        for col, width in ((0, 56), (1, 130)):
            headings.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)
        for col in (2, 3, 4):
            headings.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

        self.table.currentCellChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, 1)

        self.add_row()
        self.table.setCurrentCell(0, 2)

    @property
    def selected_row(self) -> int:
        return self.table.currentRow()

    def row_count(self) -> int:
        return self.table.rowCount()

    def filled_row_count(self) -> int:
        count = 0
        for row in range(self.table.rowCount()):
            if any(self._cell_text(row, col) for col in range(1, 5)):
                count += 1
        return count

    def add_row(
        self,
        name: str = "",
        e_text: str = "",
        cte_text: str = "",
        poisson_text: str = "",
    ) -> int:
        with QSignalBlocker(self.table):
            row = self.table.rowCount()
            self.table.insertRow(row)

            serial = QTableWidgetItem(str(row + 1))
            serial.setFlags(serial.flags() & ~Qt.ItemFlag.ItemIsEditable)
            serial.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, serial)

            for col, text in enumerate((name, e_text, cte_text, poisson_text), start=1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

        self._selection_changed()
        self.changed.emit()
        return row

    def set_resins(self, resins: list[ResinProperty], *, replace: bool = True) -> None:
        with QSignalBlocker(self.table):
            if replace:
                self.table.setRowCount(0)
            elif (
                self.table.rowCount() == 1
                and not any(self._cell_text(0, col) for col in range(1, 5))
            ):
                self.table.setRowCount(0)

            for resin in resins:
                row = self.table.rowCount()
                self.table.insertRow(row)
                serial = QTableWidgetItem(str(row + 1))
                serial.setFlags(serial.flags() & ~Qt.ItemFlag.ItemIsEditable)
                serial.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 0, serial)

                values = (
                    resin.name,
                    format_number(resin.e_mpa),
                    format_number(resin.cte_e5),
                    format_number(resin.poisson),
                )
                for col, text in enumerate(values, start=1):
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row, col, item)

        self._refresh_serials()
        self._selection_changed()
        self.changed.emit()

    def clear_rows(self) -> None:
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
        self.add_row()
        self.table.setCurrentCell(0, 2)

    def resins(self) -> list[ResinProperty]:
        rows = self.table.rowCount()
        if rows == 0:
            raise ValueError("请添加至少一组树脂性能参数。")
        if rows == 1 and not any(self._cell_text(0, col) for col in range(1, 5)):
            raise ValueError("请填写至少一组树脂性能参数（E、CTE 和泊松比）。")

        result: list[ResinProperty] = []
        for row in range(rows):
            name = self._cell_text(row, 1)
            raw_e = self._cell_text(row, 2)
            raw_cte = self._cell_text(row, 3)
            raw_v = self._cell_text(row, 4)
            if not (raw_e and raw_cte and raw_v):
                raise ValueError(
                    f"请完整填写第 {row + 1} 行的 E、CTE 和泊松比（或删除多余空行）。"
                )
            try:
                e_mpa = float(raw_e)
                cte_e5 = float(raw_cte)
                poisson = float(raw_v)
            except ValueError as error:
                raise ValueError(
                    f"第 {row + 1} 行的 E、CTE 和泊松比必须为有效数值。"
                ) from error
            try:
                result.append(
                    ResinProperty(e_mpa=e_mpa, cte_e5=cte_e5, poisson=poisson, name=name)
                )
            except ValueError as error:
                raise ValueError(f"第 {row + 1} 行：{error}") from error
        return result

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item is not None else ""

    def _refresh_serials(self) -> None:
        with QSignalBlocker(self.table):
            for row in range(self.table.rowCount()):
                self.table.item(row, 0).setText(str(row + 1))

    def _selection_changed(self) -> None:
        self.remove_btn.setEnabled(self.selected_row >= 0)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() >= 1:
            self.changed.emit()

    def _append_row(self) -> None:
        row = self.add_row()
        self.table.setCurrentCell(row, 2)
        self.table.editItem(self.table.item(row, 2))

    def _remove_row(self) -> None:
        row = self.selected_row
        if row < 0:
            return
        with QSignalBlocker(self.table):
            self.table.removeRow(row)
        self._refresh_serials()
        self._selection_changed()
        self.changed.emit()

    def _open_paste_dialog(self) -> None:
        dialog = PasteResinDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.parsed:
            self.set_resins(dialog.parsed, replace=dialog.replace_existing)


class _FiberSolveWorker(QThread):
    progress_changed = Signal(int, int, str)
    finished_summary = Signal(object)
    failed_error = Signal(str)

    def __init__(
        self,
        templates: list[FiberTemplate],
        resins: list[ResinProperty],
        output_parent: Path,
        batch_name: str,
        config: FiberSolveConfig,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._templates = templates
        self._resins = resins
        self._output_parent = output_parent
        self._batch_name = batch_name
        self._config = config
        self._cancel_event = cancel_event

    def run(self) -> None:
        try:
            summary = run_fiber_solve_batch(
                self._templates,
                self._resins,
                self._output_parent,
                self._batch_name,
                self._config,
                on_progress=lambda done, total, msg: self.progress_changed.emit(done, total, msg),
                cancel_event=self._cancel_event,
            )
            self.finished_summary.emit(summary)
        except Exception as error:
            self.failed_error.emit(str(error))


class FiberRvePage(QWidget):
    def __init__(
        self,
        default_output: Path | None = None,
        workspace: ProjectContext | None = None,
    ) -> None:
        super().__init__(objectName="FiberRvePage")
        self.settings = QSettings()
        self.workspace = workspace or ProjectContext(default_output, parent=self)
        self.last_results: list[object] = []
        self._worker: _FiberSolveWorker | None = None
        self._cancel_event = threading.Event()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(self._workspace(), 1)
        root.addWidget(self._footer())

        self.template_selector.changed.connect(self._refresh_state)
        self.resin_editor.changed.connect(self._refresh_state)
        self.resin_editor.import_requested.connect(self._choose_resin_csv)
        self.resin_editor.sample_csv_requested.connect(self._export_sample_csv)
        self.workspace.project_dir_changed.connect(self._sync_project_dir)
        self._refresh_state()

    def _header(self) -> QFrame:
        frame = QFrame(objectName="PageHeader")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(24, 16, 24, 16)

        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(label("1. Fiber 微观求解", "PageTitle"))
        titles.addWidget(
            label(
                "勾选直径模板与树脂参数，默认 7 方向并行求解并自动产出纤维束 CSV（自动联动至第 3 步）",
                "PageSubtitle",
            )
        )
        layout.addLayout(titles)
        layout.addStretch()

        self.summary_badge = label("", "MeshInfo")
        layout.addWidget(self.summary_badge)
        return frame

    def _workspace(self) -> QScrollArea:
        content = QWidget(objectName="Content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        self.template_selector = FiberTemplateSelector()
        self.resin_editor = ResinTableEditor()
        layout.addWidget(self.template_selector)
        layout.addWidget(self.resin_editor, 1)

        scroll = QScrollArea(objectName="WorkspaceScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _footer(self) -> QFrame:
        frame = QFrame(objectName="Footer")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(7)

        initial = self.workspace.layout.fiber_dir

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(label("输出目录", "FieldCaption"))
        self.output_path = QLineEdit(str(initial), readOnly=True)
        self.output_path.setAccessibleName("Fiber 输出目录")
        actions.addWidget(self.output_path, 1)

        choose_output = QPushButton("更改…")
        choose_output.clicked.connect(self._choose_output)
        actions.addWidget(choose_output)
        layout.addLayout(actions)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(8)
        batch_row.addWidget(label("批次名称", "FieldCaption"))
        self.batch_prefix = label("fiber_<直径>um_", "PrefixBadge")
        batch_row.addWidget(self.batch_prefix)
        self.batch_name = QLineEdit(
            objectName="fiber_batch_name",
            placeholderText="可选树脂批次名（如 S8NE_H350），留空按时间自动命名",
        )
        self.batch_name.setAccessibleName("Fiber 批次名称")
        batch_row.addWidget(self.batch_name, 1)

        self.bundle_btn = QPushButton("合成已有 CSV…")
        self.bundle_btn.setToolTip("读取已完成的 Mechanical_constants.txt 合成为纤维束 CSV")
        self.bundle_btn.clicked.connect(self._open_bundle_dialog)
        batch_row.addWidget(self.bundle_btn)

        self.generate_btn = QPushButton("生成求解文件")
        self.generate_btn.clicked.connect(self._generate)
        batch_row.addWidget(self.generate_btn)

        self.stop_btn = QPushButton("停止求解")
        self.stop_btn.hide()
        self.stop_btn.clicked.connect(self._stop_solve)
        batch_row.addWidget(self.stop_btn)

        self.solve_btn = QPushButton("开始本机求解", objectName="Primary")
        self.solve_btn.clicked.connect(self._start_solve)
        batch_row.addWidget(self.solve_btn)
        layout.addLayout(batch_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)

        status_row = QHBoxLayout()
        self.notice = label(
            "勾选 Fiber 模板并填写树脂参数后，可直接本机 7 方向并行求解或导出求解文件。",
            "Notice",
            wrap=True,
        )
        status_row.addWidget(self.notice, 1)

        self.result = QWidget()
        buttons = QHBoxLayout(self.result)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        for title, handler in (
            ("打开输出目录", self._open_output),
            ("复制目录路径", self._copy_paths),
        ):
            action = QPushButton(title)
            action.clicked.connect(handler)
            buttons.addWidget(action)
        self.result.hide()
        status_row.addWidget(self.result)
        layout.addLayout(status_row)
        return frame

    def _refresh_state(self) -> None:
        selected = self.template_selector.selected_templates()
        resin_count = self.resin_editor.filled_row_count()
        self.summary_badge.setText(f"已选 {len(selected)} 个模板   /   {resin_count} 组树脂")

        if not selected:
            self.batch_prefix.setText("fiber_<直径>um_")
        elif len(selected) == 1:
            self.batch_prefix.setText(f"fiber_{selected[0].group}um_")
        else:
            groups = "/".join(dict.fromkeys(f"{t.group}um" for t in selected))
            self.batch_prefix.setText(f"fiber_{groups}_")

    def _sync_project_dir(self, _project_root: Path) -> None:
        self.output_path.setText(str(self.workspace.layout.fiber_dir))

    def _choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择 Fiber RVE 输出目录", self.output_path.text()
        )
        if selected:
            output = str(Path(selected))
            self.output_path.setText(output)

    def _open_bundle_dialog(self) -> None:
        from .bundle_dialog import FiberBundleDialog

        dialog = FiberBundleDialog(Path(self.output_path.text()), self)
        dialog.exec()
        if dialog.last_csv is not None and dialog.last_csv.is_file():
            self.workspace.notify_fiber_csv(dialog.last_csv)
            self._notify(f"已合成纤维束 CSV 并自动关联至第 3 步：{dialog.last_csv.name}")

    def _choose_resin_csv(self) -> None:
        saved_csv = self.settings.value("fiber_resin_csv", "", type=str)
        sample_dir = default_sample_resin_csv().parent
        if saved_csv and Path(saved_csv).parent.is_dir():
            initial = saved_csv
        elif not is_frozen() and sample_dir.is_dir():
            initial = str(sample_dir)
        else:
            initial = self.output_path.text()

        path, _ = QFileDialog.getOpenFileName(
            self, "导入树脂性能 CSV", initial, "CSV (*.csv);;All Files (*.*)"
        )
        if not path:
            return
        try:
            resins = read_resin_csv(Path(path))
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return
        self.resin_editor.set_resins(resins, replace=True)
        self.settings.setValue("fiber_resin_csv", path)
        self._notify(f"已导入 {len(resins)} 组树脂性能参数。")

    def _export_sample_csv(self) -> None:
        default_target = str(Path(self.output_path.text()) / "resin_inputs_example.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存树脂性能示例 CSV", default_target, "CSV (*.csv)"
        )
        if not path:
            return
        try:
            saved = write_sample_resin_csv(Path(path))
        except OSError as error:
            self._notify(str(error), error=True)
            return
        self._notify(f"已导出示例 CSV：{saved}")

    def _generate(self) -> None:
        try:
            templates = self.template_selector.selected_templates()
            resins = self.resin_editor.resins()
            results = generate_fiber_cases(
                templates,
                resins,
                Path(self.output_path.text()),
                batch_name=self.batch_name.text(),
            )
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return

        self.last_results = results
        self.result.show()
        names = "、".join(item.output_dir.name for item in results)
        msg = f"已生成 {len(results)} 个 Fiber 求解目录（附 7 方向并行脚本，共 {len(resins)} 组树脂）：{names}"
        warn = non_ascii_path_warning(Path(self.output_path.text()))
        if warn:
            msg = f"{msg}\n{warn}"
        self._notify(msg)

    def _resolve_abaqus_command(self) -> str | None:
        saved = self.settings.value("abaqus_command", "", type=str).strip()
        cmd = saved or detect_abaqus_command()
        if cmd:
            return cmd
        path, _ = QFileDialog.getOpenFileName(
            self,
            "请选择 Abaqus 启动命令（如 abq2026.bat / abaqus.bat）",
            "D:/",
            "Batch / Executable (*.bat *.cmd *.exe);;All Files (*)",
        )
        if not path:
            return None
        resolved = str(Path(path))
        self.settings.setValue("abaqus_command", resolved)
        return resolved

    def _start_solve(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            templates = self.template_selector.selected_templates()
            resins = self.resin_editor.resins()
            out_parent = Path(self.output_path.text())
            plan_fiber_output_dirs(templates, out_parent, self.batch_name.text())
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return

        abaqus_cmd = self._resolve_abaqus_command()
        if not abaqus_cmd:
            self._notify("未找到 Abaqus 启动命令，已取消本机求解。", error=True)
            return

        # 默认 7 方向同时并发提交，总内存上限自动控制 <= 总物理内存 90%
        config = FiberSolveConfig(abaqus_command=abaqus_cmd)
        total_ticks = len(templates) * 7
        self._cancel_event.clear()
        self.progress.setRange(0, total_ticks)
        self.progress.setValue(0)
        self.progress.setFormat(f"%v / {total_ticks}")
        self._set_running(True)
        self._notify(f"正在 7 方向并行求解（{len(templates)} 个模板，总内存自动控制 ≤ 90%）…")

        self._worker = _FiberSolveWorker(
            templates,
            resins,
            out_parent,
            self.batch_name.text(),
            config,
            self._cancel_event,
        )
        self._worker.progress_changed.connect(self._on_solve_progress)
        self._worker.finished_summary.connect(self._on_solve_finished)
        self._worker.failed_error.connect(self._on_solve_error)
        self._worker.start()

    def _stop_solve(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self.stop_btn.setEnabled(False)
            self._notify("正在终止 Abaqus 子进程并清理临时目录…")

    def _on_solve_progress(self, done: int, total: int, msg: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self._notify(f"正在 7 方向并行求解（{done}/{total}）：{msg}")

    def _on_solve_finished(self, summary: FiberBatchSummary) -> None:
        self._set_running(False)
        succeeded = [res for res in summary.results if res.status == "succeeded"]
        if succeeded:
            self.last_results = succeeded
            self.result.show()
            csv_targets = [
                res.bundle_csv_path
                for res in succeeded
                if res.bundle_csv_path is not None and res.bundle_csv_path.is_file()
            ]
            if csv_targets:
                self.workspace.notify_fiber_csv(csv_targets[-1])
        first_err = next((res.message for res in summary.results if res.status == "failed" and res.message), "")
        msg = (
            f"Fiber 并行求解完成：成功 {summary.succeeded}，失败 {summary.failed}，取消 {summary.cancelled}（共 {summary.total}）。"
        )
        if succeeded:
            msg += " 已直接产出 Mechanical_constants.txt 与 *_fiber_bundle.csv，并已自动关联至第 3 步。"
        if first_err:
            msg += f" 失败原因：{first_err}"
        self._notify(msg, error=summary.failed > 0)

    def _on_solve_error(self, message: str) -> None:
        self._set_running(False)
        self._notify(f"求解异常：{message}", error=True)

    def _set_running(self, running: bool) -> None:
        self.bundle_btn.setEnabled(not running)
        self.generate_btn.setEnabled(not running)
        self.solve_btn.setEnabled(not running)
        self.stop_btn.setVisible(running)
        self.stop_btn.setEnabled(running)
        self.progress.setVisible(running)

    def _notify(self, text: str, *, error: bool = False) -> None:
        self.notice.setStyleSheet("color: #a43d30;" if error else "")
        self.notice.setText(text)

    def _open_output(self) -> None:
        if not self.last_results:
            return
        target = (
            self.last_results[0].output_dir
            if len(self.last_results) == 1
            else self.last_results[0].output_dir.parent
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _copy_paths(self) -> None:
        if not self.last_results:
            return
        text = "\n".join(str(item.output_dir) for item in self.last_results)
        QApplication.clipboard().setText(text)
        self._notify(f"已复制 {len(self.last_results)} 个生成目录路径。")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self._worker.wait(5000)
        super().closeEvent(event)
