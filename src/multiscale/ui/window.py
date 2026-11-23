from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..common.abaqus_utils import non_ascii_path_warning
from .registry import ToolModule, ToolRegistry, create_default_registry
from .widgets import label, load_app_icon
from .workspace import ProjectContext


class ToolboxWindow(QWidget):
    def __init__(
        self,
        default_output: Path | None = None,
        registry: ToolRegistry | None = None,
        workspace: ProjectContext | None = None,
        *,
        enable_tray: bool = True,
    ) -> None:
        super().__init__(objectName="ToolboxWindow")
        self.setWindowTitle("材料多尺度建模处理工具箱")
        self._app_icon = load_app_icon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)

        self.setMinimumSize(1020, 620)
        screen = self.screen().availableGeometry()
        self.resize(min(1320, screen.width() - 36), min(800, screen.height() - 42))

        self.workspace = workspace or ProjectContext(default_output, parent=self)
        self.registry = registry or create_default_registry(
            default_output, workspace=self.workspace
        )
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        self._pages: dict[str, QWidget] = {}
        self._page_indices: dict[str, int] = {}
        self._current_module_id = ""
        self._tray_icon: QSystemTrayIcon | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget(objectName="PageStack")
        sidebar = self._build_sidebar()
        right_panel = QWidget(objectName="MainArea")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._build_project_bar())
        right_layout.addWidget(self.stack, 1)

        root.addWidget(sidebar)
        root.addWidget(right_panel, 1)

        self.workspace.project_dir_changed.connect(self._refresh_project_bar)
        self._refresh_project_bar(self.workspace.project_dir)

        modules = self.registry.modules()
        if modules:
            default_module = next((m for m in modules if m.implemented), modules[0])
            self.select_module(default_module.module_id)

        if enable_tray and not self._app_icon.isNull() and QSystemTrayIcon.isSystemTrayAvailable():
            self._init_tray_icon()

    def _init_tray_icon(self) -> None:
        tray = QSystemTrayIcon(self._app_icon, self)
        tray.setToolTip("材料多尺度建模处理工具箱")
        menu = QMenu(self)
        menu.addAction("显示主窗口", self._bring_to_front)
        menu.addAction("退出", self.close)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray_icon = tray

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._bring_to_front()

    def _bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _build_project_bar(self) -> QFrame:
        bar = QFrame(objectName="ProjectBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(8)

        layout.addWidget(label("当前工程目录", "FieldCaption"))
        self.project_path_edit = QLineEdit(str(self.workspace.project_dir), readOnly=True)
        self.project_path_edit.setObjectName("ProjectPath")
        self.project_path_edit.setAccessibleName("当前工程目录")
        layout.addWidget(self.project_path_edit, 1)

        self.project_path_warning = label("", "PathWarning")
        self.project_path_warning.hide()
        layout.addWidget(self.project_path_warning)

        choose_btn = QPushButton("切换目录…")
        choose_btn.clicked.connect(self._choose_project_dir)
        layout.addWidget(choose_btn)

        open_btn = QPushButton("打开目录")
        open_btn.clicked.connect(self._open_project_dir)
        layout.addWidget(open_btn)
        return bar

    def _refresh_project_bar(self, project_dir: Path) -> None:
        self.project_path_edit.setText(str(project_dir))
        warn = non_ascii_path_warning(project_dir)
        if warn:
            self.project_path_warning.setText("含中文路径（本机求解将自动隔离至英文临时目录）")
            self.project_path_warning.setToolTip(warn)
            self.project_path_warning.show()
        else:
            self.project_path_warning.hide()

    def _choose_project_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择统一工程目录", str(self.workspace.project_dir)
        )
        if selected:
            self.workspace.set_project_dir(Path(selected), persist=True)

    def _open_project_dir(self) -> None:
        target = self.workspace.project_dir
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def set_project_dir(self, path: Path) -> None:
        self.workspace.set_project_dir(path, persist=False)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame(objectName="Sidebar")
        sidebar.setFixedWidth(252)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QFrame(objectName="SidebarBrand")
        brand_outer = QHBoxLayout(brand)
        brand_outer.setContentsMargins(16, 18, 16, 16)
        brand_outer.setSpacing(12)

        if not self._app_icon.isNull():
            logo = QLabel(objectName="SidebarLogo")
            logo.setFixedSize(40, 40)
            logo.setPixmap(self._app_icon.pixmap(40, 40))
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            brand_outer.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)

        brand_layout = QVBoxLayout()
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(4)
        brand_layout.addWidget(label("材料多尺度建模\n处理工具箱", "SidebarTitle", wrap=True))
        brand_layout.addWidget(label("介电层跨尺度建模平台", "SidebarSubtitle", wrap=True))
        brand_outer.addLayout(brand_layout, 1)
        layout.addWidget(brand)

        nav = QWidget(objectName="SidebarNav")
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(12, 14, 12, 14)
        nav_layout.setSpacing(6)

        grouped: dict[str, list[ToolModule]] = {}
        for module in self.registry.modules():
            grouped.setdefault(module.category, []).append(module)

        for category, modules in grouped.items():
            if category:
                nav_layout.addWidget(label(category, "NavCategory"))
            for module in modules:
                caption = (
                    f"{module.title}（{module.badge}）"
                    if not module.implemented and module.badge
                    else module.title
                )
                button = QPushButton(caption, objectName="NavItem")
                button.setCheckable(True)
                button.setProperty("implemented", "true" if module.implemented else "false")
                button.setToolTip(module.subtitle)
                button.clicked.connect(
                    lambda _checked=False, mid=module.module_id: self.select_module(mid)
                )
                self._nav_group.addButton(button)
                self._nav_buttons[module.module_id] = button
                nav_layout.addWidget(button)

                widget = module.factory()
                index = self.stack.addWidget(widget)
                self._pages[module.module_id] = widget
                self._page_indices[module.module_id] = index

            if len(grouped) > 1:
                nav_layout.addSpacing(10)

        nav_layout.addStretch(1)
        layout.addWidget(nav, 1)

        footer = QFrame(objectName="SidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 12, 16, 14)
        footer_layout.addWidget(
            label(
                "统一工程目录串联微观 → 细观 → 求解汇总三步闭环，各步也支持独立使用。",
                "SidebarFootNote",
                wrap=True,
            )
        )
        layout.addWidget(footer)
        return sidebar

    @property
    def current_module_id(self) -> str:
        return self._current_module_id

    def select_module(self, module_id: str) -> None:
        if module_id not in self._page_indices:
            raise KeyError(f"未知模块 ID：{module_id}")
        self._current_module_id = module_id
        self._nav_buttons[module_id].setChecked(True)
        self.stack.setCurrentIndex(self._page_indices[module_id])
        page = self._pages[module_id]
        on_activated = getattr(page, "on_activated", None)
        if callable(on_activated):
            on_activated()

    def page(self, module_id: str) -> QWidget:
        return self._pages[module_id]

    def registered_modules(self) -> tuple[ToolModule, ...]:
        return self.registry.modules()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._tray_icon is not None:
            self._tray_icon.hide()
            self._tray_icon.deleteLater()
            self._tray_icon = None
        for page in self._pages.values():
            page.close()
        super().closeEvent(event)
