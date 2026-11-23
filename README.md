# 材料多尺度建模处理工具箱

Windows 下双击项目目录里的 **材料多尺度建模处理工具箱.cmd**（或运行打包后的 `dist\材料多尺度建模处理工具箱.exe`）启动界面。

## 统一工程目录与 3 步闭环

在窗口顶部 **当前工程目录** 选择一次全英文工作区后，工具箱会自动按统一目录结构组织并上下游自动联动：

~~~text
<工程目录>/
├── 01_fiber/                          # 第 1 步产出：resin_inputs.csv、Mechanical_constants.txt、*_fiber_bundle.csv
├── 02_texgen/                         # 第 2 步产出：TexGen 建模总脚本与各厚度工况 .inp / .ori / .eld
├── 03_rpt/                            # 第 3 步产出：各厚度模型/树脂工况 pp*.rpt（零 ODB 残留）
└── effective_properties_summary.csv   # 最终端到端汇总总表（MPa / ppm/K）
~~~

左侧导航精简为 **3 个核心步骤**：

1. **1. Fiber 微观求解**：勾选 5 / 6 / 7 μm 单丝模板并输入树脂参数，一键 7 方向并行求解（总内存自动控制在总物理内存 90% 以内，零 ODB 残留），自动产出 `Mechanical_constants.txt` 与 `*_fiber_bundle.csv` 并联动至第 3 步；同时内置 **合成已有 CSV…** 辅助弹窗兼容离线/服务器求解结果，并支持导出独立并行脚本 `run_fiber_parallel.py`。
2. **2. TexGen 细观建模**：内置 5 款常见玻纤布预设（`PP1080` / `PP2116` / `PP3313` / `PP1035` / `PP1027`）并支持本地保存/删除自定义预设，按总厚度或剩余树脂厚度批量导出 2 × 2 平纹 TexGen 建模脚本，自动联动第 3 步。
3. **3. 细观求解与汇总**：自动带入第 1 步纤维束 CSV 与第 2 步 TexGen 几何目录；本机并发求解 Abaqus（纯英文隔离临时目录、断点续算、仅保留 RPT）后自动在「RPT 汇总表」标签页解析全部 `.rpt` 并导出 `effective_properties_summary.csv`，也支持导出独立脚本 `run_abaqus_batch.py` 或单独导入外部 RPT 目录汇总。

## 源码目录结构

~~~text
src/multiscale/
├── common/   # 资源定位、数值/文本工具、Abaqus 隔离临时目录与 90% 内存预算、工程目录布局
├── fiber/    # 第 1 步：微观 RVE、默认 7 路并行求解、纤维束 CSV
├── texgen/   # 第 2 步：平纹织物几何、常见玻纤布预设与 TexGen 批量脚本导出
├── abaqus/   # 第 3 步：TexGen-Abaqus 批量求解与独立脚本导出
├── post/     # 第 3 步：RPT 解析与汇总 CSV
└── ui/       # PySide6 图形界面（顶部工程条 + 3 步主页面 + 辅助 CSV 弹窗）
~~~

## 1. Fiber 微观求解说明

- RVE 模板库内置管理（按单丝直径分为 5 / 6 / 7 μm 组），界面直接展示分组供勾选。
- 树脂性能支持表格逐行输入、**批量粘贴…**（兼容旧 APP `(E, CTE, ν),` 元组列表与表格文本）或 **导入 CSV…**（参考 [examples/resin_inputs_example.csv](examples/resin_inputs_example.csv) 或点击 **示例 CSV…**）。
- **开始本机求解** 默认对 7 个方向同时并发提交（自动均分 ≤ 90% 物理内存，在纯英文隔离目录运行），完成后自动产出 `Mechanical_constants.txt` 与 `*_fiber_bundle.csv`；点击 **生成求解文件** 则输出 `.inp` / `.f` / `resin_inputs.csv` 与服务器独立并行脚本 `run_fiber_parallel.py`。
- 若已在服务器跑完求解，可点击 **合成已有 CSV…** 读取 `Mechanical_constants.txt` 合成为标准纤维束 CSV 并自动带入第 3 步。

## 2. TexGen 细观建模说明

1. 在 **织物几何 → 织物预设** 下拉框中可直接选取常用规格（`PP1080` / `PP2116` / `PP3313` / `PP1035` / `PP1027`）自动填充尺寸并提示建议单丝模板；修改或新增规格后点击 **保存为预设** 即可保存到本机供下次一键复用。
2. 在 **厚度方案** 中选择输入口径（**按 PP 总厚度输入** 或 **按剩余树脂厚度输入**，切换口径时自动换算已填数值）。
3. 点击 **生成 TexGen 脚本** 后，在 TexGen 中通过 **Python → Run Script** 执行总脚本即可生成全部工况体素网格与 INP 模板；回到工具箱切换到第 3 步会自动识别生成的模型。

## 3. 细观求解与汇总说明

- **Abaqus 批量求解**：自动关联第 2 步几何目录与第 1 步纤维束 CSV（无输入时也可随时点 **选择目录… / 导入 CSV… / 刷新**）；每个任务都在纯英文隔离临时目录运行并提取 `.rpt`，退出即清空 `.odb` / `.inp` 等中间文件。
- **RPT 汇总表**：求解完成后自动汇总全部 `.rpt` 并换算为工程单位（模量 `MPa`、CTE `ppm/K`），默认输出至工程根目录 `effective_properties_summary.csv`；也可直接选择任意外部 RPT 目录进行解析与导出。

## 开发与打包

~~~powershell
Set-Location 'D:\1_sdu\pp_树脂牌号计算工具\pp_rve_rpt_texgen'
$env:UV_PROJECT_ENVIRONMENT = '.venv-win'
uv sync --locked
uv run --locked pp-rve-rpt-texgen
uv run --locked pytest
~~~

使用 PyInstaller 打包独立单文件可执行程序（输出至 `dist\材料多尺度建模处理工具箱.exe`）：

~~~powershell
Set-Location 'D:\1_sdu\pp_树脂牌号计算工具\pp_rve_rpt_texgen'
.\packaging\build_exe.ps1
~~~
