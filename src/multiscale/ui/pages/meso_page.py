from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...abaqus.batch import (
    AbaqusTask,
    BatchSummary,
    SolveConfig,
    TaskResult,
    TexGenModel,
    detect_abaqus_command,
    discover_texgen_models,
    export_standalone_runner,
    format_duration,
    plan_abaqus_tasks,
    run_abaqus_batch,
)
from ...common.utils import format_number
from ...common.workspace import (
    find_latest_fiber_bundle_csv,
    find_latest_texgen_batch_dir,
)
from ...fiber.bundle import FiberBundleRow, read_fiber_bundle_csv
from ...post.rpt import (
    RptSummaryRow,
    discover_rpt_files,
    export_rpt_summary_csv,
    summarize_rpt_dir,
)
from ..widgets import card_layout, label, page_header, scroll_workspace
from ..workspace import ProjectContext

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


class _SolveWorker(QThread):
    task_started = Signal(object)
    progress_changed = Signal(int, int, object)
    log_emitted = Signal(str)
    finished_summary = Signal(object)
    failed_error = Signal(str)

    def __init__(
        self,
        tasks: list,
        config: SolveConfig,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._tasks = tasks
        self._config = config
        self._cancel_event = cancel_event

    def run(self) -> None:
        try:
            summary = run_abaqus_batch(
                self._tasks,
                self._config,
                on_progress=lambda done, total, res: self.progress_changed.emit(done, total, res),
                on_log=self.log_emitted.emit,
                on_task_start=self.task_started.emit,
                cancel_event=self._cancel_event,
            )
            self.finished_summary.emit(summary)
        except Exception as error:
            self.failed_error.emit(str(error))


class MesoSolvePage(QWidget):
    """第 3 步合并页：Abaqus 细观批量求解 + RPT 结果汇总表。"""

    def __init__(
        self,
        default_output: Path | None = None,
        workspace: ProjectContext | None = None,
    ) -> None:
        super().__init__(objectName="MesoSolvePage")
        self.settings = QSettings()
        self.workspace = workspace or ProjectContext(default_output, parent=self)

        # Tab 0（批量求解）状态
        self._models: list[TexGenModel] = []
        self._model_boxes: list[tuple[TexGenModel, QCheckBox]] = []
        self._rows: list[FiberBundleRow] = []
        self._worker: _SolveWorker | None = None
        self._cancel_event = threading.Event()
        self._active_parallel = 1
        self._run_total = 0
        self._run_counts = {"succeeded": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        self._run_durations: list[float] = []
        self.last_script: Path | None = None

        # Tab 1（RPT 汇总）状态
        self._rpt_rows: list[RptSummaryRow] = []
        self._rpt_fiber_csv: Path | None = None
        self.last_summary_csv: Path | None = None

        self.summary_badge = label("0 模型 × 0 材料 = 0 任务", "MeshInfo")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(
            page_header(
                "3. 细观求解与汇总",
                "自动联动第 1 步纤维束 CSV 与第 2 步几何目录，并发求解 Abaqus 并汇总输出等效性能 CSV",
                self.summary_badge,
            )
        )

        self.tabs = QTabWidget(objectName="StepTabs")
        self.tabs.addTab(self._build_solve_tab(), "Abaqus 批量求解")
        self.tabs.addTab(self._build_rpt_tab(), "RPT 汇总表")
        root.addWidget(self.tabs, 1)

        # 接入全局工程上下文与上游产出联动信号
        self.workspace.project_dir_changed.connect(self._on_project_dir_changed)
        self.workspace.fiber_csv_ready.connect(self._on_upstream_fiber_csv)
        self.workspace.texgen_dir_ready.connect(self._on_upstream_texgen_dir)

        self._apply_project_layout()
        self._refresh_summary()

    # ------------------------------------------------------------------
    # Tab 0：Abaqus 批量求解
    # ------------------------------------------------------------------
    def _build_solve_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll_workspace(self._inputs_card(), self._solver_card()), 1)
        layout.addWidget(self._solve_footer())
        return tab

    def _inputs_card(self) -> QFrame:
        card = QFrame()
        layout = card_layout(card)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label("计算输入", "Section"))
        header.addStretch()
        select_all = QPushButton("全选模型")
        select_all.clicked.connect(lambda: self._set_all_models(True))
        header.addWidget(select_all)
        clear_all = QPushButton("清空选择")
        clear_all.clicked.connect(lambda: self._set_all_models(False))
        header.addWidget(clear_all)
        layout.addLayout(header)

        models_row = QHBoxLayout()
        models_row.setSpacing(8)
        models_row.addWidget(label("TexGen 几何目录", "FieldCaption"))
        self.models_dir_edit = QLineEdit(
            readOnly=True, placeholderText="自动联动第 2 步生成目录，或手动选择模型目录"
        )
        self.models_dir_edit.setAccessibleName("TexGen 几何目录")
        models_row.addWidget(self.models_dir_edit, 1)
        choose_models = QPushButton("选择目录…")
        choose_models.clicked.connect(self._choose_models_dir)
        models_row.addWidget(choose_models)
        refresh_models = QPushButton("刷新")
        refresh_models.clicked.connect(self._reload_models_dir)
        models_row.addWidget(refresh_models)
        layout.addLayout(models_row)

        self.models_host = QWidget()
        self.models_grid = QGridLayout(self.models_host)
        self.models_grid.setContentsMargins(0, 2, 0, 2)
        self.models_grid.setHorizontalSpacing(12)
        self.models_grid.setVerticalSpacing(6)
        self.models_host.hide()
        layout.addWidget(self.models_host)

        csv_row = QHBoxLayout()
        csv_row.setSpacing(8)
        csv_row.addWidget(label("纤维束 CSV", "FieldCaption"))
        self.csv_path_edit = QLineEdit(
            readOnly=True, placeholderText="自动联动第 1 步生成的纤维束等效性能 CSV，或手动导入"
        )
        self.csv_path_edit.setAccessibleName("纤维束等效性能 CSV")
        csv_row.addWidget(self.csv_path_edit, 1)
        choose_csv = QPushButton("导入 CSV…")
        choose_csv.clicked.connect(self._choose_fiber_csv)
        csv_row.addWidget(choose_csv)
        layout.addLayout(csv_row)

        self.input_hint = label(
            "每个选中的几何模型与 CSV 中的每组材料组合为一个求解任务，结果按序号顺序输出为 <RPT 输出目录>/<模型名>/001_<牌号>.rpt（无牌号时自动按 001_E<MPa>_CTE<e-5>.rpt 命名）。",
            "Hint",
            wrap=True,
        )
        layout.addWidget(self.input_hint)
        return card

    def _solver_card(self) -> QFrame:
        card = QFrame()
        layout = card_layout(card)
        layout.addWidget(label("求解配置与进度", "Section"))

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(8)
        cmd_row.addWidget(label("Abaqus 命令", "FieldCaption"))
        detected = detect_abaqus_command()
        saved_cmd = self.settings.value("abaqus_command", detected, type=str)
        self.abaqus_edit = QLineEdit(saved_cmd or detected)
        self.abaqus_edit.setPlaceholderText("例如 D:/abaqus/abaqus-2026/abq2026.bat 或 /usr/.../abq2022")
        self.abaqus_edit.setAccessibleName("Abaqus 命令")
        cmd_row.addWidget(self.abaqus_edit, 1)
        browse_cmd = QPushButton("浏览…")
        browse_cmd.clicked.connect(self._choose_abaqus)
        cmd_row.addWidget(browse_cmd)
        layout.addLayout(cmd_row)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        opts_row.addWidget(label("CPUs", "FieldCaption"))
        self.cpus_spin = QSpinBox()
        self.cpus_spin.setRange(1, 64)
        self.cpus_spin.setValue(6)
        opts_row.addWidget(self.cpus_spin)

        opts_row.addWidget(label("并行任务数", "FieldCaption"))
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 32)
        self.parallel_spin.setValue(1)
        opts_row.addWidget(self.parallel_spin)

        opts_row.addWidget(label("单任务内存(MB)", "FieldCaption"))
        self.memory_spin = QSpinBox()
        self.memory_spin.setRange(0, 131072)
        self.memory_spin.setSingleStep(1024)
        self.memory_spin.setValue(12288)
        self.memory_spin.setSpecialValueText("默认")
        opts_row.addWidget(self.memory_spin)

        opts_row.addWidget(label("失败重试", "FieldCaption"))
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 5)
        self.retries_spin.setValue(1)
        opts_row.addWidget(self.retries_spin)

        self.resume_check = QCheckBox("跳过已有 RPT（断点续算）")
        self.resume_check.setChecked(True)
        opts_row.addWidget(self.resume_check)

        self.auto_summary_check = QCheckBox("求解完成后自动汇总 CSV")
        self.auto_summary_check.setChecked(True)
        opts_row.addWidget(self.auto_summary_check)
        opts_row.addStretch()
        layout.addLayout(opts_row)

        self.live_status = label("等待开始批量求解。", "Hint", wrap=True)
        layout.addWidget(self.live_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("等待运行")
        layout.addWidget(self.progress)

        self.log_view = QPlainTextEdit(objectName="RunLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(140)
        self.log_view.setPlaceholderText("求解日志将在此实时显示；中间 ODB/INP/临时文件在提取 RPT 后自动清理。")
        layout.addWidget(self.log_view, 1)
        return card

    def _solve_footer(self) -> QFrame:
        frame = QFrame(objectName="Footer")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(7)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(label("RPT 输出目录", "FieldCaption"))
        self.output_dir_edit = QLineEdit(str(self.workspace.layout.rpt_dir), readOnly=True)
        self.output_dir_edit.setAccessibleName("RPT 输出目录")
        out_row.addWidget(self.output_dir_edit, 1)
        choose_out = QPushButton("更改…")
        choose_out.clicked.connect(self._choose_output_dir)
        out_row.addWidget(choose_out)
        layout.addLayout(out_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.notice = label("选择 TexGen 几何与纤维束 CSV 后即可本机运行或导出独立脚本。", "Notice", wrap=True)
        action_row.addWidget(self.notice, 1)

        self.open_out_btn = QPushButton("打开输出目录")
        self.open_out_btn.clicked.connect(self._open_output_dir)
        action_row.addWidget(self.open_out_btn)

        self.export_btn = QPushButton("导出独立脚本…")
        self.export_btn.clicked.connect(self._export_runner_dialog)
        action_row.addWidget(self.export_btn)

        self.stop_btn = QPushButton("停止求解")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_batch)
        action_row.addWidget(self.stop_btn)

        self.run_btn = QPushButton("开始本机批量求解", objectName="Primary")
        self.run_btn.clicked.connect(self._start_batch)
        action_row.addWidget(self.run_btn)
        layout.addLayout(action_row)
        return frame

    # ------------------------------------------------------------------
    # Tab 1：RPT 汇总表
    # ------------------------------------------------------------------
    def _build_rpt_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll_workspace(self._rpt_source_card(), self._rpt_preview_card()), 1)
        layout.addWidget(self._rpt_footer())
        return tab

    def _rpt_source_card(self) -> QFrame:
        card = QFrame()
        layout = card_layout(card)

        head = QHBoxLayout()
        head.addWidget(label("RPT 输入目录", "Section"))
        head.addStretch()
        self.rpt_badge = label("未加载 RPT", "MeshInfo")
        head.addWidget(self.rpt_badge)
        layout.addLayout(head)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(label("RPT 结果目录", "FieldCaption"))
        self.rpt_dir_edit = QLineEdit(
            str(self.workspace.layout.rpt_dir),
            readOnly=True,
            placeholderText="选择 RPT 根目录或单个模型目录（自动递归扫描 *.rpt）",
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
        self.rpt_csv_edit = QLineEdit(
            readOnly=True,
            placeholderText="自动同步纤维束 CSV，以补充匹配的树脂牌号与泊松比",
        )
        self.rpt_csv_edit.setAccessibleName("RPT 匹配纤维束 CSV")
        csv_row.addWidget(self.rpt_csv_edit, 1)

        choose_csv = QPushButton("导入 CSV…")
        choose_csv.clicked.connect(self._choose_rpt_fiber_csv)
        csv_row.addWidget(choose_csv)

        clear_csv = QPushButton("清除")
        clear_csv.clicked.connect(self._clear_rpt_fiber_csv)
        csv_row.addWidget(clear_csv)
        layout.addLayout(csv_row)

        self.rpt_status = label(
            "本页默认监控当前工程的 03_rpt 目录并自动刷新；也可选择任意已有 RPT 目录进行解析汇总。",
            "Hint",
            wrap=True,
        )
        layout.addWidget(self.rpt_status)
        return card

    def _rpt_preview_card(self) -> QFrame:
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

    def _rpt_footer(self) -> QFrame:
        frame = QFrame(objectName="Footer")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(7)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(label("汇总 CSV", "FieldCaption"))
        self.summary_csv_edit = QLineEdit(
            str(self.workspace.layout.summary_csv), readOnly=True
        )
        self.summary_csv_edit.setAccessibleName("RPT 汇总 CSV 输出路径")
        out_row.addWidget(self.summary_csv_edit, 1)

        choose_out = QPushButton("更改…")
        choose_out.clicked.connect(self._choose_summary_csv)
        out_row.addWidget(choose_out)

        export_btn = QPushButton("导出汇总 CSV", objectName="Primary")
        export_btn.clicked.connect(self.generate_summary)
        out_row.addWidget(export_btn)
        layout.addLayout(out_row)

        status_row = QHBoxLayout()
        self.rpt_notice = label("加载 RPT 目录后即可一键导出结构化汇总表。", "Notice", wrap=True)
        status_row.addWidget(self.rpt_notice, 1)

        self.rpt_result = QWidget()
        buttons = QHBoxLayout(self.rpt_result)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        for title, handler in (
            ("打开所在目录", self._open_summary_dir),
            ("复制 CSV 路径", self._copy_summary_path),
        ):
            btn = QPushButton(title)
            btn.clicked.connect(handler)
            buttons.addWidget(btn)
        self.rpt_result.hide()
        status_row.addWidget(self.rpt_result)
        layout.addLayout(status_row)
        return frame

    # ------------------------------------------------------------------
    # 工程工作区联动逻辑
    # ------------------------------------------------------------------
    def _clear_model_grid(self) -> None:
        while self.models_grid.count():
            item = self.models_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._model_boxes.clear()
        self._models = []
        self.models_host.hide()

    def _apply_project_layout(self) -> None:
        layout = self.workspace.layout
        self.output_dir_edit.setText(str(layout.rpt_dir))
        self.rpt_dir_edit.setText(str(layout.rpt_dir))
        self.summary_csv_edit.setText(str(layout.summary_csv))

        latest_csv = find_latest_fiber_bundle_csv(layout)
        if latest_csv is not None:
            self.load_fiber_csv(latest_csv)

        latest_texgen = find_latest_texgen_batch_dir(layout)
        if latest_texgen is not None:
            self.load_models_dir(latest_texgen, quiet_if_empty=True)

        if layout.rpt_dir.is_dir() and discover_rpt_files(layout.rpt_dir):
            self._refresh_rpt_preview()

    def _on_project_dir_changed(self, _project_root: Path) -> None:
        # 切换工程根目录时先清空上一工程的状态，再自动探测新工程已有的三步产物
        self._clear_model_grid()
        self.models_dir_edit.clear()
        self._rows = []
        self.csv_path_edit.clear()
        self._rpt_fiber_csv = None
        self.rpt_csv_edit.clear()
        self.preview_table.setRowCount(0)
        self._rpt_rows = []
        self.last_summary_csv = None
        self.rpt_result.hide()
        self.rpt_badge.setText("未加载 RPT")
        self.tabs.setTabText(1, "RPT 汇总表")
        self.live_status.setText("等待开始批量求解。")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("等待运行")
        self._set_rpt_input_status(
            "本页默认监控当前工程的 03_rpt 目录并自动刷新；也可选择任意已有 RPT 目录进行解析汇总。",
            error=False,
        )
        self._notify("已切换工程目录，等待关联上游模型与纤维束 CSV。")

        self._apply_project_layout()
        self._refresh_summary()

    def _on_upstream_fiber_csv(self, csv_path: Path) -> None:
        if csv_path.is_file():
            self.load_fiber_csv(csv_path)

    def _on_upstream_texgen_dir(self, batch_dir: Path) -> None:
        self.load_models_dir(batch_dir, quiet_if_empty=True)

    def on_activated(self) -> None:
        """用户切换到第 3 步时自动静默检查待扫描目录（例如刚在 TexGenGUI 跑完脚本）。"""
        models_text = self.models_dir_edit.text().strip()
        if models_text and not self._models:
            target = Path(models_text)
            if target.is_dir() and discover_texgen_models(target):
                self.load_models_dir(target, quiet_if_empty=True)
        elif not models_text:
            latest_texgen = find_latest_texgen_batch_dir(self.workspace.layout)
            if latest_texgen is not None:
                self.load_models_dir(latest_texgen, quiet_if_empty=True)

        if not self.csv_path_edit.text().strip():
            latest_csv = find_latest_fiber_bundle_csv(self.workspace.layout)
            if latest_csv is not None:
                self.load_fiber_csv(latest_csv)

        rpt_text = self.rpt_dir_edit.text().strip()
        if rpt_text and Path(rpt_text).is_dir():
            if not self._rpt_rows and discover_rpt_files(Path(rpt_text)):
                self._refresh_rpt_preview()

    # ------------------------------------------------------------------
    # Tab 0 交互与求解控制
    # ------------------------------------------------------------------
    def _set_all_models(self, checked: bool) -> None:
        for _model, box in self._model_boxes:
            with QSignalBlocker(box):
                box.setChecked(checked)
        self._refresh_summary()

    def _choose_models_dir(self) -> None:
        layout = self.workspace.layout
        initial = str(layout.texgen_dir if layout.texgen_dir.is_dir() else layout.root)
        selected = QFileDialog.getExistingDirectory(self, "选择 TexGen 几何目录", initial)
        if selected:
            self.load_models_dir(Path(selected))

    def _reload_models_dir(self) -> None:
        text = self.models_dir_edit.text().strip()
        if text:
            self.load_models_dir(Path(text))

    def load_models_dir(self, path: Path, *, quiet_if_empty: bool = False) -> None:
        resolved = Path(path).resolve()
        self.models_dir_edit.setText(str(resolved))
        self._clear_model_grid()

        self._models = discover_texgen_models(resolved)
        if not self._models:
            if quiet_if_empty:
                self._notify(
                    f"已关联第 2 步批次目录（{resolved.name}），在 TexGen 运行脚本后点击「刷新」或切换本页即可自动识别模型。"
                )
            else:
                self._notify("未在所选目录发现包含 .inp 与 .ori 的 TexGen 模型。", error=True)
            self._refresh_summary()
            return

        self.models_host.show()
        cols = 3
        for col in range(cols):
            self.models_grid.setColumnStretch(col, 1)
        for idx, model in enumerate(self._models):
            box = QCheckBox(f"{model.name}（{model.inp_path.name}）")
            box.setChecked(True)
            box.toggled.connect(self._refresh_summary)
            self._model_boxes.append((model, box))
            self.models_grid.addWidget(box, idx // cols, idx % cols)

        self._notify(f"已发现 {len(self._models)} 个 TexGen 几何模型。")
        self._refresh_summary()

    def _choose_fiber_csv(self) -> None:
        layout = self.workspace.layout
        initial = str(layout.fiber_dir if layout.fiber_dir.is_dir() else layout.root)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择纤维束等效性能 CSV", initial, "CSV (*.csv);;All Files (*.*)"
        )
        if path:
            self.load_fiber_csv(Path(path))

    def load_fiber_csv(self, path: Path) -> None:
        resolved = Path(path).resolve()
        try:
            rows = read_fiber_bundle_csv(resolved)
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return

        self._rows = rows
        self.csv_path_edit.setText(str(resolved))
        # 同步至 Tab 1 用于 RPT 牌号匹配
        self._rpt_fiber_csv = resolved
        self.rpt_csv_edit.setText(str(resolved))
        self._notify(f"已读取 {len(rows)} 组纤维束材料参数：{resolved.name}")
        self._refresh_summary()
        if self.rpt_dir_edit.text().strip() and Path(self.rpt_dir_edit.text().strip()).is_dir():
            if discover_rpt_files(Path(self.rpt_dir_edit.text().strip())):
                self._refresh_rpt_preview()

    def _choose_abaqus(self) -> None:
        default_dir = "D:/" if os.name == "nt" else "/usr"
        file_filter = (
            "Batch / Executable (*.bat *.cmd *.exe);;All Files (*)"
            if os.name == "nt"
            else "All Files (*)"
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Abaqus 命令文件",
            self.abaqus_edit.text() or default_dir,
            file_filter,
        )
        if path:
            self.abaqus_edit.setText(str(Path(path)))
            self.settings.setValue("abaqus_command", str(Path(path)))

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择 RPT 输出目录", self.output_dir_edit.text()
        )
        if selected:
            output = str(Path(selected))
            self.output_dir_edit.setText(output)
            self.rpt_dir_edit.setText(output)
            if Path(output).is_dir() and discover_rpt_files(Path(output)):
                self._refresh_rpt_preview()

    def selected_models(self) -> list[TexGenModel]:
        return [model for model, box in self._model_boxes if box.isChecked()]

    def solve_config(self) -> SolveConfig:
        cmd = self.abaqus_edit.text().strip()
        if cmd:
            self.settings.setValue("abaqus_command", cmd)
        return SolveConfig(
            abaqus_command=cmd,
            cpus=self.cpus_spin.value(),
            max_parallel=self.parallel_spin.value(),
            retries=self.retries_spin.value(),
            resume_existing=self.resume_check.isChecked(),
            memory_mb=self.memory_spin.value() or None,
        )

    def _refresh_summary(self) -> None:
        m_count = len(self.selected_models())
        r_count = len(self._rows)
        self.summary_badge.setText(f"{m_count} 模型 × {r_count} 材料 = {m_count * r_count} 任务")

    def _export_runner_dialog(self) -> None:
        default_file = str(Path(self.output_dir_edit.text()) / "run_abaqus_batch.py")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出独立批量求解脚本", default_file, "Python (*.py)"
        )
        if not path:
            return
        try:
            exported = self.export_script_to(Path(path))
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return
        self._notify(f"已导出独立求解脚本：{exported.name}（可在 Windows 或 Linux 直接运行）")

    def export_script_to(self, target_path: Path) -> Path:
        models_dir_text = self.models_dir_edit.text().strip()
        csv_text = self.csv_path_edit.text().strip()
        if not models_dir_text:
            raise ValueError("请先选择 TexGen 几何目录。")
        if not csv_text:
            raise ValueError("请先导入纤维束等效性能 CSV。")
        config = self.solve_config()
        target = export_standalone_runner(
            target_path,
            models_dir=Path(models_dir_text),
            fiber_csv=Path(csv_text),
            output_dir=Path(self.output_dir_edit.text()),
            config=config,
        )
        self.last_script = target
        return target

    def _start_batch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            models = self.selected_models()
            tasks = plan_abaqus_tasks(models, self._rows, Path(self.output_dir_edit.text()))
            config = self.solve_config()
        except ValueError as error:
            self._notify(str(error), error=True)
            return

        self._cancel_event.clear()
        self._active_parallel = config.max_parallel
        self._run_total = len(tasks)
        self._run_counts = {"succeeded": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        self._run_durations = []
        self.log_view.clear()
        self.progress.setRange(0, len(tasks))
        self.progress.setValue(0)
        self.progress.setFormat(f"已完成 0 / {len(tasks)}")
        self.live_status.setText(f"准备启动 {len(tasks)} 个求解任务（并行 {config.max_parallel}）…")
        self._set_running(True)
        self._notify(f"开始本机批量求解，共 {len(tasks)} 个任务…")

        self._worker = _SolveWorker(tasks, config, self._cancel_event)
        self._worker.task_started.connect(self._on_task_started)
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.log_emitted.connect(self.log_view.appendPlainText)
        self._worker.finished_summary.connect(self._on_finished)
        self._worker.failed_error.connect(self._on_worker_error)
        self._worker.start()

    def _stop_batch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self.stop_btn.setEnabled(False)
            self.live_status.setText("正在停止当前 Abaqus 子进程并清理临时目录…")
            self._notify("正在终止当前 Abaqus 子进程并清理临时目录…")

    def _on_task_started(self, task: AbaqusTask) -> None:
        width = max(3, len(str(task.total_resins)))
        seq = f"#{task.resin_index:0{width}d}"
        name = task.row.resin_name.strip()
        resin_label = f"{seq} {name}" if name else seq
        s = self._run_counts["succeeded"]
        k = self._run_counts["skipped"]
        f = self._run_counts["failed"]
        self.live_status.setText(
            f"正在计算 [{task.task_index}/{task.total_tasks}]：{task.model.name}"
            f"（模型 {task.model_index}/{task.total_models}）· {resin_label}"
            f"（材料 {task.resin_index}/{task.total_resins}）  |  成功 {s} · 跳过 {k} · 失败 {f}"
        )

    def _on_progress(self, _done: int, total: int, result: TaskResult) -> None:
        self._run_counts[result.status] = self._run_counts.get(result.status, 0) + 1
        if result.status == "succeeded" and result.elapsed_sec > 0:
            self._run_durations.append(result.elapsed_sec)

        # 取消任务不计入已完成进度条，防止停止时进度条误满格
        executed = (
            self._run_counts["succeeded"]
            + self._run_counts["skipped"]
            + self._run_counts["failed"]
        )
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(executed)

        if result.status != "cancelled":
            s = self._run_counts["succeeded"]
            k = self._run_counts["skipped"]
            f = self._run_counts["failed"]
            fmt = f"已完成 {executed} / {total}（成功 {s} · 跳过 {k} · 失败 {f}）"
            if self._run_durations:
                avg_sec = sum(self._run_durations) / len(self._run_durations)
                remaining = max(0, total - (executed + self._run_counts["cancelled"]))
                if remaining > 0:
                    eta_sec = (avg_sec * remaining) / max(1, self._active_parallel)
                    fmt += f" · 均耗 {format_duration(avg_sec)} · 预计剩余 ~{format_duration(eta_sec)}"
            self.progress.setFormat(fmt)

    def _on_finished(self, summary: BatchSummary) -> None:
        self._set_running(False)
        executed = summary.succeeded + summary.skipped + summary.failed
        self.progress.setRange(0, max(1, summary.total))
        self.progress.setValue(executed)

        if summary.cancelled > 0:
            self.progress.setFormat(
                f"已停止：{executed} / {summary.total}"
                f"（成功 {summary.succeeded} · 跳过 {summary.skipped} · 失败 {summary.failed} · 未执行 {summary.cancelled}）"
            )
            self.live_status.setText(
                f"已停止求解：已完成 {executed} / {summary.total}（未执行 {summary.cancelled}），"
                "已出结果已刷新至「RPT 汇总表」。"
            )
            self.log_view.appendPlainText(
                f"[已停止] 当前进度：成功 {summary.succeeded} / 跳过 {summary.skipped}"
                f" / 失败 {summary.failed} / 未执行 {summary.cancelled}"
            )
            text = (
                f"已停止：已完成 {executed} / {summary.total}"
                f"（成功 {summary.succeeded}，跳过 {summary.skipped}，失败 {summary.failed}，未执行 {summary.cancelled}）。"
            )
        else:
            self.progress.setFormat(
                f"全部完成：{executed} / {summary.total}"
                f"（成功 {summary.succeeded} · 跳过 {summary.skipped} · 失败 {summary.failed}）"
            )
            self.live_status.setText(
                f"求解完成：{executed} / {summary.total}"
                f"（成功 {summary.succeeded} · 跳过 {summary.skipped} · 失败 {summary.failed}）"
            )
            text = (
                f"求解结束：成功 {summary.succeeded}，跳过 {summary.skipped}，"
                f"失败 {summary.failed}（共 {summary.total}）。"
            )

        out_dir = Path(self.output_dir_edit.text())
        if out_dir.is_dir() and discover_rpt_files(out_dir):
            self.load_rpt_dir(out_dir)
            if self.auto_summary_check.isChecked() and self._rpt_rows:
                try:
                    saved = export_rpt_summary_csv(
                        self._rpt_rows, Path(self.summary_csv_edit.text())
                    )
                    self.last_summary_csv = saved
                    self.rpt_result.show()
                    self._notify_rpt(f"已自动汇总 {len(self._rpt_rows)} 条结果：{saved.name}")
                    text += f" 已自动汇总 {len(self._rpt_rows)} 条结果至 {saved.name}。"
                except Exception as error:
                    text += f" 自动汇总失败：{error}"
        self._notify(text, error=summary.failed > 0)

    def _on_worker_error(self, message: str) -> None:
        self._set_running(False)
        self._notify(f"运行异常：{message}", error=True)

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.export_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def _notify(self, text: str, *, error: bool = False) -> None:
        self.notice.setStyleSheet("color: #a43d30;" if error else "")
        self.notice.setText(text)

    def _open_output_dir(self) -> None:
        target = Path(self.output_dir_edit.text())
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ------------------------------------------------------------------
    # Tab 1 交互与 RPT 汇总导出
    # ------------------------------------------------------------------
    def _choose_rpt_dir(self) -> None:
        layout = self.workspace.layout
        initial = str(layout.rpt_dir if layout.rpt_dir.is_dir() else layout.root)
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
        self._refresh_rpt_preview()

    def _choose_rpt_fiber_csv(self) -> None:
        initial = self.rpt_dir_edit.text() or str(self.workspace.project_dir)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择纤维束 CSV（可选）", initial, "CSV (*.csv);;All Files (*.*)"
        )
        if path:
            resolved = Path(path).resolve()
            self._rpt_fiber_csv = resolved
            self.rpt_csv_edit.setText(str(resolved))
            self._refresh_rpt_preview()

    def _clear_rpt_fiber_csv(self) -> None:
        self._rpt_fiber_csv = None
        self.rpt_csv_edit.clear()
        self._refresh_rpt_preview()

    def _refresh_rpt_preview(self) -> None:
        self.preview_table.setRowCount(0)
        self._rpt_rows = []
        self.tabs.setTabText(1, "RPT 汇总表")

        dir_text = self.rpt_dir_edit.text().strip()
        if not dir_text:
            self.rpt_badge.setText("未加载 RPT")
            return

        rpt_dir = Path(dir_text)
        if not rpt_dir.is_dir():
            self.rpt_badge.setText("目录待创建")
            self._set_rpt_input_status(f"目录尚未创建：{rpt_dir}", error=False)
            return

        try:
            rows, warnings = summarize_rpt_dir(rpt_dir, fiber_csv=self._rpt_fiber_csv)
        except (OSError, ValueError) as error:
            self.rpt_badge.setText("加载失败")
            self._set_rpt_input_status(str(error), error=True)
            return

        if not rows:
            self.rpt_badge.setText("未找到有效 RPT")
            msg = "未在该目录下发现有效 .rpt 文件。"
            if warnings:
                msg += f" 跳过 {len(warnings)} 个异常文件：{warnings[0]}"
            self._set_rpt_input_status(msg, error=bool(warnings))
            return

        self._rpt_rows = rows
        self.rpt_badge.setText(f"已解析 {len(rows)} 条 RPT")
        self.tabs.setTabText(1, f"RPT 汇总表 ({len(rows)})")
        status_msg = f"解析成功：共 {len(rows)} 条 RPT 记录"
        if self._rpt_fiber_csv is not None:
            matched_count = sum(1 for r in rows if r.resin)
            status_msg += f"（已匹配纤维束牌号 {matched_count} 条）"
        if warnings:
            status_msg += f"；另跳过 {len(warnings)} 个格式异常文件"
        self._set_rpt_input_status(status_msg + "。", error=False)

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
                *(
                    f"{val:.6g}"
                    for val in (
                        row.e1_mpa, row.e2_mpa, row.e3_mpa,
                        row.v12, row.v13, row.v23,
                        row.g12_mpa, row.g13_mpa, row.g23_mpa,
                        row.cte_x_ppm, row.cte_y_ppm, row.cte_z_ppm,
                    )
                ),
            ]
            for col_idx, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.preview_table.setItem(row_idx, col_idx, item)

    def _choose_summary_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 RPT 汇总 CSV", self.summary_csv_edit.text(), "CSV (*.csv)"
        )
        if path:
            target = Path(path)
            if target.suffix.lower() != ".csv":
                target = target.with_suffix(".csv")
            self.summary_csv_edit.setText(str(target))

    def generate_summary(self) -> Path | None:
        try:
            if not self._rpt_rows:
                raise ValueError("请先加载包含有效 .rpt 文件的目录。")
            target = export_rpt_summary_csv(
                self._rpt_rows, Path(self.summary_csv_edit.text())
            )
        except (OSError, ValueError) as error:
            self._notify_rpt(str(error), error=True)
            return None

        self.last_summary_csv = target
        self.rpt_result.show()
        self._notify_rpt(f"已导出 RPT 汇总 CSV（共 {len(self._rpt_rows)} 条）：{target.name}")
        return target

    def _set_rpt_input_status(self, text: str, *, error: bool) -> None:
        self.rpt_status.setStyleSheet("color: #a43d30;" if error else "")
        self.rpt_status.setText(text)

    def _notify_rpt(self, text: str, *, error: bool = False) -> None:
        self.rpt_notice.setStyleSheet("color: #a43d30;" if error else "")
        self.rpt_notice.setText(text)

    def _open_summary_dir(self) -> None:
        if self.last_summary_csv is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_summary_csv.parent)))

    def _copy_summary_path(self) -> None:
        if self.last_summary_csv is not None:
            QApplication.clipboard().setText(str(self.last_summary_csv))
            self._notify_rpt(f"已复制汇总 CSV 路径：{self.last_summary_csv.name}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self._worker.wait(5000)
        super().closeEvent(event)
