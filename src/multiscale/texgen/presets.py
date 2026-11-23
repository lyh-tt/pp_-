from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from ..common.utils import format_number
from .geometry import PlainWeave


@dataclass(frozen=True)
class FabricPreset:
    """常用玻纤布平纹几何预设（内置或用户本地自定义）。"""

    fabric: PlainWeave
    recommended_fiber_um: str = ""
    note: str = ""
    builtin: bool = False

    @property
    def display_label(self) -> str:
        thick = format_number(self.fabric.fabric_thickness_um)
        if self.builtin:
            if self.recommended_fiber_um:
                return f"{self.fabric.name}（内置 · {self.recommended_fiber_um}μm · 布厚 {thick}μm）"
            return f"{self.fabric.name}（内置 · 布厚 {thick}μm）"
        return f"{self.fabric.name}（自定义 · 布厚 {thick}μm）"


# 基于项目 IPC-4412 文档与等厚椭圆截面公式（ρ=2540 kg/m³，Vf=0.7734）核验通过的 5 款常见规格
BUILTIN_FABRIC_PRESETS: tuple[FabricPreset, ...] = (
    FabricPreset(
        fabric=PlainWeave("PP1080", 0.269, 0.4237, 0.269, 0.5405, 53.0),
        recommended_fiber_um="5",
        note="23.6×18.5 根/cm · EC 5-11",
        builtin=True,
    ),
    FabricPreset(
        fabric=PlainWeave("PP2116", 0.3034, 0.4237, 0.3034, 0.4386, 94.0),
        recommended_fiber_um="7",
        note="23.6×22.8 根/cm · EC 7-22",
        builtin=True,
    ),
    FabricPreset(
        fabric=PlainWeave("PP3313", 0.2546, 0.4237, 0.2546, 0.4098, 84.0),
        recommended_fiber_um="6",
        note="23.6×24.4 根/cm · EC 6-16.5",
        builtin=True,
    ),
    FabricPreset(
        fabric=PlainWeave("PP1035", 0.2546, 0.3846, 0.2546, 0.3731, 28.0),
        recommended_fiber_um="5",
        note="26.0×26.8 根/cm · EC 5-5.5",
        builtin=True,
    ),
    FabricPreset(
        fabric=PlainWeave("PP1027", 0.2258, 0.339, 0.2258, 0.339, 19.0),
        recommended_fiber_um="4",
        note="29.5×29.5 根/cm · EC 4-3.31",
        builtin=True,
    ),
)


def default_user_presets_path() -> Path:
    return Path.home() / ".pp_multiscale_toolbox" / "fabric_presets.json"


def _preset_to_dict(preset: FabricPreset) -> dict[str, object]:
    f = preset.fabric
    return {
        "name": f.name,
        "warp_width_mm": f.warp_width_mm,
        "warp_spacing_mm": f.warp_spacing_mm,
        "weft_width_mm": f.weft_width_mm,
        "weft_spacing_mm": f.weft_spacing_mm,
        "fabric_thickness_um": f.fabric_thickness_um,
        "recommended_fiber_um": preset.recommended_fiber_um,
        "note": preset.note,
    }


def load_user_presets(path: Path | None = None) -> list[FabricPreset]:
    """读取本地自定义织物预设 JSON；文件损坏或单条非法时容错跳过。"""
    target = Path(path) if path is not None else default_user_presets_path()
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    items: list[FabricPreset] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            fabric = PlainWeave(
                name=str(entry["name"]),
                warp_width_mm=float(entry["warp_width_mm"]),
                warp_spacing_mm=float(entry["warp_spacing_mm"]),
                weft_width_mm=float(entry["weft_width_mm"]),
                weft_spacing_mm=float(entry["weft_spacing_mm"]),
                fabric_thickness_um=float(entry["fabric_thickness_um"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        items.append(
            FabricPreset(
                fabric=fabric,
                recommended_fiber_um=str(entry.get("recommended_fiber_um", "") or ""),
                note=str(entry.get("note", "") or ""),
                builtin=False,
            )
        )
    return items


def _write_user_presets(presets: list[FabricPreset], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [_preset_to_dict(item) for item in presets],
        ensure_ascii=False,
        indent=2,
    )
    with TemporaryDirectory(dir=target.parent, prefix=".pp-preset-") as stage:
        staged = Path(stage) / target.name
        staged.write_text(payload, encoding="utf-8")
        staged.replace(target)


def list_all_presets(path: Path | None = None) -> list[FabricPreset]:
    """返回完整预设列表：内置预设优先，同名时被用户自定义预设覆盖。"""
    user_presets = load_user_presets(path)
    user_by_key = {item.fabric.name.casefold(): item for item in user_presets}

    merged: list[FabricPreset] = []
    seen: set[str] = set()
    for builtin in BUILTIN_FABRIC_PRESETS:
        key = builtin.fabric.name.casefold()
        seen.add(key)
        merged.append(user_by_key.get(key, builtin))

    for custom in user_presets:
        key = custom.fabric.name.casefold()
        if key not in seen:
            seen.add(key)
            merged.append(custom)
    return merged


def save_user_preset(
    fabric: PlainWeave, *, note: str = "", path: Path | None = None
) -> list[FabricPreset]:
    """保存或按名称覆盖一条本地自定义织物预设，返回刷新后的完整列表。"""
    target = Path(path) if path is not None else default_user_presets_path()
    current = load_user_presets(target)
    key = fabric.name.casefold()
    new_preset = FabricPreset(fabric=fabric, note=note.strip(), builtin=False)

    updated: list[FabricPreset] = []
    replaced = False
    for item in current:
        if item.fabric.name.casefold() == key:
            updated.append(new_preset)
            replaced = True
        else:
            updated.append(item)
    if not replaced:
        updated.append(new_preset)

    _write_user_presets(updated, target)
    return list_all_presets(target)


def delete_user_preset(name: str, *, path: Path | None = None) -> list[FabricPreset]:
    """删除指定名称的用户自定义预设（同名内置预设会自动恢复），返回完整列表。"""
    target = Path(path) if path is not None else default_user_presets_path()
    current = load_user_presets(target)
    key = name.strip().casefold()
    filtered = [item for item in current if item.fabric.name.casefold() != key]
    _write_user_presets(filtered, target)
    return list_all_presets(target)
