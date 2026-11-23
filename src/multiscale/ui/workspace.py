from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal

from ..common.workspace import ProjectLayout


class ProjectContext(QObject):
    """全局统一工程上下文：串联第 1~3 步目录布局与上下游产物路径联动。"""

    project_dir_changed = Signal(Path)
    fiber_csv_ready = Signal(Path)
    texgen_dir_ready = Signal(Path)

    def __init__(
        self, default_output: Path | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.latest_fiber_csv: Path | None = None
        self.latest_texgen_dir: Path | None = None

        if default_output is not None:
            # 显式指定根目录时（如测试或 CLI 参数），不读写全局 QSettings，避免污染用户记忆
            self._project_dir = Path(default_output).resolve()
        else:
            documents = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
            fallback = (
                Path(documents) if documents and Path(documents).is_dir() else Path.home()
            )
            saved = QSettings().value("project_dir", "", type=str)
            candidate = Path(saved) if saved and Path(saved).is_dir() else fallback
            self._project_dir = candidate.resolve()

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def layout(self) -> ProjectLayout:
        return ProjectLayout(self._project_dir)

    def set_project_dir(self, path: Path, *, persist: bool = False) -> None:
        resolved = Path(path).resolve()
        self._project_dir = resolved
        self.latest_fiber_csv = None
        self.latest_texgen_dir = None
        if persist:
            QSettings().setValue("project_dir", str(resolved))
        self.project_dir_changed.emit(resolved)

    def notify_fiber_csv(self, csv_path: Path) -> None:
        resolved = Path(csv_path).resolve()
        self.latest_fiber_csv = resolved
        self.fiber_csv_ready.emit(resolved)

    def notify_texgen_dir(self, batch_dir: Path) -> None:
        resolved = Path(batch_dir).resolve()
        self.latest_texgen_dir = resolved
        self.texgen_dir_ready.emit(resolved)
