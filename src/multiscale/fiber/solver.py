from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory

from ..common.abaqus_utils import (
    auto_per_job_memory_mb,
    check_abaqus_job_ok,
    memory_cli_args,
    run_cancellable_command,
    safe_ascii_temp_dir,
)
from ..common.resources import package_resource
from ..common.utils import clean_float, format_number
from .bundle import read_fiber_bundle_csv
from .rve import (
    UMAT_CTE_SUBROUTINE,
    UMAT_SUBROUTINE,
    FiberTemplate,
    ResinProperty,
    _parse_cell_size_um,
    plan_fiber_output_csvs,
    stage_fiber_case_files,
)

RUNNER_SCRIPT_NAME = "run_fiber_parallel.py"

# 第 1 步 Fiber 微观 RVE 7 个相互独立的求解工况：(方向键名, Job 名, Fortran 子程序)
FIBER_JOBS: tuple[tuple[str, str, str], ...] = (
    ("E11", "job-E11", UMAT_SUBROUTINE),
    ("E22", "job-E22", UMAT_SUBROUTINE),
    ("E33", "job-E33", UMAT_SUBROUTINE),
    ("G12", "job-G12", UMAT_SUBROUTINE),
    ("G13", "job-G13", UMAT_SUBROUTINE),
    ("G23", "job-G23", UMAT_SUBROUTINE),
    ("CTE", "job-CTE", UMAT_CTE_SUBROUTINE),
)


@dataclass(frozen=True)
class FiberSolveConfig:
    abaqus_command: str
    cpus: int = 2
    max_parallel: int = 7              # 默认 7 个方向同时并发提交
    total_memory_ratio: float = 0.90   # 全部并发任务合计不超过总物理内存的 90%
    retries: int = 1

    def __post_init__(self) -> None:
        if not self.abaqus_command.strip():
            raise ValueError("请填写 Abaqus 命令或路径。")
        if self.cpus < 1:
            raise ValueError("CPUs 须大于等于 1。")
        if self.max_parallel < 1:
            raise ValueError("并行方向数须大于等于 1。")
        if not (0.0 < self.total_memory_ratio <= 1.0):
            raise ValueError("总内存占比上限须介于 (0, 1] 之间。")
        if self.retries < 0:
            raise ValueError("失败重试次数不能小于 0。")


@dataclass(frozen=True)
class FiberSolveResult:
    template: FiberTemplate
    output_dir: Path
    status: str  # "succeeded" | "failed" | "cancelled"
    message: str = ""
    bundle_csv_path: Path | None = None


@dataclass(frozen=True)
class FiberBatchSummary:
    total: int
    succeeded: int
    failed: int
    cancelled: int
    results: tuple[FiberSolveResult, ...]


def export_fiber_runner(
    output_script: Path,
    *,
    cell_size_um: float,
    abaqus_command: str = "",
    cpus: int = 2,
    max_parallel: int = 7,
    total_memory_ratio: float = 0.90,
    retries: int = 1,
) -> Path:
    """导出纯 Python 标准库的 7 方向默认并行求解脚本 run_fiber_parallel.py。"""
    if cell_size_um <= 0:
        raise ValueError("单胞尺寸须大于 0 μm。")

    post_tpl_text = package_resource("fiber/templates/fiber_post.py.tpl").read_text(encoding="utf-8")
    runner_tpl = Template(
        package_resource("fiber/templates/fiber_runner.py.tpl").read_text(encoding="utf-8")
    )
    script = runner_tpl.substitute(
        cell_size_mm=repr(clean_float(cell_size_um / 1000.0)),
        abaqus_command=repr(abaqus_command.strip()),
        cpus=cpus,
        max_parallel=max_parallel,
        total_memory_ratio=repr(total_memory_ratio),
        retries=retries,
        post_template=repr(post_tpl_text),
    )
    target = Path(output_script).resolve()
    compile(script, target.name, "exec")
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent, prefix=".pp-frun-") as stage_dir:
        staged = Path(stage_dir) / target.name
        staged.write_text(script, encoding="utf-8", newline="\n")
        staged.replace(target)
    return target


def _run_direction_job(
    inputs_dir: Path,
    tmp_root: Path,
    direction: str,
    job_name: str,
    sub_name: str,
    expected_increments: int,
    per_job_memory_mb: int | None,
    config: FiberSolveConfig,
    cancel_event: threading.Event | None,
    on_log: Callable[[str], None] | None,
) -> tuple[str, str, bool, str]:
    """在纯英文隔离目录中提交并校验单个方向求解任务，避免 UMAT 编译锁冲突。"""
    job_dir = tmp_root / job_name
    last_error = "未知错误"

    for attempt in range(1, config.retries + 2):
        if cancel_event is not None and cancel_event.is_set():
            return direction, "", True, "已取消"

        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        scratch = job_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inputs_dir / f"{job_name}.inp", job_dir / f"{job_name}.inp")
        shutil.copy2(inputs_dir / sub_name, job_dir / sub_name)

        # CLI 提交：明确传入均分后的 memory 上限，保证 7 路并发总计不超过物理内存 90%
        cmd = [
            config.abaqus_command.strip(),
            f"job={job_name}",
            f"input={job_name}.inp",
            f"user={sub_name}",
            f"cpus={config.cpus}",
            *memory_cli_args(per_job_memory_mb),
            f"scratch={scratch}",
            "ask_delete=OFF",
            "interactive",
        ]
        if on_log is not None:
            on_log(f"[求解 {attempt}/{config.retries + 1}] {job_name}")

        rc, cancelled = run_cancellable_command(cmd, job_dir, job_dir / "solve.log", cancel_event)
        if cancelled:
            return direction, "", True, "已取消"
        if rc != 0:
            last_error = f"Abaqus 退出码异常（{rc}）"
            continue

        ok, reason = check_abaqus_job_ok(
            job_dir,
            job_name,
            expected_steps=1,
            expected_increments=expected_increments,
        )
        if ok:
            if on_log is not None:
                on_log(f"[完成] {job_name}")
            return direction, f"{job_name}/{job_name}.odb", False, ""
        last_error = reason

    return direction, "", False, last_error


def solve_single_fiber_case(
    template: FiberTemplate,
    resins: list[ResinProperty],
    output_csv: Path,
    config: FiberSolveConfig,
    *,
    on_step: Callable[[str], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> FiberSolveResult:
    """默认 7 方向同时并发求解单个 Fiber 模板，直接提取并原子写出唯一一份纤维束 CSV。"""
    target = Path(output_csv).resolve()
    if cancel_event is not None and cancel_event.is_set():
        return FiberSolveResult(template, target.parent, "cancelled", "已取消")

    cell_um = template.cell_size_um or _parse_cell_size_um(template.path)
    if cell_um is None:
        return FiberSolveResult(
            template, target.parent, "failed", f"无法解析模板单胞尺寸：{template.name}"
        )

    try:
        active_parallel = min(config.max_parallel, len(FIBER_JOBS))
        per_job_mem = auto_per_job_memory_mb(active_parallel, config.total_memory_ratio)
        if per_job_mem and on_log is not None:
            on_log(
                f"[内存分配] {active_parallel} 方向并发，每方向上限 {per_job_mem} MB"
                f"（合计 ≤ {int(config.total_memory_ratio * 100)}% 总内存）"
            )

        with safe_ascii_temp_dir(prefix="abq-fiber-") as tmp:
            tmp_root = Path(tmp)
            inputs_dir = tmp_root / "inputs"
            stage_fiber_case_files(template, resins, inputs_dir)

            # 1) 7 个方向在各自纯英文子目录中默认全部同时并发求解
            odb_map: dict[str, str] = {}
            internal_abort = threading.Event()

            class _CombinedCancel:
                def is_set(self) -> bool:
                    return internal_abort.is_set() or (cancel_event is not None and cancel_event.is_set())

            effective_cancel = _CombinedCancel()
            first_error = ""
            with ThreadPoolExecutor(max_workers=active_parallel) as pool:
                future_map = {
                    pool.submit(
                        _run_direction_job,
                        inputs_dir,
                        tmp_root,
                        direction,
                        job_name,
                        sub_name,
                        len(resins),
                        per_job_mem,
                        config,
                        effective_cancel,  # type: ignore[arg-type]
                        on_log,
                    ): job_name
                    for direction, job_name, sub_name in FIBER_JOBS
                }
                for future in as_completed(future_map):
                    job_name = future_map[future]
                    direction, rel_odb, cancelled, err = future.result()
                    if cancelled:
                        if on_step is not None:
                            on_step(f"{job_name} 已取消")
                        continue
                    if err:
                        if not first_error:
                            first_error = f"{job_name}：{err}"
                        internal_abort.set()
                        if on_log is not None:
                            on_log(f"[失败] {first_error}")
                        if on_step is not None:
                            on_step(f"{job_name} 失败")
                        continue
                    odb_map[direction] = rel_odb
                    if on_step is not None:
                        on_step(f"{job_name} 完成")

            if cancel_event is not None and cancel_event.is_set():
                return FiberSolveResult(template, target.parent, "cancelled", "已取消")
            if first_error or len(odb_map) != len(FIBER_JOBS):
                return FiberSolveResult(
                    template, target.parent, "failed", first_error or "部分方向未完成"
                )

            # 2) 仅启动 1 次 CAE 统一提取 7 个 ODB 并直接生成 16 列纤维束 CSV
            resin_rows = [
                (
                    resin.name,
                    format_number(resin.e_mpa),
                    format_number(resin.cte_e5),
                    format_number(resin.poisson),
                )
                for resin in resins
            ]
            post_tpl = Template(
                package_resource("fiber/templates/fiber_post.py.tpl").read_text(encoding="utf-8")
            )
            post_py = tmp_root / "extract_fiber.py"
            post_py.write_text(
                post_tpl.substitute(
                    cell_size_mm=repr(clean_float(cell_um / 1000.0)),
                    odb_map=repr(odb_map),
                    resin_rows=repr(resin_rows),
                    output_csv=repr(target.name),
                ),
                encoding="utf-8",
            )
            if on_log is not None:
                on_log(f"[后处理] {template.group}um 直接提取 7 方向结果至 {target.name}…")

            rc_post, cancelled = run_cancellable_command(
                [config.abaqus_command.strip(), "cae", f"noGUI={post_py.name}"],
                tmp_root,
                tmp_root / "post.log",
                cancel_event,
            )
            if cancelled:
                return FiberSolveResult(template, target.parent, "cancelled", "已取消")

            bundle_tmp = tmp_root / target.name
            if rc_post != 0 or not bundle_tmp.is_file():
                return FiberSolveResult(
                    template, target.parent, "failed", f"Fiber ODB 后处理失败（退出码 {rc_post}）"
                )

            # 3) 校验 CSV 完整性并原子发布唯一目标文件（零多余中间文件）
            parsed_rows = read_fiber_bundle_csv(bundle_tmp)
            if len(parsed_rows) != len(resins):
                return FiberSolveResult(
                    template,
                    target.parent,
                    "failed",
                    f"提取 CSV 行数（{len(parsed_rows)}）与输入树脂数（{len(resins)}）不一致",
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=target.parent, prefix=".pp-fiber-") as stage_dir:
                staged = Path(stage_dir) / target.name
                shutil.copy2(bundle_tmp, staged)
                staged.replace(target)

            return FiberSolveResult(
                template=template,
                output_dir=target.parent,
                status="succeeded",
                message=str(target),
                bundle_csv_path=target,
            )
    except Exception as error:
        return FiberSolveResult(template, target.parent, "failed", str(error))


def run_fiber_solve_batch(
    templates: list[FiberTemplate],
    resins: list[ResinProperty],
    output_parent: Path,
    batch_name: str,
    config: FiberSolveConfig,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> FiberBatchSummary:
    """按模板顺序逐个 7 路并发求解，并实时汇总进度与结果。"""
    if not resins:
        raise ValueError("请填写至少一组树脂性能参数。")
    planned = plan_fiber_output_csvs(templates, output_parent, batch_name)

    total_steps = len(planned) * len(FIBER_JOBS)
    done_steps = 0
    results: list[FiberSolveResult] = []
    counts = {"succeeded": 0, "failed": 0, "cancelled": 0}

    for idx, (template, target_csv) in enumerate(planned):
        if cancel_event is not None and cancel_event.is_set():
            for rem_tmpl, rem_csv in planned[idx:]:
                res = FiberSolveResult(rem_tmpl, rem_csv.parent, "cancelled", "已取消")
                results.append(res)
                counts["cancelled"] += 1
            break

        if on_log is not None:
            on_log(f"=== 开始求解模板 {template.group} μm ({template.name}) → {target_csv.name} ===")

        def _tick(msg: str) -> None:
            nonlocal done_steps
            done_steps += 1
            if on_progress is not None:
                on_progress(done_steps, total_steps, msg)

        res = solve_single_fiber_case(
            template,
            resins,
            target_csv,
            config,
            on_step=_tick,
            on_log=on_log,
            cancel_event=cancel_event,
        )
        results.append(res)
        counts[res.status] += 1

    return FiberBatchSummary(
        total=len(planned),
        succeeded=counts["succeeded"],
        failed=counts["failed"],
        cancelled=counts["cancelled"],
        results=tuple(results),
    )
