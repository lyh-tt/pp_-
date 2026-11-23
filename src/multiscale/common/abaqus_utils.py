from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .utils import natural_sort_key

# Abaqus 常见失败/成功特征关键字（用于日志与状态校验）
FAIL_MARKERS = (
    "THE ANALYSIS HAS NOT BEEN COMPLETED",
    "TIME INCREMENT REQUIRED IS LESS THAN THE MINIMUM SPECIFIED",
    "TOO MANY ATTEMPTS MADE FOR THIS INCREMENT",
    "ABAQUS/STANDARD ANALYSIS EXITED WITH AN ERROR",
    "ABAQUS ERROR",
)
SUCCESS_MARKERS = (
    "THE ANALYSIS HAS COMPLETED SUCCESSFULLY",
    "THE ANALYSIS HAS BEEN COMPLETED",
)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("sullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def detect_total_memory_mb() -> int | None:
    """读取本机总物理内存（MB），跨平台支持 Windows 与 Linux。"""
    try:
        if os.name == "nt":
            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.ullTotalPhys // (1024 * 1024))
            return None
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            if page_size > 0 and phys_pages > 0:
                return int((page_size * phys_pages) // (1024 * 1024))
    except Exception:
        pass

    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo.read_text(errors="ignore"), re.MULTILINE)
        if match:
            return int(match.group(1)) // 1024
    return None


def auto_per_job_memory_mb(parallel_jobs: int, total_ratio: float = 0.90) -> int | None:
    """按并发任务数均分物理内存上限，确保全部任务总占用 <= total_ratio（默认 90%）。"""
    if parallel_jobs < 1:
        raise ValueError("并发任务数须大于等于 1。")
    if not (0.0 < total_ratio <= 1.0):
        raise ValueError("总内存占比上限须在 (0, 1] 之间。")

    total_mb = detect_total_memory_mb()
    if total_mb is None or total_mb <= 0:
        return None
    per_job = int(total_mb * total_ratio) // parallel_jobs
    return per_job if per_job >= 64 else None


def is_ascii_path(path: Path) -> bool:
    """判断路径是否仅包含 ASCII 字符，避免 Abaqus 内核在中文路径下 0xC0000142 崩溃。"""
    return str(Path(path).resolve()).isascii()


def non_ascii_path_warning(path: Path) -> str | None:
    """若路径含中文或非 ASCII 字符，返回提示文案；全英文路径返回 None。"""
    if is_ascii_path(path):
        return None
    return "注意：当前输出路径包含中文或非 ASCII 字符，直接在此目录手动运行 Abaqus CAE 可能崩溃（建议仅使用英文目录，或点击“开始本机求解”自动隔离运行）。"


def safe_ascii_temp_dir(prefix: str = "abq-") -> TemporaryDirectory[str]:
    """返回纯 ASCII 路径的临时目录；若系统 %TEMP% 含中文，则回退到 C:\\abq_tmp。"""
    system_tmp = Path(tempfile.gettempdir())
    if str(system_tmp).isascii():
        return TemporaryDirectory(prefix=prefix)

    if os.name == "nt":
        drive = os.environ.get("SystemDrive", "C:")
        fallback = Path(f"{drive}{os.sep}abq_tmp")
    else:
        fallback = Path("/tmp/abq_tmp")
    fallback.mkdir(parents=True, exist_ok=True)
    return TemporaryDirectory(dir=fallback, prefix=prefix)


def detect_abaqus_command() -> str:
    """自动探测本机可用的 Abaqus 启动命令（优先 PATH，其次常见安装目录）。"""
    for name in ("abq2026", "abq2024", "abq2022", "abaqus"):
        located = shutil.which(name)
        if located:
            return located

    search_roots = (Path("D:/abaqus"), Path("D:/ABAQUS"), Path("C:/SIMULIA"), Path("D:/SIMULIA"))
    candidates: list[Path] = []
    for root in search_roots:
        if root.is_dir():
            candidates.extend(root.glob("**/abq*.bat"))
            candidates.extend(root.glob("**/abaqus.bat"))
    if candidates:
        candidates.sort(key=lambda p: natural_sort_key(str(p)), reverse=True)
        return str(candidates[0])
    return ""


def memory_cli_args(memory_mb: int | None) -> list[str]:
    """构造无空格的 Abaqus memory 参数（如 memory=3072mb），防止 cmd /c 断词。"""
    if memory_mb is None or memory_mb <= 0:
        return []
    return [f"memory={int(memory_mb)}mb"]


def terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """跨平台终止 Abaqus 及其派生求解子进程树。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_cancellable_command(
    cmd: list[str],
    cwd: Path,
    log_path: Path,
    cancel_event: threading.Event | None,
) -> tuple[int, bool]:
    """在指定目录执行命令，支持取消标志并把合并输出写入 log_path。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    full_cmd = (
        ["cmd", "/c", *cmd]
        if (os.name == "nt" and cmd[0].lower().endswith((".bat", ".cmd")))
        else cmd
    )
    with log_path.open("w", encoding="utf-8", errors="ignore") as out:
        proc = subprocess.Popen(
            full_cmd,
            cwd=str(cwd),
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        while True:
            rc = proc.poll()
            if rc is not None:
                return rc, False
            if cancel_event is not None and cancel_event.is_set():
                terminate_process_tree(proc)
                return -1, True
            time.sleep(0.25)


def check_abaqus_job_ok(
    work_dir: Path,
    job_name: str,
    *,
    expected_steps: int = 1,
    expected_increments: int | None = None,
) -> tuple[bool, str]:
    """综合检查 .sta/.msg/.dat/.log 与 .odb，确认 Abaqus 任务真正完整收敛。"""
    texts: list[str] = []
    for ext in (".sta", ".msg", ".dat", ".log"):
        path = work_dir / f"{job_name}{ext}"
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    upper = "\n".join(texts).upper()

    for marker in FAIL_MARKERS:
        if marker in upper:
            return False, f"Abaqus 报错：{marker}"
    if not any(marker in upper for marker in SUCCESS_MARKERS):
        return False, "未在求解日志中检测到完成标志"

    sta_path = work_dir / f"{job_name}.sta"
    max_step = 0
    max_inc = 0
    if sta_path.is_file():
        for line in sta_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.match(r"^\s*(\d+)\s+(\d+)\s+", line)
            if match:
                step_num, inc_num = int(match.group(1)), int(match.group(2))
                max_step = max(max_step, step_num)
                max_inc = max(max_inc, inc_num)
    if max_step < expected_steps:
        return False, f"求解 Step 数不足（实际 {max_step}，期望 {expected_steps}）"
    if expected_increments is not None and max_inc < expected_increments:
        return False, f"增量步数不足（实际 {max_inc}，期望 {expected_increments}）"

    odb_path = work_dir / f"{job_name}.odb"
    if not odb_path.is_file() or odb_path.stat().st_size == 0:
        return False, "未生成有效的 .odb 文件"
    return True, ""
