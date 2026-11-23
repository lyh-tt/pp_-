import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..common.resources import package_resource


def load_app_icon() -> QIcon:
    """加载应用抽象几何图标：Windows 优先多分辨率 ICO 并补充 PNG，其他平台加载 PNG。"""
    icon = QIcon()
    if sys.platform == "win32":
        ico_path = package_resource("ui/assets/app_icon.ico")
        if ico_path.is_file():
            icon.addFile(str(ico_path))
    png_path = package_resource("ui/assets/app_icon.png")
    if png_path.is_file():
        icon.addFile(str(png_path))
    return icon


def label(text: str, role: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(text, objectName=role)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    return widget


def card_layout(frame: QFrame) -> QVBoxLayout:
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(9)
    return layout


def page_header(
    title: str, subtitle: str, right_widget: QWidget | None = None
) -> QFrame:
    frame = QFrame(objectName="PageHeader")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(24, 16, 24, 16)

    titles = QVBoxLayout()
    titles.setSpacing(3)
    titles.addWidget(label(title, "PageTitle"))
    titles.addWidget(label(subtitle, "PageSubtitle"))
    layout.addLayout(titles)
    layout.addStretch()

    if right_widget is not None:
        layout.addWidget(right_widget)
    return frame


def scroll_workspace(*widgets: QWidget, stretch_last: bool = True) -> QScrollArea:
    content = QWidget(objectName="Content")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(22, 18, 22, 18)
    layout.setSpacing(14)

    last_idx = len(widgets) - 1
    for idx, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if stretch_last and idx == last_idx else 0)
    if not stretch_last:
        layout.addStretch(1)

    scroll = QScrollArea(objectName="WorkspaceScroll")
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    return scroll


def initial_output_dir(
    settings: QSettings, key: str, default_output: Path | None
) -> Path:
    if default_output is not None:
        return default_output
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    fallback = Path(documents) if documents and Path(documents).is_dir() else Path.home()
    saved = settings.value(key, "", type=str)
    return Path(saved) if saved and Path(saved).is_dir() else fallback
