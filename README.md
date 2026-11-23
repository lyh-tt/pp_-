# 材料多尺度建模处理工具箱

Windows 下双击项目目录里的 **材料多尺度建模处理工具箱.cmd**（或运行打包后的 `dist\材料多尺度建模处理工具箱.exe`）启动界面。

## 多尺度流程与模块

左侧 Sidebar 按多尺度建模与计算先后顺序排列：

1. **1. Fiber 微观建模**（已实现）：按单丝直径分组选择内置微观 RVE 模板（5 / 6 / 7 μm），批量替换树脂 `E`（MPa）、`CTE`（10⁻⁵/K）与泊松比 `ν`；支持一键 7 方向并行求解（总内存自动控制在总物理内存 90% 以内，零 ODB 残留，直接产出 `Mechanical_constants.txt` 与 `*_fiber_bundle.csv`），也可导出含独立并行脚本 `run_fiber_parallel.py` 的求解目录。
2. **2. TexGen 细观建模**（已实现）：同一树脂体系下按不同总厚度或剩余树脂厚度批量生成 2 × 2 平纹单胞 TexGen 建模脚本与精简几何表。
3. **3. 纤维束 CSV 生成**（已实现）：读取第 1 步求解完成的 `Mechanical_constants.txt`，自动关联 `resin_inputs.csv`（也兼容手动选择/粘贴树脂参数），直接导出单份干净的纤维束等效性能 CSV。
4. **4. Abaqus 批量求解**（已实现）：直接将 TexGen 导出的 `.inp` 模型中的 `Mat0`（树脂）与 `Mat1`（纤维束）替换为真实材料参数；每个任务都在纯英文隔离临时目录求解并提取 `.rpt`，随后自动清空全部中间文件，仅保留 `<输出目录>/<模型名>/pp*.rpt` 并可自动汇总 CSV；同时支持导出零外部依赖的单文件独立求解脚本供本机或 Linux 服务器运行。
5. **5. RPT 后处理**（已实现）：递归解析各厚度、各树脂工况 `.rpt` 等效性能并换算为 `MPa` 与 `ppm/K`，导出汇总 CSV。

## 源码目录结构

~~~text
src/multiscale/
├── common/   # 资源定位、数值/文本工具、Abaqus 隔离临时目录与 90% 内存预算
├── fiber/    # 第 1 步 & 第 3 步：微观 RVE、默认 7 路并行求解、纤维束 CSV
├── texgen/   # 第 2 步：平纹织物几何与 TexGen 批量脚本导出
├── abaqus/   # 第 4 步：TexGen-Abaqus 批量求解与独立脚本导出
├── post/     # 第 5 步：RPT 解析与汇总 CSV
└── ui/       # PySide6 图形界面与各步骤页面
~~~

## 1. Fiber 微观建模说明

- RVE 模板库内置管理（按单丝直径分为 5 / 6 / 7 μm 组），界面直接展示分组供勾选。
- 树脂性能支持表格逐行输入、**批量粘贴…**（兼容旧 APP `(E, CTE, ν),` 元组列表与表格文本）或 **导入 CSV…**。
- 导入格式可参考项目内的示例文件 [examples/resin_inputs_example.csv](examples/resin_inputs_example.csv)，也可直接在界面点击 **示例 CSV…** 一键导出模板。
- **开始本机求解** 默认对 7 个方向同时并发提交（自动均分 ≤ 90% 物理内存，在纯英文隔离目录运行），完成后自动产出 `Mechanical_constants.txt` 与 `*_fiber_bundle.csv`；点击 **生成求解文件** 则输出 `.inp` / `.f` / `resin_inputs.csv` 与服务器独立并行脚本 `run_fiber_parallel.py`。

## 2. TexGen 细观建模说明

1. 在 **织物几何** 中自由输入 **PP 型号**、**织物厚度（μm）**，以及经纱和纬纱各自的 **纱束宽度（mm）** 与 **中心间距（mm）**。
2. 在 **厚度方案** 中选择输入口径（**按 PP 总厚度输入** 或 **按剩余树脂厚度输入**，切换口径时会自动换算已填数值），导出各工况至平坦目录 `<批次目录>/<PP型号_厚度>/1.inp`。
3. 点击 **生成 TexGen 脚本** 后，在 TexGen 中通过 **Python → Run Script** 执行总脚本即可生成全部工况体素网格与 INP 模板。

## 3. 纤维束 CSV 生成说明

- 无需复制模板目录，只生成**单份** `<命名>_fiber_bundle.csv`。
- 列依次为：`Resin Name, E1MPa, CTE/e-5, Poisson, E1, E2, E3, V12, V13, V23, G12, G13, G23, CTE_X, CTE_Y, CTE_Z`。
- 第 4 步同时兼容此 16 列 CSV（保留精确 `Poisson`）和旧版 14 列 CSV。

## 4. Abaqus 批量求解说明

- **本机并发求解**：选择第 2 步几何目录与第 3 步纤维束 CSV，配置 `Abaqus 命令`（自动探测本机安装）、`CPUs` 与 `并行任务数`，支持断点续算与随时终止。
- **零中间文件残留**：每个任务均在系统临时目录中运行 Abaqus 与 RPT 后处理，成功核验后原子保存 `<输出目录>/<模型名>/pp<E_GPa>_<CTE>e_5.rpt`，退出即清空 `.odb` / `.inp` / `.sta` 等过程文件。
- **导出独立脚本**：点击 **导出独立脚本…** 生成单个纯 Python 3 标准库脚本 `run_abaqus_batch.py`，可直接放到 Linux 服务器运行。

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
