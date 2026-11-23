# -*- coding: utf-8 -*-
"""第 1 步 Fiber 微观 RVE 默认 7 方向并行求解脚本（纯 Python 3 标准库，可直接拷到服务器运行）。

核心流程：
1. 根据当前机器总物理内存自动按 <= 90% 总额均分给并发任务，避免手工配置单任务内存；
2. 默认 7 个方向（E11/E22/E33/G12/G13/G23/CTE）在纯英文隔离临时目录同时提交；
3. 全部收敛后仅启动 1 次 CAE 直接提取 7 个 ODB 并写出 16 列 *_fiber_bundle.csv；
4. 运行结束自动清理全部 .odb/.dat/.dll 等过程文件（可用 --keep-odb 保留）。
"""
import argparse
import csv
import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory

# ===== 默认配置（默认 7 路全并发，总内存上限 <= 90%）=====
CELL_SIZE_MM = $cell_size_mm
ABAQUS_COMMAND = $abaqus_command
CPUS = $cpus
MAX_PARALLEL = $max_parallel
TOTAL_MEMORY_RATIO = $total_memory_ratio
RETRIES = $retries
# =======================================================

POST_TEMPLATE = Template($post_template)

# 7 个彼此独立的加载方向与其对应的 Fortran 子程序
FIBER_JOBS = (
    ("E11", "job-E11", "Visco-Prepreg-Hole.f"),
    ("E22", "job-E22", "Visco-Prepreg-Hole.f"),
    ("E33", "job-E33", "Visco-Prepreg-Hole.f"),
    ("G12", "job-G12", "Visco-Prepreg-Hole.f"),
    ("G13", "job-G13", "Visco-Prepreg-Hole.f"),
    ("G23", "job-G23", "Visco-Prepreg-Hole.f"),
    ("CTE", "job-CTE", "Visco-Prepreg-Hole-CTE.f"),
)
FAIL_KEYS = (
    "THE ANALYSIS HAS NOT BEEN COMPLETED",
    "TOO MANY ATTEMPTS MADE FOR THIS INCREMENT",
    "ABAQUS/STANDARD ANALYSIS EXITED WITH AN ERROR",
    "ABAQUS ERROR",
)
SUCCESS_KEYS = (
    "THE ANALYSIS HAS COMPLETED SUCCESSFULLY",
    "THE ANALYSIS HAS BEEN COMPLETED",
)

_LOG_LOCK = threading.Lock()


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


def log(msg):
    with _LOG_LOCK:
        print(msg, flush=True)


def detect_abaqus():
    for name in ("abq2026", "abq2024", "abq2022", "abaqus"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def detect_total_memory_mb():
    """读取总物理内存（MB），用于自动限制全部并发任务总占用不超过 90%。"""
    try:
        if os.name == "nt":
            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys // (1024 * 1024))
            return None
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                return int((pages * page_size) // (1024 * 1024))
    except Exception:
        pass
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        m = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo.read_text(errors="ignore"), re.MULTILINE)
        if m:
            return int(m.group(1)) // 1024
    return None


def auto_per_job_memory_mb(parallel_jobs, total_ratio=0.90):
    total_mb = detect_total_memory_mb()
    if not total_mb or parallel_jobs < 1:
        return None
    per_job = int(total_mb * total_ratio) // parallel_jobs
    return per_job if per_job >= 64 else None


def safe_ascii_temp_dir(prefix="abq-fiber-"):
    """确保 Abaqus 运行目录为纯 ASCII 路径，规避中文路径崩溃。"""
    base = Path(tempfile.gettempdir())
    if str(base).isascii():
        return TemporaryDirectory(prefix=prefix)
    fallback = Path(os.environ.get("SystemDrive", "C:") + os.sep + "abq_tmp") if os.name == "nt" else Path("/tmp/abq_tmp")
    fallback.mkdir(parents=True, exist_ok=True)
    return TemporaryDirectory(dir=fallback, prefix=prefix)


def run_cmd(cmd, cwd, log_path):
    full_cmd = ["cmd", "/c", *cmd] if (os.name == "nt" and cmd[0].lower().endswith((".bat", ".cmd"))) else cmd
    with log_path.open("w", encoding="utf-8", errors="ignore") as out:
        proc = subprocess.run(full_cmd, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT)
    return proc.returncode


def check_job_ok(work_dir, job_name):
    texts = [
        (work_dir / (job_name + ext)).read_text(encoding="utf-8", errors="ignore")
        for ext in (".sta", ".msg", ".dat", ".log")
        if (work_dir / (job_name + ext)).is_file()
    ]
    upper = "\n".join(texts).upper()
    for key in FAIL_KEYS:
        if key in upper:
            return False, "Abaqus error: " + key
    if not any(key in upper for key in SUCCESS_KEYS):
        return False, "Missing completion marker"
    odb = work_dir / (job_name + ".odb")
    if not odb.is_file() or odb.stat().st_size == 0:
        return False, "Missing ODB: " + odb.name
    return True, ""


def submit_one_direction(case_dir, tmp_root, abaqus_cmd, direction, job_name, sub_name, per_job_mem, args):
    """在独立临时子目录中提交单个方向（总并发内存自动控制 <= 90%）。"""
    job_dir = tmp_root / job_name
    last_err = "Unknown error"
    for attempt in range(1, args.retries + 2):
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        scratch = job_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(case_dir / (job_name + ".inp"), job_dir / (job_name + ".inp"))
        shutil.copy2(case_dir / sub_name, job_dir / sub_name)

        cmd = [
            abaqus_cmd,
            "job=" + job_name,
            "input=" + job_name + ".inp",
            "user=" + sub_name,
            "cpus=" + str(args.cpus),
        ]
        if per_job_mem:
            cmd.append("memory=%dmb" % per_job_mem)
        cmd.extend(["scratch=" + str(scratch), "ask_delete=OFF", "interactive"])

        log("[Submit %d/%d] %s" % (attempt, args.retries + 1, job_name))
        rc = run_cmd(cmd, job_dir, job_dir / "solve.log")
        if rc == 0:
            ok, reason = check_job_ok(job_dir, job_name)
            if ok:
                log("[Done] " + job_name)
                return direction, job_name + "/" + job_name + ".odb"
            last_err = reason
        else:
            last_err = "Exit code %d" % rc
        log("[Retry] %s -> %s" % (job_name, last_err))

    raise RuntimeError(job_name + " failed: " + last_err)


def load_resin_rows(case_dir):
    resin_csv = case_dir / "resin_inputs.csv"
    if not resin_csv.is_file():
        raise FileNotFoundError("Missing resin_inputs.csv in " + str(case_dir))
    rows = []
    with resin_csv.open("r", encoding="utf-8-sig", newline="") as f:
        for item in csv.DictReader(f):
            rows.append((
                item.get("Resin Name", "").strip(),
                item["E (MPa)"].strip(),
                item["CTE (1e-5/K)"].strip(),
                item["Poisson"].strip(),
            ))
    if not rows:
        raise ValueError("resin_inputs.csv is empty in " + str(case_dir))
    return rows


def main():
    parser = argparse.ArgumentParser(description="7-direction parallel Abaqus solver for Fiber RVE.")
    parser.add_argument("--case-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--abaqus", default=ABAQUS_COMMAND)
    parser.add_argument("--cpus", type=int, default=CPUS)
    parser.add_argument("--parallel", type=int, default=MAX_PARALLEL)
    parser.add_argument("--retries", type=int, default=RETRIES)
    parser.add_argument("--keep-odb", action="store_true")
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    resin_rows = load_resin_rows(case_dir)
    abaqus_cmd = (args.abaqus or "").strip() or detect_abaqus()
    if not abaqus_cmd:
        sys.exit("Abaqus command not found; pass --abaqus <path>.")

    active_parallel = min(max(1, args.parallel), len(FIBER_JOBS))
    per_job_mem = auto_per_job_memory_mb(active_parallel, TOTAL_MEMORY_RATIO)
    if per_job_mem:
        log("[Memory] %d parallel jobs -> %d MB/job (total <= %d%% RAM)" % (
            active_parallel, per_job_mem, int(TOTAL_MEMORY_RATIO * 100)
        ))

    bundle_name = case_dir.name + "_fiber_bundle.csv"
    with safe_ascii_temp_dir(prefix="abq-fiber-") as tmp:
        tmp_root = Path(tmp)
        odb_map = {}

        # 第一阶段：7 个方向在各自纯英文隔离目录同时并发求解
        with ThreadPoolExecutor(max_workers=active_parallel) as pool:
            futures = {
                pool.submit(
                    submit_one_direction,
                    case_dir, tmp_root, abaqus_cmd, key, job, sub, per_job_mem, args
                ): job
                for key, job, sub in FIBER_JOBS
            }
            for fut in as_completed(futures):
                key, rel_odb = fut.result()
                odb_map[key] = rel_odb

        # 第二阶段：仅启动 1 次 CAE 统一提取所有 7 个 ODB 并直接写出 *_fiber_bundle.csv
        post_py = tmp_root / "extract_fiber.py"
        post_py.write_text(
            POST_TEMPLATE.substitute(
                cell_size_mm=repr(CELL_SIZE_MM),
                odb_map=repr(odb_map),
                resin_rows=repr(resin_rows),
                output_csv=repr(bundle_name),
            ),
            encoding="utf-8",
        )
        log("[Post] Extracting direct bundle CSV: " + bundle_name + " ...")
        rc = run_cmd([abaqus_cmd, "cae", "noGUI=" + post_py.name], tmp_root, tmp_root / "post.log")
        bundle_tmp = tmp_root / bundle_name
        if rc != 0 or not bundle_tmp.is_file():
            sys.exit("Post-processing failed (see post.log), exit code: %d" % rc)

        bundle_dst = case_dir / bundle_name
        shutil.copy2(bundle_tmp, bundle_dst)
        if args.keep_odb:
            for rel_odb in odb_map.values():
                shutil.copy2(tmp_root / rel_odb, case_dir / Path(rel_odb).name)

    log("Completed bundle CSV: " + str(bundle_dst))


if __name__ == "__main__":
    main()
