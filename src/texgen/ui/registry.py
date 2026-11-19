from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .fiber_page import FiberRvePage
from .texgen_page import TexGenPage
from .widgets import card_layout, label


@dataclass(frozen=True)
class ToolModule:
    module_id: str
    title: str
    subtitle: str
    category: str
    implemented: bool
    factory: Callable[[], QWidget]
    badge: str = ""


class ToolRegistry:
    """Registry for sidebar workflow modules in multiscale execution order."""

    def __init__(self) -> None:
        self._modules: list[ToolModule] = []
        self._by_id: dict[str, ToolModule] = {}

    def register(self, module: ToolModule) -> None:
        if module.module_id in self._by_id:
            raise ValueError(f"重复注册模块 ID：{module.module_id}")
        self._modules.append(module)
        self._by_id[module.module_id] = module

    def modules(self) -> tuple[ToolModule, ...]:
        return tuple(self._modules)

    def get(self, module_id: str) -> ToolModule:
        return self._by_id[module_id]


class PlaceholderPage(QWidget):
    def __init__(
        self, module_id: str, title: str, subtitle: str, badge: str, description: str
    ) -> None:
        super().__init__(objectName="PlaceholderPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(objectName="PageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 16, 24, 16)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(label(title, "PageTitle"))
        titles.addWidget(label(subtitle, "PageSubtitle"))
        header_layout.addLayout(titles)
        header_layout.addStretch()
        header_layout.addWidget(label(badge or "待接入", "PlaceholderBadge"))
        root.addWidget(header)

        body = QWidget(objectName="Content")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 18, 22, 18)
        body_layout.setSpacing(14)

        card = QFrame()
        card_box = card_layout(card)
        card_box.addWidget(label("流程接口说明", "Section"))
        card_box.addWidget(label(description, "FieldCaption", wrap=True))
        card_box.addWidget(
            label(
                f"当前状态：已按多尺度流程顺序预留入口（模块 ID：{module_id}），"
                "可在 src/texgen/ui/registry.py 中替换为正式页面实现。",
                "PlaceholderStatus",
                wrap=True,
            )
        )
        body_layout.addWidget(card)
        body_layout.addStretch(1)
        root.addWidget(body, 1)


def _placeholder_module(
    module_id: str, title: str, subtitle: str, description: str
) -> ToolModule:
    badge = "待接入"
    return ToolModule(
        module_id=module_id,
        title=title,
        subtitle=subtitle,
        category="",
        implemented=False,
        badge=badge,
        factory=lambda: PlaceholderPage(module_id, title, subtitle, badge, description),
    )


def create_default_registry(default_output: Path | None = None) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolModule(
            module_id="fiber_rve",
            title="1. Fiber 微观建模",
            subtitle="按单丝直径分组批量替换树脂性能，生成微观纤维束 RVE 求解文件",
            category="",
            implemented=True,
            factory=lambda: FiberRvePage(default_output),
        )
    )

    registry.register(
        ToolModule(
            module_id="texgen_modeling",
            title="2. TexGen 细观建模",
            subtitle="平纹织物参数化与多厚度单胞体素网格脚本生成",
            category="",
            implemented=True,
            factory=lambda: TexGenPage(default_output),
        )
    )

    placeholders = (
        (
            "fiber_csv_export",
            "3. 纤维束 CSV 生成",
            "读取 Fiber RVE 计算结果 Mechanical_constants.txt，生成纤维束等效性能 CSV",
            "本步骤承接第 1 步（Fiber 微观建模）Abaqus 求解完成的 Mechanical_constants.txt，"
            "与第 1 步导出的树脂输入顺序一一对应，汇总整理为后续细观 Abaqus 计算所需的纤维束等效性能 CSV。",
        ),
        (
            "abaqus_batch",
            "4. Abaqus 批量求解",
            "结合 TexGen INP 几何、同种树脂性能与纤维束等效 CSV 批量提交求解",
            "本步骤读取第 2 步（TexGen 细观建模）导出的各厚度 INP / ORI / ELD 几何模型，"
            "结合同一种树脂性能与第 3 步生成的纤维束等效性能 CSV 替换材料卡片后，自动批量执行 Abaqus 均质化求解。",
        ),
        (
            "rpt_post",
            "5. RPT 后处理",
            "提取介电层均质化等效力学与 CTE 结果并汇总导出 CSV",
            "本步骤用于批量解析第 4 步输出的各厚度、各树脂工况 RPT 均质化结果，"
            "导出可用于宏观翘曲仿真子程序或 AI 数据集的结构化汇总表。",
        ),
    )

    for module_id, title, subtitle, description in placeholders:
        registry.register(_placeholder_module(module_id, title, subtitle, description))

    return registry
