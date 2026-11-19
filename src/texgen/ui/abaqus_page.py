import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QSignalBlocker, QThread, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..abaqus_batch import (
    BatchSummary,
    SolveConfig,
    TaskResult,
    TexGenModel,
    detect_abaqus_command,
    discover_texgen_models,
    export_standalone_runner,
    plan_abaqus_tasks,
    run_abaqus_batch,
)
from ..fiber_bundle import FiberBundleRow, read_fiber_bundle_csv
from .widgets import card_layout, initial_output_dir, label, page_header, scroll_workspace


class _SolveWorker(QThread):
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
                cancel_event=self._cancel_event,
            )
            self.finished_summary.emit(summary)
        except Exception as error:
            self.failed_error.emit(str(error))


class AbaqusBatchPage(QWidget):
    def __init__(self, default_output: Path | None = None) -> None:
        super().__init__(objectName="AbaqusBatchPage")
        self.settings = QSettings()
        self._models: list[TexGenModel] = []
        self._model_boxes: list[tuple[TexGenModel, QCheckBox]] = []
        self._rows: list[FiberBundleRow] = []
        self._worker: _SolveWorker | None = None
        self._cancel_event = threading.Event()
        self.last_script: Path | None = None

        self.summary_badge = label("0 模型 × 0 材料 = 0 任务", "MeshInfo")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(
            page_header(
                "4. Abaqus 批量求解",
                "替换 TexGen INP 树脂与纤维束参数，在临时目录并发求解并仅保留 RPT 结果",
                self.summary_badge,
            )
        )
        root.addWidget(
            scroll_workspace(self._inputs_card(), self._solver_card()),
            1,
        )
        root.addWidget(self._footer(default_output))
        self._refresh_summary()

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
            readOnly=True, placeholderText="选择第 2 步生成的批次目录或单个模型目录"
        )
        self.models_dir_edit.setAccessibleName("TexGen 几何目录")
        models_row.addWidget(self.models_dir_edit, 1)
        choose_models = QPushButton("选择目录…")
        choose_models.clicked.connect(self._choose_models_dir)
        models_row.addWidget(choose_models)
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
            readOnly=True, placeholderText="选择第 3 步生成的纤维束等效性能 CSV"
        )
        self.csv_path_edit.setAccessibleName("纤维束等效性能 CSV")
        csv_row.addWidget(self.csv_path_edit, 1)
        choose_csv = QPushButton("导入 CSV…")
        choose_csv.clicked.connect(self._choose_fiber_csv)
        csv_row.addWidget(choose_csv)
        layout.addLayout(csv_row)

        self.input_hint = label(
            "每个选中的几何模型与 CSV 中的每组材料组合为一个求解任务，结果输出为 <输出目录>/<模型名>/pp*.rpt。",
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
        self.cpus_spin.setValue(2)
        opts_row.addWidget(self.cpus_spin)

        opts_row.addWidget(label("并行任务数", "FieldCaption"))
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 32)
        self.parallel_spin.setValue(2)
        opts_row.addWidget(self.parallel_spin)

        opts_row.addWidget(label("失败重试", "FieldCaption"))
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 5)
        self.retries_spin.setValue(1)
        opts_row.addWidget(self.retries_spin)

        self.resume_check = QCheckBox("跳过已有 RPT（断点续算）")
        self.resume_check.setChecked(True)
        opts_row.addWidget(self.resume_check)
        opts_row.addStretch()
        layout.addLayout(opts_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("等待运行")
        layout.addWidget(self.progress)

        self.log_view = QPlainTextEdit(objectName="RunLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setMinimumHeight(140)
        self.log_view.setPlaceholderText("求解日志将在此实时显示；中间 ODB/INP/临时文件在提取 RPT 后自动清理。")
        layout.addWidget(self.log_view, 1)
        return card

    def _footer(self, default_output: Path | None) -> QFrame:
        frame = QFrame(objectName="Footer")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(7)

        initial_dir = initial_output_dir(self.settings, "abaqus_output_dir", default_output)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(label("RPT 输出目录", "FieldCaption"))
        self.output_dir_edit = QLineEdit(str(initial_dir), readOnly=True)
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

    def _set_all_models(self, checked: bool) -> None:
        for _model, box in self._model_boxes:
            with QSignalBlocker(box):
                box.setChecked(checked)
        self._refresh_summary()

    def _choose_models_dir(self) -> None:
        initial = self.settings.value("abaqus_models_dir", str(Path.home()), type=str)
        selected = QFileDialog.getExistingDirectory(self, "选择 TexGen 几何目录", initial)
        if selected:
            self.load_models_dir(Path(selected))

    def load_models_dir(self, path: Path) -> None:
        resolved = Path(path).resolve()
        self.models_dir_edit.setText(str(resolved))
        self.settings.setValue("abaqus_models_dir", str(resolved))

        while self.models_grid.count():
            item = self.models_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._model_boxes.clear()

        self._models = discover_texgen_models(resolved)
        if not self._models:
            self.models_host.hide()
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
        initial = self.settings.value("abaqus_fiber_csv", self.output_dir_edit.text(), type=str)
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
        self.settings.setValue("abaqus_fiber_csv", str(resolved))
        self._notify(f"已读取 {len(rows)} 组纤维束材料参数。")
        self._refresh_summary()

    def _choose_abaqus(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Abaqus 命令文件",
            self.abaqus_edit.text() or "D:/",
            "Batch / Executable (*.bat *.cmd *.exe);;All Files (*)",
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
            self.settings.setValue("abaqus_output_dir", output)

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
        self.log_view.clear()
        self.progress.setRange(0, len(tasks))
        self.progress.setValue(0)
        self.progress.setFormat(f"%v / {len(tasks)}")
        self._set_running(True)
        self._notify(f"开始本机批量求解，共 {len(tasks)} 个任务…")

        self._worker = _SolveWorker(tasks, config, self._cancel_event)
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.log_emitted.connect(self.log_view.appendPlainText)
        self._worker.finished_summary.connect(self._on_finished)
        self._worker.failed_error.connect(self._on_worker_error)
        self._worker.start()

    def _stop_batch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self.stop_btn.setEnabled(False)
            self._notify("正在终止当前 Abaqus 子进程并清理临时目录…")

    def _on_progress(self, done: int, total: int, _result: TaskResult) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _on_finished(self, summary: BatchSummary) -> None:
        self._set_running(False)
        text = (
            f"求解结束：成功 {summary.succeeded}，跳过 {summary.skipped}，"
            f"失败 {summary.failed}，取消 {summary.cancelled}（共 {summary.total}）。"
        )
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

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self._worker.wait(5000)
        super().closeEvent(event)
