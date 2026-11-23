from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ...common.utils import format_number
from ...texgen.geometry import PlainWeave
from ...texgen.presets import (
    FabricPreset,
    delete_user_preset,
    list_all_presets,
    save_user_preset,
)
from ...texgen.workflow import (
    CaseInput,
    ThicknessMode,
    prepare_case,
    resolve_thicknesses,
)
from ..widgets import card_layout, label


class GeometryEditor(QFrame):
    changed = Signal()
    preset_notice = Signal(str, bool)

    def __init__(self, presets_path: Path | None = None) -> None:
        super().__init__()
        self._presets_path = presets_path
        self._presets: list[FabricPreset] = []
        layout = card_layout(self)
        heading = QHBoxLayout()
        heading.addWidget(label("织物几何", "Section"))
        heading.addStretch()
        heading.addWidget(label("支持内置/自定义常见规格一键填充，也可自由输入参数", "Hint"))
        layout.addLayout(heading)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addWidget(label("织物预设", "FieldCaption"))
        self.preset_combo = QComboBox(objectName="FabricPreset")
        self.preset_combo.setAccessibleName("织物几何预设")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self.preset_combo, 1)

        self.save_preset_btn = QPushButton("保存为预设")
        self.save_preset_btn.clicked.connect(self._save_current_preset)
        preset_row.addWidget(self.save_preset_btn)

        self.delete_preset_btn = QPushButton("删除预设")
        self.delete_preset_btn.clicked.connect(self._delete_current_preset)
        preset_row.addWidget(self.delete_preset_btn)
        layout.addLayout(preset_row)

        self.fields: dict[str, QLineEdit] = {}
        profile = QHBoxLayout()
        profile.setSpacing(10)
        profile.addWidget(label("PP 型号", "FieldCaption"))
        profile.addWidget(
            self._field("name", "PP 型号", placeholder="例如 PP1080 / 1080 / 2116"), 1
        )
        profile.addSpacing(10)
        profile.addWidget(label("织物厚度（μm）", "FieldCaption"))
        profile.addWidget(
            self._field("fabric_thickness_um", "织物厚度（μm）", placeholder="例如 53"), 1
        )
        layout.addLayout(profile)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        for column, caption in enumerate(("纱向", "纱束宽度（mm）", "中心间距（mm）")):
            grid.addWidget(label(caption, "FieldCaption"), 0, column)
        placeholders = {
            "warp_width_mm": "例如 0.269",
            "warp_spacing_mm": "例如 0.424",
            "weft_width_mm": "例如 0.269",
            "weft_spacing_mm": "例如 0.541",
        }
        for row, (name, direction, tag) in enumerate(
            (("warp", "经纱 · 沿 Y", "Warp"), ("weft", "纬纱 · 沿 X", "Weft")), start=1
        ):
            grid.addWidget(label(direction, tag), row, 0)
            yarn = direction[:2]
            width_key = f"{name}_width_mm"
            spacing_key = f"{name}_spacing_mm"
            grid.addWidget(
                self._field(width_key, f"{yarn}宽度（mm）", placeholder=placeholders[width_key]),
                row,
                1,
            )
            grid.addWidget(
                self._field(
                    spacing_key, f"{yarn}中心间距（mm）", placeholder=placeholders[spacing_key]
                ),
                row,
                2,
            )
        layout.addLayout(grid)
        self.measure = label("", "GeometryNote", wrap=True)
        layout.addWidget(self.measure)
        self._reload_presets()

    def _field(self, key: str, title: str, *, placeholder: str = "") -> QLineEdit:
        field = QLineEdit(objectName=key)
        field.setAccessibleName(title)
        if placeholder:
            field.setPlaceholderText(placeholder)
        field.textChanged.connect(self.changed)
        self.fields[key] = field
        return field

    def _reload_presets(self, select_name: str = "") -> None:
        self._presets = list_all_presets(self._presets_path)
        target_idx = 0
        with QSignalBlocker(self.preset_combo):
            self.preset_combo.clear()
            self.preset_combo.addItem("— 手动输入 / 选择常用预设 —", None)
            for idx, preset in enumerate(self._presets, start=1):
                self.preset_combo.addItem(preset.display_label, preset)
                if select_name and preset.fabric.name.casefold() == select_name.casefold():
                    target_idx = idx
            self.preset_combo.setCurrentIndex(target_idx)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        preset = self.preset_combo.currentData()
        self.delete_preset_btn.setEnabled(isinstance(preset, FabricPreset) and not preset.builtin)

    def _on_preset_selected(self) -> None:
        preset = self.preset_combo.currentData()
        self._update_preset_buttons()
        if isinstance(preset, FabricPreset):
            self.apply_fabric(preset.fabric)

    def apply_fabric(self, fabric: PlainWeave) -> None:
        mapping = {
            "name": fabric.name,
            "fabric_thickness_um": format_number(fabric.fabric_thickness_um),
            "warp_width_mm": format_number(fabric.warp_width_mm),
            "warp_spacing_mm": format_number(fabric.warp_spacing_mm),
            "weft_width_mm": format_number(fabric.weft_width_mm),
            "weft_spacing_mm": format_number(fabric.weft_spacing_mm),
        }
        for key, text in mapping.items():
            with QSignalBlocker(self.fields[key]):
                self.fields[key].setText(text)
        self.changed.emit()

    def select_preset(self, name: str) -> bool:
        for idx in range(1, self.preset_combo.count()):
            preset = self.preset_combo.itemData(idx)
            if isinstance(preset, FabricPreset) and preset.fabric.name.casefold() == name.casefold():
                self.preset_combo.setCurrentIndex(idx)
                return True
        return False

    def _save_current_preset(self) -> None:
        try:
            fabric = self.value()
            save_user_preset(fabric, path=self._presets_path)
        except (OSError, ValueError) as error:
            self.preset_notice.emit(str(error), True)
            return
        self._reload_presets(select_name=fabric.name)
        self.preset_notice.emit(f"已保存本地织物预设：{fabric.name}", False)

    def _delete_current_preset(self) -> None:
        preset = self.preset_combo.currentData()
        if not isinstance(preset, FabricPreset) or preset.builtin:
            return
        name = preset.fabric.name
        try:
            delete_user_preset(name, path=self._presets_path)
        except OSError as error:
            self.preset_notice.emit(str(error), True)
            return
        self._reload_presets()
        self.preset_notice.emit(f"已删除自定义预设：{name}", False)

    def is_blank(self) -> bool:
        return all(not field.text().strip() for field in self.fields.values())

    def value(self) -> PlainWeave:
        name = self.fields["name"].text().strip()
        if not name:
            raise ValueError("请填写 PP 型号。")
        try:
            values = {
                key: float(field.text().strip())
                for key, field in self.fields.items()
                if key != "name"
            }
        except ValueError as error:
            raise ValueError("请填写织物厚度以及经纬纱宽度和中心间距。") from error
        return PlainWeave(name=name, **values)

    def show_measure(self, geometry: PlainWeave | None, error: str = "") -> None:
        if geometry is not None:
            x_size, y_size = geometry.cell_size_mm
            text = (
                f"纱束高度：{format_number(geometry.yarn_height_um)} μm（织物厚度的一半）"
                f"   ·   2 × 2 单胞平面尺寸：{format_number(x_size)} × {format_number(y_size)} mm"
            )
            matched = next(
                (
                    p
                    for p in self._presets
                    if p.fabric.name.casefold() == geometry.name.casefold() and p.recommended_fiber_um
                ),
                None,
            )
            if matched is not None:
                text += f"   ·   建议单丝模板：{matched.recommended_fiber_um} μm"
            self.measure.setStyleSheet("")
            self.measure.setText(text)
            return

        if self.is_blank():
            self.measure.setStyleSheet("")
            self.measure.setText("请选择上方织物预设，或手动填写 PP 型号、织物厚度及经纬纱尺寸。")
        else:
            self.measure.setStyleSheet("color: #a43d30;")
            self.measure.setText(error)


class ThicknessCaseEditor(QFrame):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._fabric: PlainWeave | None = None
        self._current_mode: ThicknessMode = "total"
        layout = card_layout(self)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label("厚度方案", "Section"))
        header.addStretch()
        header.addWidget(label("输入口径", "FieldCaption"))

        self.mode_selector = QComboBox(objectName="ThicknessMode")
        self.mode_selector.setAccessibleName("厚度输入口径")
        self.mode_selector.addItem("按 PP 总厚度输入（μm）", "total")
        self.mode_selector.addItem("按剩余树脂厚度输入（μm）", "resin")
        self.mode_selector.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(self.mode_selector)

        add = QPushButton("添加工况")
        add.clicked.connect(self._append_case)
        header.addWidget(add)

        self.remove = QPushButton("删除选中")
        self.remove.clicked.connect(self._remove_case)
        header.addWidget(self.remove)
        layout.addLayout(header)

        note = label(
            "说明：微观（Fiber RVE）与细观（TexGen）使用同一种树脂；此处按不同厚度生成对应 INP 几何模型"
            "（自动写入标准占位材料以保留热力学步），纤维束等效性能 CSV 与真实树脂参数在后续 Abaqus 求解中使用。",
            "Hint",
            wrap=True,
        )
        layout.addWidget(note)

        self.table = QTableWidget(0, 5, objectName="Cases")
        self.table.setMinimumHeight(180)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(37)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)

        headings = self.table.horizontalHeader()
        for column, width in enumerate((58, 165, 165, 145)):
            headings.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, width)
        headings.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.table.currentCellChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, 1)

        hint = label(
            "双击输入厚度或子目录后缀；留空时自动命名为“PP型号_总厚度”（如 PP1080_80），"
            "填写时自动加“PP型号_”前缀，TexGen 文件直接放在该目录下。切换输入口径时自动换算已填数值。",
            "Hint",
            wrap=True,
        )
        layout.addWidget(hint)

        self._update_headers()
        self.add_case()
        self.table.setCurrentCell(0, 1)

    @property
    def mode(self) -> ThicknessMode:
        return self._current_mode

    def set_mode(self, mode: ThicknessMode) -> None:
        for index in range(self.mode_selector.count()):
            if self.mode_selector.itemData(index) == mode:
                if self.mode_selector.currentIndex() != index:
                    self.mode_selector.setCurrentIndex(index)
                return
        raise ValueError(f"不支持的厚度输入模式：{mode}")

    @property
    def selected_row(self) -> int:
        return self.table.currentRow()

    def set_fabric(self, fabric: PlainWeave | None) -> None:
        self._fabric = fabric
        self._refresh_derived()

    def add_case(self, thickness_text: str = "", dir_suffix: str = "") -> int:
        with QSignalBlocker(self.table):
            row = self.table.rowCount()
            self.table.insertRow(row)

            serial_item = self._readonly_item(str(row + 1), align_center=True)
            thickness_item = QTableWidgetItem(thickness_text)
            thickness_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            converted_item = self._readonly_item("—", align_center=True)
            suffix_item = QTableWidgetItem(dir_suffix)
            suffix_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_item = self._readonly_item("—", align_center=False)

            self.table.setItem(row, 0, serial_item)
            self.table.setItem(row, 1, thickness_item)
            self.table.setItem(row, 2, converted_item)
            self.table.setItem(row, 3, suffix_item)
            self.table.setItem(row, 4, preview_item)

        self._refresh_derived()
        self._selection_changed()
        return row

    def inputs(self) -> list[CaseInput]:
        items: list[CaseInput] = []
        for row in range(self.table.rowCount()):
            raw_thickness = self._cell_text(row, 1)
            raw_suffix = self._cell_text(row, 3)
            if not raw_thickness:
                raise ValueError(f"请填写工况 {row + 1} 的厚度（μm）。")
            try:
                thickness_val = float(raw_thickness)
            except ValueError as error:
                raise ValueError(f"工况 {row + 1} 的厚度须为有效数值。") from error
            items.append(
                CaseInput(thickness_um=thickness_val, mode=self.mode, dir_suffix=raw_suffix)
            )
        return items

    def _readonly_item(self, text: str, *, align_center: bool) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if align_center:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item is not None else ""

    def _update_headers(self) -> None:
        if self._current_mode == "total":
            primary_col = "PP 总厚度（μm）"
            secondary_col = "剩余树脂厚度（μm）"
        else:
            primary_col = "剩余树脂厚度（μm）"
            secondary_col = "PP 总厚度（μm）"
        self.table.setHorizontalHeaderLabels(
            ("序号", primary_col, secondary_col, "子目录后缀（可选）", "输出模型")
        )

    def _on_mode_changed(self) -> None:
        old_mode = self._current_mode
        new_mode: ThicknessMode = self.mode_selector.currentData()
        if new_mode == old_mode:
            return

        if self._fabric is not None:
            with QSignalBlocker(self.table):
                for row in range(self.table.rowCount()):
                    raw = self._cell_text(row, 1)
                    if not raw:
                        continue
                    try:
                        value = float(raw)
                        total_um, resin_um = resolve_thicknesses(
                            value, old_mode, self._fabric.fabric_thickness_um
                        )
                    except ValueError:
                        continue
                    converted = resin_um if new_mode == "resin" else total_um
                    self.table.item(row, 1).setText(format_number(converted))

        self._current_mode = new_mode
        self._update_headers()
        self._refresh_derived()
        self.changed.emit()

    def _selection_changed(self) -> None:
        self.remove.setEnabled(self.selected_row >= 0)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in (1, 3):
            self._refresh_derived()
            self.changed.emit()

    def _append_case(self) -> None:
        row = self.add_case()
        self.table.setCurrentCell(row, 1)
        self.table.editItem(self.table.item(row, 1))
        self.changed.emit()

    def _remove_case(self) -> None:
        row = self.selected_row
        if row < 0:
            return
        with QSignalBlocker(self.table):
            self.table.removeRow(row)
        self._refresh_derived()
        self._selection_changed()
        self.changed.emit()

    def _refresh_derived(self) -> None:
        seen_names: set[str] = set()
        with QSignalBlocker(self.table):
            for row in range(self.table.rowCount()):
                self.table.item(row, 0).setText(str(row + 1))
                raw_thickness = self._cell_text(row, 1)
                raw_suffix = self._cell_text(row, 3)

                if not raw_thickness:
                    self.table.item(row, 2).setText("—")
                    self.table.item(row, 4).setText("待填写厚度")
                    continue

                try:
                    value = float(raw_thickness)
                except ValueError:
                    self.table.item(row, 2).setText("—")
                    self.table.item(row, 4).setText("厚度须为有效数值")
                    continue

                if self._fabric is None:
                    self.table.item(row, 2).setText("—")
                    self.table.item(row, 4).setText("等待有效织物几何")
                    continue

                try:
                    prepared = prepare_case(
                        CaseInput(
                            thickness_um=value, mode=self._current_mode, dir_suffix=raw_suffix
                        ),
                        self._fabric,
                        row + 1,
                    )
                except ValueError as error:
                    self.table.item(row, 2).setText("无效")
                    self.table.item(row, 4).setText(str(error))
                    continue

                other_value = (
                    prepared.resin_thickness_um
                    if self._current_mode == "total"
                    else prepared.total_thickness_um
                )
                self.table.item(row, 2).setText(format_number(other_value))

                key = prepared.name.casefold()
                if key in seen_names:
                    self.table.item(row, 4).setText(f"目录名重复（{prepared.name}）")
                else:
                    seen_names.add(key)
                    self.table.item(row, 4).setText(f"{prepared.name}/{row + 1}.inp")
