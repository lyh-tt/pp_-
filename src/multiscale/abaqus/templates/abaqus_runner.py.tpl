# -*- coding: utf-8 -*-
"""Standalone zero-dependency Abaqus batch homogenization runner.

Supports Windows and Linux servers using only the Python 3 standard library.
Each task runs inside an isolated temporary directory and exports only the final
<output_dir>/<model_name>/<job_name>.rpt file without leaving intermediate ODB/INP clutter.
"""
import argparse
import csv
import math
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

# ===== Configuration =====
MODELS_DIR = $models_dir
FIBER_CSV = $fiber_csv
OUTPUT_DIR = $output_dir
ABAQUS_COMMAND = $abaqus_command
CPUS = $cpus
MAX_PARALLEL = $max_parallel
MEMORY_MB = $memory_mb
RETRIES = $retries
RESUME_EXISTING = $resume_existing
DEFAULT_POISSON = $default_poisson
SCRATCH_DIR = $scratch_dir
# =========================

POST_TEMPLATE = Template($post_template)

FAIL_KEYS = (
    "THE ANALYSIS HAS NOT BEEN COMPLETED",
    "TIME INCREMENT REQUIRED IS LESS THAN THE MINIMUM SPECIFIED",
    "TOO MANY ATTEMPTS MADE FOR THIS INCREMENT",
    "ABAQUS/STANDARD ANALYSIS EXITED WITH AN ERROR",
    "ABAQUS ERROR",
)
SUCCESS_KEYS = (
    "THE ANALYSIS HAS COMPLETED SUCCESSFULLY",
    "THE ANALYSIS HAS BEEN COMPLETED",
)
REQUIRED_CSV_COLUMNS = (
    "E1MPa",
    "CTE/e-5",
    "E1",
    "E2",
    "E3",
    "V12",
    "V13",
    "V23",
    "G12",
    "G13",
    "G23",
    "CTE_X",
    "CTE_Y",
    "CTE_Z",
)

_LOG_LOCK = threading.Lock()


def log(message):
    with _LOG_LOCK:
        print(message, flush=True)


def natural_sort_key(text):
    parts = re.split(r"(\d+)", str(text))
    key = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def format_token(value):
    cleaned = round(float(value), 10)
    if cleaned == 0:
        cleaned = 0.0
    text = ("%.10f" % cleaned).rstrip("0").rstrip(".")
    return text.replace(".", "p")


def read_text_fallback(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def discover_models(root_dir):
    root = Path(root_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError("TexGen models folder not found: " + str(root))

    def inspect_dir(folder, model_name):
        inps = sorted(folder.glob("*.inp"), key=lambda p: natural_sort_key(p.name))
        oris = sorted(folder.glob("*.ori"), key=lambda p: natural_sort_key(p.name))
        if not inps or not oris:
            return None
        elds = sorted(folder.glob("*.eld"), key=lambda p: natural_sort_key(p.name))
        return {
            "name": model_name,
            "inp": inps[0],
            "ori": oris[0],
            "eld": elds[0] if elds else None,
        }

    direct = inspect_dir(root, root.name)
    if direct is not None:
        return [direct]

    models = []
    subdirs = sorted(
        (d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=lambda p: natural_sort_key(p.name),
    )
    for sub in subdirs:
        found = inspect_dir(sub, sub.name)
        if found is not None:
            models.append(found)
            continue
        for child in sub.iterdir():
            if child.is_dir() and child.name.lower() == "texgen":
                nested = inspect_dir(child, sub.name)
                if nested is not None:
                    models.append(nested)
                    break

    if not models:
        raise ValueError("No valid TexGen (.inp + .ori) models found under: " + str(root))
    return models


def read_fiber_bundle_csv(csv_path, default_poisson=0.38):
    content = read_text_fallback(csv_path)
    rows = [
        [cell.strip() for cell in line]
        for line in csv.reader(content.splitlines())
        if any(cell.strip() for cell in line)
    ]
    if len(rows) < 2:
        raise ValueError("Fiber CSV has no data rows: " + str(csv_path))

    headers = [col.strip() for col in rows[0]]
    col_idx = {name: idx for idx, name in enumerate(headers)}
    missing = [col for col in REQUIRED_CSV_COLUMNS if col not in col_idx]
    if missing:
        raise ValueError("Fiber CSV missing columns: " + ", ".join(missing))

    poisson_idx = col_idx.get("Poisson")
    parsed = []
    for row_num, cells in enumerate(rows[1:], start=1):
        try:
            item = {col: float(cells[col_idx[col]]) for col in REQUIRED_CSV_COLUMNS}
            if poisson_idx is not None and poisson_idx < len(cells) and cells[poisson_idx]:
                item["Poisson"] = float(cells[poisson_idx])
            else:
                item["Poisson"] = float(default_poisson)
        except (IndexError, ValueError) as exc:
            raise ValueError("Invalid numeric value in CSV row %d: %s" % (row_num, exc))
        for val in item.values():
            if not math.isfinite(val):
                raise ValueError("Non-finite value in CSV row %d" % row_num)
        e_gpa = round(item["E1MPa"] / 1000.0, 10)
        item["rpt_stem"] = "pp%s_%se_5" % (format_token(e_gpa), format_token(item["CTE/e-5"]))
        parsed.append(item)
    return parsed


def build_tasks(models, rows, output_dir):
    out_root = Path(output_dir).resolve()
    tasks = []
    for model in models:
        used = {}
        for row in rows:
            stem = row["rpt_stem"]
            count = used.get(stem, 0) + 1
            used[stem] = count
            job_name = stem if count == 1 else "%s_%d" % (stem, count)
            tasks.append({
                "model": model,
                "row": row,
                "job_name": job_name,
                "output_rpt": out_root / model["name"] / (job_name + ".rpt"),
            })
    return tasks


def replace_aux_tokens(line, aux_map):
    result = line
    for old_name, new_name in aux_map.items():
        pattern = re.compile(
            r"(?<![A-Za-z0-9_\-.])" + re.escape(old_name) + r"(?![A-Za-z0-9_\-.])"
        )
        result = pattern.sub(new_name, result)
    return result


def skip_data_lines(lines, idx):
    while idx < len(lines) and not lines[idx].lstrip().startswith("*"):
        idx += 1
    return idx


def patch_inp(template_inp, output_inp, row, aux_map):
    lines = Path(template_inp).read_text(encoding="utf-8", errors="ignore").splitlines(True)
    out = []
    current_mat = ""
    replaced = {
        "mat0_elastic": False,
        "mat0_expansion": False,
        "mat1_elastic": False,
        "mat1_expansion": False,
    }
    e_pa = row["E1MPa"] * 1e6
    cte_resin = row["CTE/e-5"] * 1e-5
    elastic_vals = (
        row["E1"] * 1e6,
        row["E2"] * 1e6,
        row["E3"] * 1e6,
        row["V12"],
        row["V13"],
        row["V23"],
        row["G12"] * 1e6,
        row["G13"] * 1e6,
        row["G23"] * 1e6,
    )

    idx = 0
    while idx < len(lines):
        line = replace_aux_tokens(lines[idx], aux_map)
        stripped = line.strip().lower()
        compact = stripped.replace(" ", "")

        if compact.startswith("*material") and "name=" in compact:
            match = re.search(r"name=([^,\s]+)", compact)
            current_mat = match.group(1) if match else ""

        if stripped.startswith("*elastic") and "type=engineeringconstants" in compact:
            out.append(line)
            out.append(
                " %.6E, %.6E, %.6E, %.6E, %.6E, %.6E, %.6E, %.6E\n" % elastic_vals[:8]
            )
            out.append(" %.6E\n" % elastic_vals[8])
            idx = skip_data_lines(lines, idx + 1)
            replaced["mat1_elastic"] = True
            continue

        if stripped.startswith("*elastic") and "type=" not in compact and current_mat in ("", "mat0"):
            out.append(line)
            out.append(" %.6E, %g\n" % (e_pa, row["Poisson"]))
            idx = skip_data_lines(lines, idx + 1)
            replaced["mat0_elastic"] = True
            continue

        if stripped.startswith("*expansion") and "type=ortho" in compact:
            out.append(line)
            out.append(" %.6E, %.6E, %.6E\n" % (row["CTE_X"], row["CTE_Y"], row["CTE_Z"]))
            idx = skip_data_lines(lines, idx + 1)
            replaced["mat1_expansion"] = True
            continue

        if stripped.startswith("*expansion") and "type=" not in compact and current_mat in ("", "mat0"):
            out.append(line)
            out.append(" %.6E\n" % cte_resin)
            idx = skip_data_lines(lines, idx + 1)
            replaced["mat0_expansion"] = True
            continue

        out.append(line)
        idx += 1

    missing = [k for k, ok in replaced.items() if not ok]
    if missing:
        raise ValueError("INP missing material blocks (%s): %s" % (", ".join(missing), template_inp))

    Path(output_inp).write_text("".join(out), encoding="utf-8", newline="\n")


def count_inp_steps(inp_path):
    text = Path(inp_path).read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r"^\s*\*Step\b", text, flags=re.IGNORECASE | re.MULTILINE))


def check_abaqus_success(work_dir, job_name, expected_steps):
    combined = []
    for ext in (".sta", ".msg", ".dat", ".log"):
        path = Path(work_dir) / (job_name + ext)
        if path.is_file():
            combined.append(path.read_text(encoding="utf-8", errors="ignore"))
    upper = "\n".join(combined).upper()

    for key in FAIL_KEYS:
        if key in upper:
            return False, "Abaqus failure marker: " + key
    if not any(key in upper for key in SUCCESS_KEYS):
        return False, "Abaqus completion marker not found"

    sta_path = Path(work_dir) / (job_name + ".sta")
    max_step = 0
    if sta_path.is_file():
        for line in sta_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.match(r"^\s*(\d+)\s+\d+\s+", line)
            if match:
                max_step = max(max_step, int(match.group(1)))
    if max_step < expected_steps:
        return False, "Step count %d < expected %d" % (max_step, expected_steps)

    odb_path = Path(work_dir) / (job_name + ".odb")
    if not odb_path.is_file() or odb_path.stat().st_size == 0:
        return False, "Missing or empty ODB"
    return True, ""


def run_command(cmd, cwd, log_path):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    full_cmd = (
        ["cmd", "/c", *cmd]
        if (os.name == "nt" and cmd[0].lower().endswith((".bat", ".cmd")))
        else cmd
    )
    with open(log_path, "w", encoding="utf-8", errors="ignore") as out:
        proc = subprocess.Popen(
            full_cmd,
            cwd=str(cwd),
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        return proc.wait()


def safe_ascii_temp_dir(prefix="abq-work-"):
    base = Path(tempfile.gettempdir())
    if str(base).isascii():
        return TemporaryDirectory(prefix=prefix)
    fallback = Path(os.environ.get("SystemDrive", "C:") + os.sep + "abq_tmp") if os.name == "nt" else Path("/tmp/abq_tmp")
    fallback.mkdir(parents=True, exist_ok=True)
    return TemporaryDirectory(dir=fallback, prefix=prefix)


def run_single_task(task, args):
    out_rpt = task["output_rpt"]
    label = "%s/%s" % (task["model"]["name"], task["job_name"])
    if args.resume and out_rpt.is_file() and out_rpt.stat().st_size > 0:
        log("[SKIP] %s (already exists)" % label)
        return "skipped", ""

    model = task["model"]
    job_name = task["job_name"]
    last_error = "Unknown failure"

    for attempt in range(1, args.retries + 2):
        log("[RUN %d/%d] %s" % (attempt, args.retries + 1, label))
        try:
            with safe_ascii_temp_dir(prefix="abq-work-") as tmp:
                work_dir = Path(tmp)
                aux_map = {}
                shutil.copy2(model["ori"], work_dir / (job_name + ".ori"))
                aux_map[model["ori"].name] = job_name + ".ori"
                if model["eld"] is not None:
                    shutil.copy2(model["eld"], work_dir / (job_name + ".eld"))
                    aux_map[model["eld"].name] = job_name + ".eld"

                inp_path = work_dir / (job_name + ".inp")
                patch_inp(model["inp"], inp_path, task["row"], aux_map)
                expected_steps = count_inp_steps(inp_path)

                scratch_root = Path(args.scratch_dir) if args.scratch_dir else (work_dir / "scratch")
                task_scratch = scratch_root / ("%s_%d" % (job_name, os.getpid()))
                task_scratch.mkdir(parents=True, exist_ok=True)
                try:
                    solve_cmd = [
                        args.abaqus,
                        "job=" + job_name,
                        "input=" + job_name + ".inp",
                        "cpus=" + str(args.cpus),
                    ]
                    if args.memory > 0:
                        solve_cmd.append("memory=%dmb" % args.memory)
                    solve_cmd.extend([
                        "scratch=" + str(task_scratch),
                        "ask_delete=OFF",
                        "interactive",
                    ])
                    rc = run_command(solve_cmd, work_dir, work_dir / "solve.log")
                    if rc != 0:
                        last_error = "Abaqus exited with code %d" % rc
                        continue

                    ok, reason = check_abaqus_success(work_dir, job_name, expected_steps)
                    if not ok:
                        last_error = reason
                        continue

                    post_script = work_dir / "extract_rpt.py"
                    post_script.write_text(
                        POST_TEMPLATE.substitute(model_name=repr(job_name)),
                        encoding="utf-8",
                    )
                    rc_post = run_command(
                        [args.abaqus, "cae", "noGUI=extract_rpt.py"],
                        work_dir,
                        work_dir / "post.log",
                    )
                    rpt_tmp = work_dir / (job_name + ".rpt")
                    if rc_post != 0 or not rpt_tmp.is_file() or rpt_tmp.stat().st_size == 0:
                        last_error = "RPT extraction failed (rc=%d)" % rc_post
                        continue
                    rpt_text = rpt_tmp.read_text(encoding="utf-8", errors="ignore")
                    if "Effective material properties" not in rpt_text or "CTE_1" not in rpt_text:
                        last_error = "RPT missing expected property fields"
                        continue

                    out_rpt.parent.mkdir(parents=True, exist_ok=True)
                    with TemporaryDirectory(dir=str(out_rpt.parent), prefix=".rpt-") as stage:
                        staged = Path(stage) / out_rpt.name
                        shutil.copy2(rpt_tmp, staged)
                        staged.replace(out_rpt)
                    log("[DONE] %s -> %s" % (label, out_rpt))
                    return "succeeded", ""
                finally:
                    if args.scratch_dir and task_scratch.exists():
                        shutil.rmtree(task_scratch, ignore_errors=True)
        except Exception as exc:
            last_error = str(exc)

    log("[FAIL] %s: %s" % (label, last_error))
    return "failed", last_error


def main():
    parser = argparse.ArgumentParser(description="Abaqus batch homogenization runner")
    parser.add_argument("--models-dir", default=MODELS_DIR)
    parser.add_argument("--fiber-csv", default=FIBER_CSV)
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--abaqus", default=ABAQUS_COMMAND)
    parser.add_argument("--cpus", type=int, default=CPUS)
    parser.add_argument("--parallel", type=int, default=MAX_PARALLEL)
    parser.add_argument("--memory", type=int, default=MEMORY_MB, help="Per-job memory limit in MB (0 = Abaqus default)")
    parser.add_argument("--retries", type=int, default=RETRIES)
    parser.add_argument("--no-resume", dest="resume", action="store_false", default=RESUME_EXISTING)
    parser.add_argument("--default-poisson", type=float, default=DEFAULT_POISSON)
    parser.add_argument("--scratch-dir", default=SCRATCH_DIR)
    args = parser.parse_args()

    models = discover_models(args.models_dir)
    rows = read_fiber_bundle_csv(args.fiber_csv, default_poisson=args.default_poisson)
    tasks = build_tasks(models, rows, args.output_dir)
    log("Models: %d | Materials: %d | Total tasks: %d | Parallel: %d" % (
        len(models), len(rows), len(tasks), args.parallel
    ))

    counts = {"succeeded": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = [pool.submit(run_single_task, task, args) for task in tasks]
        for future in as_completed(futures):
            status, _err = future.result()
            counts[status] += 1

    log("Finished: succeeded=%d, skipped=%d, failed=%d" % (
        counts["succeeded"], counts["skipped"], counts["failed"]
    ))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
