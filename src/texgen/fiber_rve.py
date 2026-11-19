from __future__ import annotations

import ast
import csv
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from .resources import app_root
from .workflow import clean_float

STIFFNESS_NAMES = ("D11", "D12", "D13", "D22", "D23", "D33", "D44", "D55", "D66")
CTE_NAMES = ("CTE_X", "CTE_Y", "CTE_Z")
BATCH_SCRIPT_NAME = "EqPropFEA_BatchScripts_Viscoelastic-fiber.py"
UMAT_SUBROUTINE = "Visco-Prepreg-Hole.f"
UMAT_CTE_SUBROUTINE = "Visco-Prepreg-Hole-CTE.f"
INP_NAMES = (
    "job-CTE.inp",
    "job-E11.inp",
    "job-E22.inp",
    "job-E33.inp",
    "job-G12.inp",
    "job-G13.inp",
    "job-G23.inp",
)
REQUIRED_TEMPLATE_FILES = (
    BATCH_SCRIPT_NAME,
    UMAT_SUBROUTINE,
    UMAT_CTE_SUBROUTINE,
    *INP_NAMES,
)
ALLOWED_COPY_SUFFIXES = {".inp", ".f", ".py"}


@dataclass(frozen=True)
class FiberTemplate:
    group: str
    name: str
    path: Path
    cell_size_um: float | None
    inp_count: int


@dataclass(frozen=True)
class ResinProperty:
    e_mpa: float
    cte_e5: float
    poisson: float
    name: str = ""

    def __post_init__(self) -> None:
        if not isfinite(self.e_mpa) or self.e_mpa <= 0:
            raise ValueError("树脂弹性模量 E 必须为大于 0 的有限数值（MPa）。")
        if not isfinite(self.cte_e5):
            raise ValueError("树脂热膨胀系数 CTE 必须为有限数值（10⁻⁵/K）。")
        if not isfinite(self.poisson) or not (-1.0 < self.poisson < 0.5):
            raise ValueError("树脂泊松比 ν 必须介于 -1 和 0.5 之间。")
        clean_name = self.name.strip()
        if clean_name != self.name:
            object.__setattr__(self, "name", clean_name)


@dataclass(frozen=True)
class FiberGenerationResult:
    template: FiberTemplate
    output_dir: Path
    resin_count: int
    updated_inp_count: int
    csv_path: Path


SAMPLE_RESIN_ROWS = (
    ResinProperty(4604.51, 6.04967, 0.38, "S8NE"),
    ResinProperty(4030.56, 5.27133, 0.38, "H350"),
)


def default_fiber_templates_root() -> Path:
    return app_root() / "templates" / "fiber"


def default_sample_resin_csv() -> Path:
    return app_root() / "examples" / "resin_inputs_example.csv"


def write_sample_resin_csv(path: Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "Resin Name": resin.name,
            "E (MPa)": resin.e_mpa,
            "CTE (1e-5/K)": resin.cte_e5,
            "Poisson": resin.poisson,
        }
        for resin in SAMPLE_RESIN_ROWS
    ]
    pd.DataFrame(rows).to_csv(target, index=False, encoding="utf-8-sig")
    return target


def _natural_group_key(text: str) -> tuple[tuple[int, int | str], ...]:
    parts = re.split(r"(\d+)", text)
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def _is_valid_template_dir(directory: Path) -> bool:
    return directory.is_dir() and all(
        (directory / filename).is_file() for filename in REQUIRED_TEMPLATE_FILES
    )


def _parse_cell_size_um(template_dir: Path) -> float | None:
    script_path = template_dir / BATCH_SCRIPT_NAME
    if not script_path.is_file():
        return None
    text = script_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^\s*L\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eEdD][-+]?\d+)?)", text, re.MULTILINE)
    if not match:
        return None
    value_mm = float(match.group(1).replace("D", "E").replace("d", "e"))
    return clean_float(value_mm * 1000.0)


def discover_fiber_templates(templates_root: Path) -> list[FiberTemplate]:
    root = Path(templates_root)
    if not root.is_dir():
        return []

    found: list[FiberTemplate] = []
    group_dirs = sorted(
        (entry for entry in root.iterdir() if entry.is_dir()),
        key=lambda entry: _natural_group_key(entry.name),
    )
    for group_dir in group_dirs:
        if _is_valid_template_dir(group_dir):
            candidate_dirs = [group_dir]
        else:
            candidate_dirs = sorted(
                (
                    sub
                    for sub in group_dir.iterdir()
                    if sub.is_dir() and _is_valid_template_dir(sub)
                ),
                key=lambda entry: _natural_group_key(entry.name),
            )

        for template_dir in candidate_dirs:
            inp_count = len(list(template_dir.glob("job-*.inp")))
            found.append(
                FiberTemplate(
                    group=group_dir.name,
                    name=template_dir.name,
                    path=template_dir.resolve(),
                    cell_size_um=_parse_cell_size_um(template_dir),
                    inp_count=inp_count,
                )
            )
    return found


def resin_stiffness_row(resin: ResinProperty) -> dict[str, float]:
    e_mpa = resin.e_mpa
    poisson = resin.poisson
    g = e_mpa / (2.0 * (1.0 + poisson))
    coupling = poisson * e_mpa / ((1.0 + poisson) * (1.0 - 2.0 * poisson))
    d_diag = coupling + 2.0 * g
    cte_value = resin.cte_e5 * 1e-5

    return {
        "D11": d_diag,
        "D12": coupling,
        "D13": coupling,
        "D22": d_diag,
        "D23": coupling,
        "D33": d_diag,
        "D44": g,
        "D55": g,
        "D66": g,
        "CTE_X": cte_value,
        "CTE_Y": cte_value,
        "CTE_Z": cte_value,
    }


def _resin_from_sequence(item: object, row_index: int) -> ResinProperty:
    if not isinstance(item, (list, tuple)) or len(item) not in (3, 4):
        raise ValueError(f"第 {row_index} 条树脂参数格式错误，应为 3 项 (E, CTE, ν) 或 4 项 (名称, E, CTE, ν)。")

    if len(item) == 3:
        name = ""
        raw_e, raw_cte, raw_v = item
    else:
        raw_name, raw_e, raw_cte, raw_v = item
        name = "" if raw_name is None else str(raw_name).strip()

    try:
        e_mpa = float(raw_e)
        cte_e5 = float(raw_cte)
        poisson = float(raw_v)
    except (TypeError, ValueError) as error:
        raise ValueError(f"第 {row_index} 条树脂参数的 E / CTE / 泊松比必须为数字。") from error

    try:
        return ResinProperty(e_mpa=e_mpa, cte_e5=cte_e5, poisson=poisson, name=name)
    except ValueError as error:
        raise ValueError(f"第 {row_index} 条树脂参数无效：{error}") from error


def _try_parse_python_tuples(stripped: str) -> list[ResinProperty] | None:
    if "(" not in stripped and "[" not in stripped:
        return None
    candidates = [stripped]
    if not stripped.startswith("["):
        candidates.append(f"[\n{stripped}\n]")
    for source in candidates:
        try:
            parsed = ast.literal_eval(source)
        except (SyntaxError, ValueError):
            continue
        if isinstance(parsed, tuple) and len(parsed) in (3, 4) and not any(
            isinstance(elem, (list, tuple)) for elem in parsed
        ):
            return [_resin_from_sequence(parsed, 1)]
        if isinstance(parsed, (list, tuple)):
            return [
                _resin_from_sequence(item, index)
                for index, item in enumerate(parsed, start=1)
            ]
    return None


def _split_line_tokens(line: str) -> list[str]:
    clean = line.strip().rstrip(",").strip()
    if clean.startswith("(") and clean.endswith(")"):
        clean = clean[1:-1].strip()
    if "," in clean:
        tokens = [part.strip().strip("'\"") for part in clean.split(",")]
    elif "\t" in clean:
        tokens = [part.strip().strip("'\"") for part in clean.split("\t")]
    else:
        tokens = [part.strip().strip("'\"") for part in clean.split()]
    return [token for token in tokens if token != ""]


def _looks_like_header(tokens: list[str]) -> bool:
    if len(tokens) < 3:
        return True
    numeric = 0
    for token in tokens:
        try:
            float(token)
            numeric += 1
        except ValueError:
            pass
    return numeric == 0


def parse_resin_text(text: str) -> list[ResinProperty]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("请输入或粘贴至少一组树脂性能参数。")

    python_parsed = _try_parse_python_tuples(stripped)
    if python_parsed is not None:
        if not python_parsed:
            raise ValueError("未解析到有效的树脂性能参数。")
        return python_parsed

    results: list[ResinProperty] = []
    lines = [
        line.strip()
        for line in stripped.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line_no, line in enumerate(lines, start=1):
        tokens = _split_line_tokens(line)
        if line_no == 1 and _looks_like_header(tokens):
            continue
        if len(tokens) == 3:
            results.append(_resin_from_sequence(tokens, len(results) + 1))
        elif len(tokens) == 4:
            results.append(_resin_from_sequence(tokens, len(results) + 1))
        else:
            raise ValueError(
                f"第 {line_no} 行有 {len(tokens)} 列，需要 3 列（E, CTE, ν）或 4 列（牌号, E, CTE, ν）。"
            )

    if not results:
        raise ValueError("未解析到有效的树脂性能参数。")
    return results


def _read_text_with_fallback(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别文件编码：{path.name}")


def _normalize_header(column: str) -> str:
    return re.sub(r"[\s_\-()（）/]+", "", column.strip().lower())


def read_resin_csv(path: Path) -> list[ResinProperty]:
    content = _read_text_with_fallback(Path(path))
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(content.splitlines())
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise ValueError("CSV 文件中没有数据行。")

    header_norm = [_normalize_header(col) for col in rows[0]]
    e_aliases = {"e", "empa", "e1mpa", "modulus", "模量", "模量empa", "弹性模量", "树脂模量"}
    cte_aliases = {"cte", "ctee5", "cte1e5k", "ctek", "热膨胀系数"}
    v_aliases = {"poisson", "nu", "v", "ν", "泊松比", "泊松比ν", "树脂ν", "poissonratio"}
    name_aliases = {"name", "resin", "resinname", "树脂牌号", "牌号", "名称", "树脂"}

    def _find_col(aliases: set[str], prefix_fallback: tuple[str, ...] = ()) -> int | None:
        for idx, norm in enumerate(header_norm):
            if norm in aliases:
                return idx
        for idx, norm in enumerate(header_norm):
            if any(norm.startswith(prefix) for prefix in prefix_fallback):
                return idx
        return None

    e_idx = _find_col(e_aliases, ("模量e",))
    cte_idx = _find_col(cte_aliases, ("cte",))
    v_idx = _find_col(v_aliases, ("poisson", "泊松比"))
    name_idx = _find_col(name_aliases)

    if e_idx is not None and cte_idx is not None and v_idx is not None:
        data_rows = rows[1:]
        if not data_rows:
            raise ValueError("CSV 文件仅有表头，没有数据行。")
        results: list[ResinProperty] = []
        for idx, row in enumerate(data_rows, start=1):
            needed = max(e_idx, cte_idx, v_idx, name_idx if name_idx is not None else 0) + 1
            if len(row) < needed:
                raise ValueError(f"CSV 第 {idx} 行列数不足。")
            name = row[name_idx] if name_idx is not None else ""
            results.append(_resin_from_sequence((name, row[e_idx], row[cte_idx], row[v_idx]), idx))
        return results

    # Fallback: headerless CSV (or unrecognized header handled by parse_resin_text).
    return parse_resin_text(content)


def _fortran_number(value: float) -> str:
    return f"{value:.10e}"


def _build_block(name: str, values: list[float]) -> str:
    lines = [f"C --- {name} ---"]
    for index, value in enumerate(values, start=1):
        lines.append(f"      {name}({index}) = {_fortran_number(value)}")
    return "\n".join(lines)


def _replace_named_block(text: str, name: str, values: list[float]) -> str:
    pattern = (
        rf"C --- {re.escape(name)} ---\n"
        rf"(?:\s*{re.escape(name)}\(\d+\)\s*=\s*[-+0-9.eEdD]+\s*\n?)+"
    )
    replacement = _build_block(name, values) + "\n"
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise ValueError(f"子程序模板中未找到数据块：C --- {name} ---")
    return updated


def _resize_subroutine_arrays(text: str, array_size: int, *, include_cte: bool) -> str:
    stiffness_decl = "\n".join(
        [
            f"      DOUBLE PRECISION D11({array_size}),D12({array_size}),D13({array_size}),",
            f"     + D22({array_size}),D23({array_size}),D33({array_size}),D44({array_size}),",
            f"     + D55({array_size}),D66({array_size}),C_0(NTENS,NTENS),",
            "     + C_INF(NTENS,NTENS),C_DEF(NTENS,NTENS),STREND(NTENS)",
        ]
    )
    stiffness_pattern = r"(?ms)^\s*DOUBLE PRECISION\s+D11\(\d+\).*?STREND\(NTENS\)\s*$"
    text, stiffness_count = re.subn(stiffness_pattern, stiffness_decl, text, count=1)
    if stiffness_count != 1:
        raise ValueError("子程序模板中未找到刚度数组声明（DOUBLE PRECISION D11...）。")

    if include_cte:
        cte_decl = "\n".join(
            [
                f"      DOUBLE PRECISION CTE_X({array_size}),CTE_Y({array_size}),",
                f"     + CTE_Z({array_size})",
            ]
        )
        cte_pattern = r"(?ms)^\s*DOUBLE PRECISION\s+CTE_X\(\d+\).*?CTE_Z\(\d+\)\s*$"
        text, cte_count = re.subn(cte_pattern, cte_decl, text, count=1)
        if cte_count != 1:
            raise ValueError("子程序模板中未找到 CTE 数组声明（DOUBLE PRECISION CTE_X...）。")

    return text


def write_resin_values_to_subroutine(
    path: Path, rows: list[dict[str, float]], *, include_cte: bool
) -> None:
    if not rows:
        raise ValueError("树脂参数行不能为空。")
    text = path.read_text(encoding="utf-8")
    array_size = max(100, len(rows))
    text = _resize_subroutine_arrays(text, array_size, include_cte=include_cte)

    for name in STIFFNESS_NAMES:
        text = _replace_named_block(text, name, [row[name] for row in rows])
    if include_cte:
        for name in CTE_NAMES:
            text = _replace_named_block(text, name, [row[name] for row in rows])

    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def update_inp_step_count(path: Path, data_count: int) -> None:
    text = path.read_text(encoding="utf-8")
    count_text = str(data_count)

    amplitude_pattern = (
        r"(\*Amplitude,\s*name=Amp-1\s*\n"
        r"\s*0\.,\s*1\.,\s*)[-+0-9.eEdD]+(\.,\s*1\.)"
    )
    text, amplitude_count = re.subn(amplitude_pattern, rf"\g<1>{count_text}\g<2>", text, count=1)
    if amplitude_count != 1:
        raise ValueError(f"INP 文件中未找到 Amp-1 定义：{path.name}")

    static_pattern = r"(\*Static(?:,\s*direct)?\s*\n\s*1\.,\s*)[-+0-9.eEdD]+(\.,?)"
    text, static_count = re.subn(static_pattern, rf"\g<1>{count_text}\g<2>", text, count=1)
    if static_count != 1:
        raise ValueError(f"INP 文件中未找到 Static 步长定义：{path.name}")

    step_pattern = r"(?im)^(\*Step\b[^\r\n]*)$"
    max_inc = max(100, data_count + 20)

    def _update_step(match: re.Match[str]) -> str:
        line = match.group(1)
        if re.search(r"\binc\s*=", line, flags=re.IGNORECASE):
            return re.sub(
                r"\binc\s*=\s*\d+",
                f"inc={max_inc}",
                line,
                count=1,
                flags=re.IGNORECASE,
            )
        if data_count >= 100:
            return f"{line}, inc={max_inc}"
        return line

    text, step_count = re.subn(step_pattern, _update_step, text, count=1)
    if step_count != 1:
        raise ValueError(f"INP 文件中未找到 Step 定义：{path.name}")

    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def _plan_output_name(
    template: FiberTemplate,
    *,
    group_multi_selected: bool,
    batch_name: str,
    timestamp: str,
) -> str:
    prefix = f"fiber_{template.group}um"
    if group_multi_selected:
        prefix = f"{prefix}_{template.name}"
    suffix = batch_name if batch_name else timestamp
    return f"{prefix}_{suffix}"


def generate_fiber_cases(
    templates: list[FiberTemplate],
    resins: list[ResinProperty],
    output_parent: Path,
    batch_name: str = "",
) -> list[FiberGenerationResult]:
    if not templates:
        raise ValueError("请至少勾选一个 Fiber RVE 模板。")
    if not resins:
        raise ValueError("请填写至少一组树脂性能参数。")

    clean_batch = batch_name.strip()
    if clean_batch and not re.fullmatch(r"[A-Za-z0-9_.-]+", clean_batch):
        raise ValueError("批次名称仅支持英文字母、数字、点、横线和下划线。")

    parent = Path(output_parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    group_counts = Counter(template.group for template in templates)

    planned: list[tuple[FiberTemplate, Path]] = []
    seen_targets: set[str] = set()
    for template in templates:
        if not _is_valid_template_dir(template.path):
            raise FileNotFoundError(f"模板目录缺少必要文件或不存在：{template.path}")
        dir_name = _plan_output_name(
            template,
            group_multi_selected=group_counts[template.group] > 1,
            batch_name=clean_batch,
            timestamp=timestamp,
        )
        key = dir_name.casefold()
        if key in seen_targets:
            raise ValueError(f"生成目录名称重复：{dir_name}")
        seen_targets.add(key)
        destination = parent / dir_name
        if destination.exists():
            raise ValueError(
                f"目标目录已存在：{destination.name}。请更换批次名称或留空按时间自动命名。"
            )
        planned.append((template, destination))

    stiffness_rows = [resin_stiffness_row(resin) for resin in resins]
    csv_rows = [
        {
            "Row": index,
            "Resin Name": resin.name,
            "E (MPa)": resin.e_mpa,
            "CTE (1e-5/K)": resin.cte_e5,
            "Poisson": resin.poisson,
        }
        for index, resin in enumerate(resins, start=1)
    ]

    results: list[FiberGenerationResult] = []
    for template, destination in planned:
        with TemporaryDirectory(dir=parent, prefix=".pp-fiber-") as temporary:
            staged = Path(temporary) / destination.name
            staged.mkdir()
            for source in template.path.iterdir():
                if source.is_file() and source.suffix.lower() in ALLOWED_COPY_SUFFIXES:
                    shutil.copy2(source, staged / source.name)

            write_resin_values_to_subroutine(
                staged / UMAT_SUBROUTINE, stiffness_rows, include_cte=False
            )
            write_resin_values_to_subroutine(
                staged / UMAT_CTE_SUBROUTINE, stiffness_rows, include_cte=True
            )

            inp_files = sorted(staged.glob("job-*.inp"))
            for inp_path in inp_files:
                update_inp_step_count(inp_path, len(resins))

            csv_path = staged / "resin_inputs.csv"
            pd.DataFrame(csv_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
            staged.rename(destination)

        results.append(
            FiberGenerationResult(
                template=template,
                output_dir=destination,
                resin_count=len(resins),
                updated_inp_count=len(inp_files),
                csv_path=destination / "resin_inputs.csv",
            )
        )
    return results
