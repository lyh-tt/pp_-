from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QWidget

from .pages.fiber_page import FiberRvePage
from .pages.meso_page import MesoSolvePage
from .pages.texgen_page import TexGenPage
from .workspace import ProjectContext


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


def create_default_registry(
    default_output: Path | None = None,
    workspace: ProjectContext | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    ctx = workspace or ProjectContext(default_output)

    registry.register(
        ToolModule(
            module_id="fiber_rve",
            title="1. Fiber 微观求解",
            subtitle="7 方向并行求解微观 RVE，自动产出机械常数与纤维束等效性能 CSV",
            category="",
            implemented=True,
            factory=lambda: FiberRvePage(default_output, workspace=ctx),
        )
    )

    registry.register(
        ToolModule(
            module_id="texgen_modeling",
            title="2. TexGen 细观建模",
            subtitle="支持常用玻纤布预设，按厚度方案批量生成 2 × 2 平纹单胞脚本",
            category="",
            implemented=True,
            factory=lambda: TexGenPage(default_output, workspace=ctx),
        )
    )

    registry.register(
        ToolModule(
            module_id="meso_solve",
            title="3. 细观求解与汇总",
            subtitle="自动联动纤维束 CSV 与几何目录，批量求解 Abaqus 并汇总等效性能 CSV",
            category="",
            implemented=True,
            factory=lambda: MesoSolvePage(default_output, workspace=ctx),
        )
    )

    return registry
