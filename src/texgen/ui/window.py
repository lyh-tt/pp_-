from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .registry import ToolModule, ToolRegistry, create_default_registry
from .widgets import label


class ToolboxWindow(QWidget):
    def __init__(
        self,
        default_output: Path | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        super().__init__(objectName="ToolboxWindow")
        self.setWindowTitle("材料多尺度建模处理工具箱")
        self.setMinimumSize(1020, 620)
        screen = self.screen().availableGeometry()
        self.resize(min(1320, screen.width() - 36), min(800, screen.height() - 42))

        self.registry = registry or create_default_registry(default_output)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        self._pages: dict[str, QWidget] = {}
        self._page_indices: dict[str, int] = {}
        self._current_module_id = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget(objectName="PageStack")
        sidebar = self._build_sidebar()
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)

        modules = self.registry.modules()
        if modules:
            default_module = next((m for m in modules if m.implemented), modules[0])
            self.select_module(default_module.module_id)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame(objectName="Sidebar")
        sidebar.setFixedWidth(252)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QFrame(objectName="SidebarBrand")
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(18, 20, 18, 16)
        brand_layout.setSpacing(5)
        brand_layout.addWidget(label("材料多尺度建模\n处理工具箱", "SidebarTitle", wrap=True))
        brand_layout.addWidget(label("介电层跨尺度建模平台", "SidebarSubtitle", wrap=True))
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
            label("按微观 → 细观 → 求解顺序排列，支持按模块接口逐步接入。", "SidebarFootNote", wrap=True)
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

    def page(self, module_id: str) -> QWidget:
        return self._pages[module_id]

    def registered_modules(self) -> tuple[ToolModule, ...]:
        return self.registry.modules()
