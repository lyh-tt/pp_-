# 材料多尺度建模处理工具箱

Windows 下双击项目目录里的 **材料多尺度建模处理工具箱.cmd** 启动界面。首次运行时，启动脚本使用 uv 同步 Windows 环境。

## 多尺度流程与模块

左侧 Sidebar 按多尺度建模与计算先后顺序排列：

1. **1. Fiber 微观建模**（已实现）：按单丝直径分组选择独立管理的微观 RVE 模板（`templates/fiber/` 下默认内置 5 / 6 / 7 μm 三组模板），用树脂 E（MPa）、CTE（10⁻⁵/K）与泊松比 ν 批量替换子程序与 INP 步长并生成计算目录。
2. **2. TexGen 细观建模**（已实现）：同一树脂体系下按不同总厚度或剩余树脂厚度批量生成 2 × 2 平纹单胞 TexGen 建模脚本与精简几何表。
3. **3. 纤维束 CSV 生成**（待接入）：读取 Fiber RVE 计算完成的 `Mechanical_constants.txt`，整理纤维束等效性能 CSV。
4. **4. Abaqus 批量求解**（待接入）：读取 TexGen 导出的各厚度 INP 几何模型，结合同一种树脂参数和纤维束等效性能 CSV 替换材料卡片后批量提交 Abaqus。
5. **5. RPT 后处理**（待接入）：汇总各厚度、各树脂工况下的均质化等效力学与 CTE 结果。

## 1. Fiber 微观建模说明

- RVE 模板库内置管理（按单丝直径分为 5 / 6 / 7 μm 组），界面直接展示分组供勾选，无需手动配置模板目录路径。
- 树脂性能支持在表格中逐行输入，也支持 **批量粘贴…**（兼容旧 APP 的 `(E, CTE, ν),` 元组列表与表格文本）或 **导入 CSV…**。
- 导入格式可参考项目内的示例文件 [examples/resin_inputs_example.csv](examples/resin_inputs_example.csv)，也可直接在界面点击 **示例 CSV…** 一键导出模板。
- 支持勾选单一或多个模板分组批量生成；同时在生成目录中写出 `resin_inputs.csv`，便于与后续 Abaqus 输出的 `Mechanical_constants.txt` 行号一一对应。

## 2. TexGen 细观建模说明

1. 在 **织物几何** 中自由输入 **PP 型号**、**织物厚度（μm）**，以及经纱和纬纱各自的 **纱束宽度（mm）** 与 **中心间距（mm）**。
2. 在 **厚度方案** 中选择输入口径（**按 PP 总厚度输入** 或 **按剩余树脂厚度输入**，切换口径时会自动换算已填数值）：
   - 微观（Fiber RVE）与细观（TexGen）使用同一种树脂体系，介电层薄膜仅存在总厚度差异。
   - 剩余树脂厚度 = PP 总厚度 − 织物厚度。
   - 表格中“子目录后缀（可选）”留空时按 `PP型号_总厚度` 自动命名（如 `PP1080_80`），填写时自动拼接 `PP型号_` 前缀；TexGen 导出的 `1.inp`、`1.ori`、`1.eld`、`1.tg3` 直接放入该工况目录下。
   - TexGen 阶段无需导入纤维束等效性能 CSV，导出脚本会自动写入标准占位 `Mat0`/`Mat1` 参数以保留完整热力学计算步；真实树脂与纤维束等效参数在后续 Abaqus 批量计算阶段替换写入。
3. 在底部选择输出目录，可选填写带 `PP型号_` 前缀的自定义 **批次目录** 后缀（留空按 `PP型号_TexGen_时间戳` 自动命名），点击 **生成 TexGen 脚本**；随后在 TexGen 中通过 **Python → Run Script** 执行即可。

## 开发与打包

~~~powershell
Set-Location 'D:\1_sdu\pp_树脂牌号计算工具\pp_rve_rpt_texgen'
$env:UV_PROJECT_ENVIRONMENT = '.venv-win'
uv sync --locked
uv run --locked pp-rve-rpt-texgen
uv run --locked pytest
~~~

使用 PyInstaller 打包独立单文件可执行程序（输出至 `dist\材料多尺度建模处理工具箱.exe`，内置 Fiber 模板库、示例 CSV、样式与 TexGen 脚本模板）：

~~~powershell
Set-Location 'D:\1_sdu\pp_树脂牌号计算工具\pp_rve_rpt_texgen'
.\packaging\build_exe.ps1
~~~

PySide6 界面、模块注册表、Fiber 微观建模引擎、TexGen 几何工作流与导出代码都在 `src/texgen` 中；独立 Fiber 模板库位于 `templates/fiber`。
