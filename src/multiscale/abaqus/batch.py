from __future__ import annotations

import re
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory

from ..common.abaqus_utils import (
    FAIL_MARKERS,
    SUCCESS_MARKERS,
    check_abaqus_job_ok,
    detect_abaqus_command,
    memory_cli_args,
    run_cancellable_command,
    safe_ascii_temp_dir,
)
from ..common.resources import package_resource
from ..common.utils import format_number, natural_sort_key
from ..fiber.bundle import FiberBundleRow

__all__ = [
    "AbaqusTask",
    "BatchSummary",
    "FAIL_MARKERS",
    "SUCCESS_MARKERS",
    "SolveConfig",
    "TaskResult",
    "TexGenModel",
    "detect_abaqus_command",
    "discover_texgen_models",
    "export_standalone_runner",
    "format_duration",
    "patch_texgen_inp",
    "plan_abaqus_tasks",
    "run_abaqus_batch",
    "run_single_task",
]


@dataclass(frozen=True)
class TexGenModel:
    name: str
    inp_path: Path
    ori_path: Path
    eld_path: Path | None = None


@dataclass(frozen=True)
class SolveConfig:
    abaqus_command: str
    cpus: int = 2
    max_parallel: int = 2
    retries: int = 1
    resume_existing: bool = True
    scratch_dir: Path | None = None
    memory_mb: int | None = None

    def __post_init__(self) -> None:
        if not self.abaqus_command.strip():
            raise ValueError("请填写 Abaqus 命令或路径。")
        if self.cpus < 1:
            raise ValueError("CPUs 须大于等于 1。")
        if self.max_parallel < 1:
            raise ValueError("并行任务数须大于等于 1。")
        if self.retries < 0:
            raise ValueError("失败重试次数不能小于 0。")
        if self.memory_mb is not None and self.memory_mb < 0:
            raise ValueError("单任务内存上限不能小于 0。")


@dataclass(frozen=True)
class AbaqusTask:
    model: TexGenModel
    row: FiberBundleRow
    job_name: str
    output_rpt: Path
    task_index: int = 1
    total_tasks: int = 1
    model_index: int = 1
    total_models: int = 1
    resin_index: int = 1
    total_resins: int = 1


@dataclass(frozen=True)
class TaskResult:
    task: AbaqusTask
    status: str  # "succeeded" | "skipped" | "failed" | "cancelled"
    message: str = ""
    elapsed_sec: float = 0.0


@dataclass(frozen=True)
class BatchSummary:
    total: int
    succeeded: int
    skipped: int
    failed: int
    cancelled: int
    results: tuple[TaskResult, ...]


def format_duration(seconds: float) -> str:
    """将秒数格式化为简洁耗时标签（如 42s / 4m12s / 1h05m）。"""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, rem_mins = divmod(minutes, 60)
    return f"{hours}h{rem_mins:02d}m"


_format_duration = format_duration


def _resin_tag(task: AbaqusTask) -> str:
    width = max(3, len(str(task.total_resins)))
    seq = f"#{task.resin_index:0{width}d}"
    name = task.row.resin_name.strip()
    return f"{seq} {name}" if name else seq


def _inspect_model_dir(folder: Path, model_name: str) -> TexGenModel | None:
    inps = sorted(folder.glob("*.inp"), key=lambda p: natural_sort_key(p.name))
    oris = sorted(folder.glob("*.ori"), key=lambda p: natural_sort_key(p.name))
    if not inps or not oris:
        return None
    elds = sorted(folder.glob("*.eld"), key=lambda p: natural_sort_key(p.name))
    return TexGenModel(
        name=model_name,
        inp_path=inps[0].resolve(),
        ori_path=oris[0].resolve(),
        eld_path=elds[0].resolve() if elds else None,
    )


def discover_texgen_models(root_dir: Path) -> list[TexGenModel]:
    root = Path(root_dir).resolve()
    if not root.is_dir():
        return []

    direct = _inspect_model_dir(root, root.name)
    if direct is not None:
        return [direct]

    models: list[TexGenModel] = []
    subdirs = sorted(
        (entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(".")),
        key=lambda entry: natural_sort_key(entry.name),
    )
    for sub in subdirs:
        found = _inspect_model_dir(sub, sub.name)
        if found is not None:
            models.append(found)
            continue
        for child in sub.iterdir():
            if child.is_dir() and child.name.lower() == "texgen":
                nested = _inspect_model_dir(child, sub.name)
                if nested is not None:
                    models.append(nested)
                    break
    return models


def _replace_aux_tokens(line: str, aux_map: dict[str, str]) -> str:
    result = line
    for old_name, new_name in aux_map.items():
        pattern = re.compile(
            r"(?<![A-Za-z0-9_\-.])" + re.escape(old_name) + r"(?![A-Za-z0-9_\-.])"
        )
        result = pattern.sub(new_name, result)
    return result


def _skip_data_lines(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].lstrip().startswith("*"):
        index += 1
    return index


def patch_texgen_inp(
    template_inp: Path,
    output_inp: Path,
    row: FiberBundleRow,
    aux_map: dict[str, str],
) -> None:
    lines = Path(template_inp).read_text(encoding="utf-8", errors="ignore").splitlines(True)
    out: list[str] = []
    current_mat = ""
    replaced = {
        "mat0_elastic": False,
        "mat0_expansion": False,
        "mat1_elastic": False,
        "mat1_expansion": False,
    }

    e_pa = row.e_mpa * 1e6
    cte_resin = row.cte_e5 * 1e-5
    elastic_vals = (
        row.e1 * 1e6, row.e2 * 1e6, row.e3 * 1e6,
        row.v12, row.v13, row.v23,
        row.g12 * 1e6, row.g13 * 1e6, row.g23 * 1e6,
    )

    idx = 0
    while idx < len(lines):
        line = _replace_aux_tokens(lines[idx], aux_map)
        stripped = line.strip().lower()
        compact = stripped.replace(" ", "")

        if compact.startswith("*material") and "name=" in compact:
            match = re.search(r"name=([^,\s]+)", compact)
            current_mat = match.group(1) if match else ""

        if stripped.startswith("*elastic") and "type=engineeringconstants" in compact:
            out.append(line)
            out.append(
                f" {elastic_vals[0]:.6E}, {elastic_vals[1]:.6E}, {elastic_vals[2]:.6E}, "
                f"{elastic_vals[3]:.6E}, {elastic_vals[4]:.6E}, {elastic_vals[5]:.6E}, "
                f"{elastic_vals[6]:.6E}, {elastic_vals[7]:.6E}\n"
            )
            out.append(f" {elastic_vals[8]:.6E}\n")
            idx = _skip_data_lines(lines, idx + 1)
            replaced["mat1_elastic"] = True
            continue

        if stripped.startswith("*elastic") and "type=" not in compact and current_mat in ("", "mat0"):
            out.append(line)
            out.append(f" {e_pa:.6E}, {row.poisson:g}\n")
            idx = _skip_data_lines(lines, idx + 1)
            replaced["mat0_elastic"] = True
            continue

        if stripped.startswith("*expansion") and "type=ortho" in compact:
            out.append(line)
            out.append(f" {row.cte_x:.6E}, {row.cte_y:.6E}, {row.cte_z:.6E}\n")
            idx = _skip_data_lines(lines, idx + 1)
            replaced["mat1_expansion"] = True
            continue

        if stripped.startswith("*expansion") and "type=" not in compact and current_mat in ("", "mat0"):
            out.append(line)
            out.append(f" {cte_resin:.6E}\n")
            idx = _skip_data_lines(lines, idx + 1)
            replaced["mat0_expansion"] = True
            continue

        out.append(line)
        idx += 1

    missing = [key for key, done in replaced.items() if not done]
    if missing:
        raise ValueError(
            f"INP 模板缺少所需材料卡片（{', '.join(missing)}）：{Path(template_inp).name}"
        )

    Path(output_inp).write_text("".join(out), encoding="utf-8", newline="\n")


def plan_abaqus_tasks(
    models: list[TexGenModel],
    rows: list[FiberBundleRow],
    output_dir: Path,
) -> list[AbaqusTask]:
    if not models:
        raise ValueError("请选择至少一个 TexGen 几何模型。")
    if not rows:
        raise ValueError("纤维束 CSV 中没有有效的材料行。")

    out_root = Path(output_dir).resolve()
    total_models = len(models)
    total_resins = len(rows)
    total_tasks = total_models * total_resins
    pad_width = max(3, len(str(total_resins)))

    tasks: list[AbaqusTask] = []
    task_index = 0
    for m_idx, model in enumerate(models, start=1):
        used_stems: dict[str, int] = {}
        for r_idx, row in enumerate(rows, start=1):
            task_index += 1
            stem = row.task_stem(r_idx, pad_width=pad_width)
            seen = used_stems.get(stem, 0) + 1
            used_stems[stem] = seen
            job_name = stem if seen == 1 else f"{stem}_{seen}"
            tasks.append(
                AbaqusTask(
                    model=model,
                    row=row,
                    job_name=job_name,
                    output_rpt=out_root / model.name / f"{job_name}.rpt",
                    task_index=task_index,
                    total_tasks=total_tasks,
                    model_index=m_idx,
                    total_models=total_models,
                    resin_index=r_idx,
                    total_resins=total_resins,
                )
            )
    return tasks


def _count_inp_steps(inp_path: Path) -> int:
    text = inp_path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r"^\s*\*Step\b", text, flags=re.IGNORECASE | re.MULTILINE))


def run_single_task(
    task: AbaqusTask,
    config: SolveConfig,
    *,
    post_template: Template | None = None,
    cancel_event: threading.Event | None = None,
    on_log: Callable[[str], None] | None = None,
    on_start: Callable[[AbaqusTask], None] | None = None,
) -> TaskResult:
    if cancel_event is not None and cancel_event.is_set():
        return TaskResult(task, "cancelled", "已取消")

    pos = f"{task.task_index}/{task.total_tasks}"
    resin_tag = _resin_tag(task)

    if task.resin_index == 1 and on_log is not None:
        on_log(
            f"=== 模型 {task.model_index}/{task.total_models}：{task.model.name}（共 {task.total_resins} 组树脂）==="
        )

    if config.resume_existing and task.output_rpt.is_file() and task.output_rpt.stat().st_size > 0:
        if on_log is not None:
            on_log(f"[{pos} 跳过] {task.model.name} | {resin_tag}（已有 {task.output_rpt.name}）")
        return TaskResult(task, "skipped", "已有 RPT")

    if on_start is not None:
        on_start(task)

    tpl = post_template or Template(
        package_resource("abaqus/templates/rve_post.py.tpl").read_text(encoding="utf-8")
    )
    max_attempts = config.retries + 1
    task_start = time.monotonic()
    last_error = "未知错误"

    for attempt in range(1, max_attempts + 1):
        if cancel_event is not None and cancel_event.is_set():
            return TaskResult(task, "cancelled", "已取消")

        if on_log is not None:
            if attempt == 1:
                on_log(
                    f"[{pos} 开始] {task.model.name} | {resin_tag}"
                    f"（E={format_number(task.row.e_mpa)} MPa, CTE={format_number(task.row.cte_e5)}e-5）"
                )
            else:
                on_log(
                    f"[{pos} 重试 {attempt}/{max_attempts}] {task.model.name} | {resin_tag}"
                    f"（上次失败：{last_error}）"
                )

        saved_ok = False
        try:
            with safe_ascii_temp_dir(prefix="abq-work-") as tmp:
                work_dir = Path(tmp)
                aux_map: dict[str, str] = {}
                shutil.copy2(task.model.ori_path, work_dir / f"{task.job_name}.ori")
                aux_map[task.model.ori_path.name] = f"{task.job_name}.ori"
                if task.model.eld_path is not None:
                    shutil.copy2(task.model.eld_path, work_dir / f"{task.job_name}.eld")
                    aux_map[task.model.eld_path.name] = f"{task.job_name}.eld"

                inp_path = work_dir / f"{task.job_name}.inp"
                patch_texgen_inp(task.model.inp_path, inp_path, task.row, aux_map)
                expected_steps = _count_inp_steps(inp_path)

                scratch_root = (
                    Path(config.scratch_dir).resolve()
                    if config.scratch_dir is not None
                    else (work_dir / "scratch")
                )
                task_scratch = scratch_root / f"{task.job_name}_{threading.get_ident()}"
                task_scratch.mkdir(parents=True, exist_ok=True)
                try:
                    solve_cmd = [
                        config.abaqus_command,
                        f"job={task.job_name}",
                        f"input={task.job_name}.inp",
                        f"cpus={config.cpus}",
                        *memory_cli_args(config.memory_mb),
                        f"scratch={task_scratch}",
                        "ask_delete=OFF",
                        "interactive",
                    ]
                    rc, cancelled = run_cancellable_command(
                        solve_cmd, work_dir, work_dir / "solve.log", cancel_event
                    )
                    if cancelled:
                        return TaskResult(task, "cancelled", "已取消")
                    if rc != 0:
                        last_error = f"Abaqus 退出码异常（{rc}）"
                        continue

                    ok, reason = check_abaqus_job_ok(
                        work_dir, task.job_name, expected_steps=expected_steps
                    )
                    if not ok:
                        last_error = reason
                        continue

                    post_script = work_dir / "extract_rpt.py"
                    post_script.write_text(
                        tpl.substitute(model_name=repr(task.job_name)),
                        encoding="utf-8",
                    )
                    rc_post, cancelled = run_cancellable_command(
                        [config.abaqus_command, "cae", "noGUI=extract_rpt.py"],
                        work_dir,
                        work_dir / "post.log",
                        cancel_event,
                    )
                    if cancelled:
                        return TaskResult(task, "cancelled", "已取消")

                    rpt_tmp = work_dir / f"{task.job_name}.rpt"
                    if rc_post != 0 or not rpt_tmp.is_file() or rpt_tmp.stat().st_size == 0:
                        last_error = f"RPT 提取失败（退出码 {rc_post}）"
                        continue

                    rpt_text = rpt_tmp.read_text(encoding="utf-8", errors="ignore")
                    if "Effective material properties" not in rpt_text or "CTE_1" not in rpt_text:
                        last_error = "RPT 缺少等效材料性能字段"
                        continue

                    task.output_rpt.parent.mkdir(parents=True, exist_ok=True)
                    with TemporaryDirectory(
                        dir=task.output_rpt.parent,
                        prefix=".rpt-",
                        ignore_cleanup_errors=True,
                    ) as stage_dir:
                        staged = Path(stage_dir) / task.output_rpt.name
                        shutil.copy2(rpt_tmp, staged)
                        staged.replace(task.output_rpt)
                    saved_ok = True
                finally:
                    if config.scratch_dir is not None and task_scratch.exists():
                        shutil.rmtree(task_scratch, ignore_errors=True)
        except Exception as error:
            if not saved_ok:
                last_error = str(error)
                continue

        if saved_ok:
            elapsed = time.monotonic() - task_start
            if on_log is not None:
                on_log(
                    f"[{pos} 完成] {task.model.name} | {resin_tag}"
                    f"（耗时 {format_duration(elapsed)}）→ {task.output_rpt.name}"
                )
            return TaskResult(task, "succeeded", str(task.output_rpt), elapsed_sec=elapsed)

    elapsed = time.monotonic() - task_start
    if on_log is not None:
        on_log(
            f"[{pos} 失败] {task.model.name} | {resin_tag}"
            f"（耗时 {format_duration(elapsed)}）：{last_error}"
        )
    return TaskResult(task, "failed", last_error, elapsed_sec=elapsed)


def run_abaqus_batch(
    tasks: list[AbaqusTask],
    config: SolveConfig,
    *,
    on_progress: Callable[[int, int, TaskResult], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_task_start: Callable[[AbaqusTask], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> BatchSummary:
    if not tasks:
        raise ValueError("没有待执行的求解任务。")

    post_template = Template(
        package_resource("abaqus/templates/rve_post.py.tpl").read_text(encoding="utf-8")
    )
    total = len(tasks)
    results: list[TaskResult] = []
    counts = {"succeeded": 0, "skipped": 0, "failed": 0, "cancelled": 0}

    with ThreadPoolExecutor(max_workers=config.max_parallel) as pool:
        future_map = {
            pool.submit(
                run_single_task,
                task,
                config,
                post_template=post_template,
                cancel_event=cancel_event,
                on_log=on_log,
                on_start=on_task_start,
            ): task
            for task in tasks
        }
        completed = 0
        for future in as_completed(future_map):
            result = future.result()
            results.append(result)
            counts[result.status] += 1
            completed += 1
            if on_progress is not None:
                on_progress(completed, total, result)

    return BatchSummary(
        total=total,
        succeeded=counts["succeeded"],
        skipped=counts["skipped"],
        failed=counts["failed"],
        cancelled=counts["cancelled"],
        results=tuple(results),
    )


def export_standalone_runner(
    output_script: Path,
    *,
    models_dir: Path,
    fiber_csv: Path,
    output_dir: Path,
    config: SolveConfig,
    default_poisson: float = 0.38,
) -> Path:
    if not isfinite(default_poisson) or not (-1.0 < default_poisson < 0.5):
        raise ValueError("默认树脂泊松比须介于 -1 和 0.5 之间。")

    post_tpl_text = package_resource("abaqus/templates/rve_post.py.tpl").read_text(encoding="utf-8")
    runner_tpl = Template(
        package_resource("abaqus/templates/abaqus_runner.py.tpl").read_text(encoding="utf-8")
    )
    script = runner_tpl.substitute(
        models_dir=repr(str(Path(models_dir).resolve())),
        fiber_csv=repr(str(Path(fiber_csv).resolve())),
        output_dir=repr(str(Path(output_dir).resolve())),
        abaqus_command=repr(config.abaqus_command.strip()),
        cpus=config.cpus,
        max_parallel=config.max_parallel,
        retries=config.retries,
        resume_existing=repr(config.resume_existing),
        default_poisson=repr(default_poisson),
        scratch_dir=repr(str(config.scratch_dir.resolve()) if config.scratch_dir else ""),
        memory_mb=repr(config.memory_mb or 0),
        post_template=repr(post_tpl_text),
    )
    target = Path(output_script).resolve()
    compile(script, target.name, "exec")
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent, prefix=".pp-runner-") as stage_dir:
        staged = Path(stage_dir) / target.name
        staged.write_text(script, encoding="utf-8", newline="\n")
        staged.replace(target)
    return target
