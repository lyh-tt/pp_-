from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QWidget

from .pages.abaqus_page import AbaqusBatchPage
from .pages.bundle_page import FiberBundlePage
from .pages.fiber_page import FiberRvePage
from .pages.rpt_page import RptPostPage
from .pages.texgen_page import TexGenPage


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


def create_default_registry(default_output: Path | None = None) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolModule(
            module_id="fiber_rve",
            title="1. Fiber 微观建模",
            subtitle="按单丝直径分组批量替换树脂性能，7 方向并行求解微观纤维束 RVE",
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

    registry.register(
        ToolModule(
            module_id="fiber_csv_export",
            title="3. 纤维束 CSV 生成",
            subtitle="读取 Mechanical_constants.txt 合并树脂输入，输出单份纤维束等效性能 CSV",
            category="",
            implemented=True,
            factory=lambda: FiberBundlePage(default_output),
        )
    )

    registry.register(
        ToolModule(
            module_id="abaqus_batch",
            title="4. Abaqus 批量求解",
            subtitle="替换 TexGen INP 树脂与纤维束参数，临时目录并发求解并只保留 RPT",
            category="",
            implemented=True,
            factory=lambda: AbaqusBatchPage(default_output),
        )
    )

    registry.register(
        ToolModule(
            module_id="rpt_post",
            title="5. RPT 后处理",
            subtitle="解析介电层均质化 .rpt 结果并导出 MPa / ppm/K 汇总 CSV",
            category="",
            implemented=True,
            factory=lambda: RptPostPage(default_output),
        )
    )

    return registry
