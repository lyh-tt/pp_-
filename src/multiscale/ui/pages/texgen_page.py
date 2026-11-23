from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...texgen.export import export_batch
from ...texgen.workflow import VOXELS, prepare_batch
from ..widgets import label
from .texgen_editor import GeometryEditor, ThicknessCaseEditor


class TexGenPage(QWidget):
    def __init__(self, default_output: Path | None = None) -> None:
        super().__init__(objectName="TexGenPage")
        self.settings = QSettings()
        self.script: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(self._workspace(), 1)
        root.addWidget(self._footer(default_output))

        self.geometry_editor.changed.connect(self._refresh_state)
        self.cases.changed.connect(self._refresh_state)
        self._refresh_state()

    def _header(self) -> QFrame:
        frame = QFrame(objectName="PageHeader")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(24, 16, 24, 16)

        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(label("2. TexGen 细观建模", "PageTitle"))
        titles.addWidget(
            label(
                "同一树脂体系下按不同总厚度/剩余树脂厚度批量生成 2 × 2 平纹单胞脚本",
                "PageSubtitle",
            )
        )
        layout.addLayout(titles)
        layout.addStretch()

        mesh_size = " × ".join(str(n) for n in VOXELS)
        layout.addWidget(label(f"2 × 2 平纹   /   {mesh_size} 体素", "MeshInfo"))
        return frame

    def _workspace(self) -> QScrollArea:
        content = QWidget(objectName="Content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        self.geometry_editor = GeometryEditor()
        self.cases = ThicknessCaseEditor()
        layout.addWidget(self.geometry_editor)
        layout.addWidget(self.cases, 1)

        scroll = QScrollArea(objectName="WorkspaceScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _footer(self, default_output: Path | None) -> QFrame:
        frame = QFrame(objectName="Footer")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(7)

        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        fallback = Path(documents) if documents and Path(documents).is_dir() else Path.home()
        if default_output is not None:
            initial = default_output
        else:
            saved = self.settings.value("output_dir", str(fallback), type=str)
            initial = Path(saved) if saved and Path(saved).is_dir() else fallback

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(label("输出目录", "FieldCaption"))
        self.output_path = QLineEdit(str(initial), readOnly=True)
        self.output_path.setAccessibleName("输出目录")
        actions.addWidget(self.output_path, 1)

        choose_output = QPushButton("更改…")
        choose_output.clicked.connect(self._choose_output)
        actions.addWidget(choose_output)
        layout.addLayout(actions)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(8)
        batch_row.addWidget(label("批次目录", "FieldCaption"))
        self.batch_prefix = label("<PP型号>_", "PrefixBadge")
        batch_row.addWidget(self.batch_prefix)
        self.batch_suffix = QLineEdit(
            objectName="batch_suffix",
            placeholderText="可选自定义目录后缀（如 batch1），留空按时间自动命名",
        )
        self.batch_suffix.setAccessibleName("批次目录后缀")
        batch_row.addWidget(self.batch_suffix, 1)

        generate = QPushButton("生成 TexGen 脚本", objectName="Primary")
        generate.clicked.connect(self._generate)
        batch_row.addWidget(generate)
        layout.addLayout(batch_row)

        status_row = QHBoxLayout()
        self.notice = label("填写织物几何与厚度方案后即可生成脚本。", "Notice", wrap=True)
        status_row.addWidget(self.notice, 1)

        self.result = QWidget()
        buttons = QHBoxLayout(self.result)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        for title, handler in (
            ("打开批次目录", self._open_output),
            ("复制脚本路径", self._copy_script),
            ("打开 TexGen", self._open_texgen),
        ):
            action = QPushButton(title)
            action.clicked.connect(handler)
            buttons.addWidget(action)
        self.result.hide()
        status_row.addWidget(self.result)
        layout.addLayout(status_row)
        return frame

    def _refresh_state(self) -> None:
        raw_name = self.geometry_editor.fields["name"].text().strip()
        self.batch_prefix.setText(f"{raw_name}_" if raw_name else "<PP型号>_")
        try:
            fabric = self.geometry_editor.value()
        except ValueError as error:
            fabric = None
            self.geometry_editor.show_measure(None, str(error))
        else:
            self.geometry_editor.show_measure(fabric)
        self.cases.set_fabric(fabric)

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_path.text())
        if path:
            output = str(Path(path))
            self.output_path.setText(output)
            self.settings.setValue("output_dir", output)

    def _generate(self) -> None:
        try:
            fabric = self.geometry_editor.value()
            batch = prepare_batch(
                self.cases.inputs(),
                fabric,
                batch_suffix=self.batch_suffix.text(),
            )
            self.script = export_batch(batch, Path(self.output_path.text()))
        except (OSError, ValueError) as error:
            self._notify(str(error), error=True)
            return

        self.result.show()
        self._notify(
            f"已生成 {self.script.parent.name}（共 {len(batch.cases)} 个厚度模型）。"
            "请在 TexGen 中用 Python → Run Script 执行。"
        )

    def _notify(self, text: str, *, error: bool = False) -> None:
        self.notice.setStyleSheet("color: #a43d30;" if error else "")
        self.notice.setText(text)

    def _open_output(self) -> None:
        if self.script is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.script.parent)))

    def _copy_script(self) -> None:
        if self.script is not None:
            QApplication.clipboard().setText(str(self.script))
            self._notify(f"已复制 {self.script.parent.name} 的脚本路径。")

    def _open_texgen(self) -> None:
        installed = Path("D:/TexGen/TexGenGUI.exe")
        saved = self.settings.value("texgen_exe", str(installed), type=str)
        exe = Path(saved)
        if not exe.is_file():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 TexGenGUI.exe", "", "TexGen (TexGenGUI.exe)"
            )
            if not path:
                return
            exe = Path(path)
        started, _ = QProcess.startDetached(str(exe), [], str(exe.parent))
        if started:
            self.settings.setValue("texgen_exe", str(exe))
        else:
            self._notify(f"无法启动 TexGen。请检查 {exe}。", error=True)
