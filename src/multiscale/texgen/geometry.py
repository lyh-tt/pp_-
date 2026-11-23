import re
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class PlainWeave:
    name: str
    warp_width_mm: float
    warp_spacing_mm: float
    weft_width_mm: float
    weft_spacing_mm: float
    fabric_thickness_um: float

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.name):
            raise ValueError("PP 型号须以英文字母或数字开头，可包含点、横线和下划线。")
        dimensions = (
            self.warp_width_mm,
            self.warp_spacing_mm,
            self.weft_width_mm,
            self.weft_spacing_mm,
            self.fabric_thickness_um,
        )
        if any(not isfinite(value) or value <= 0 for value in dimensions):
            raise ValueError("经纬纱宽度、中心间距和织物厚度都须大于 0。")
        if self.warp_width_mm > self.warp_spacing_mm:
            raise ValueError("经纱宽度须小于或等于经纱中心间距。")
        if self.weft_width_mm > self.weft_spacing_mm:
            raise ValueError("纬纱宽度须小于或等于纬纱中心间距。")

    @property
    def cell_size_mm(self) -> tuple[float, float]:
        return 2 * self.warp_spacing_mm, 2 * self.weft_spacing_mm

    @property
    def yarn_height_um(self) -> float:
        return self.fabric_thickness_um / 2


@dataclass(frozen=True)
class WeaveGeometry:
    warp_width_m: float
    warp_spacing_m: float
    weft_width_m: float
    weft_spacing_m: float
    fabric_thickness_m: float
    yarn_height_m: float


def calculate_geometry(fabric: PlainWeave) -> WeaveGeometry:
    return WeaveGeometry(
        warp_width_m=fabric.warp_width_mm * 1e-3,
        warp_spacing_m=fabric.warp_spacing_mm * 1e-3,
        weft_width_m=fabric.weft_width_mm * 1e-3,
        weft_spacing_m=fabric.weft_spacing_mm * 1e-3,
        fabric_thickness_m=fabric.fabric_thickness_um * 1e-6,
        yarn_height_m=fabric.yarn_height_um * 1e-6,
    )
