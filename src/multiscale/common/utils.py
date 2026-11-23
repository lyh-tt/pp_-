from __future__ import annotations

import re
from math import isfinite
from pathlib import Path

import numpy as np


def clean_float(value: float) -> float:
    cleaned = round(float(value), 10)
    return 0.0 if cleaned == 0 else cleaned


def format_number(value: float) -> str:
    if not isfinite(value):
        raise ValueError("数值必须为有限实数。")
    return np.format_float_positional(clean_float(value), trim="-")


def natural_sort_key(text: str) -> tuple[tuple[int, int | str], ...]:
    """自然排序键，确保 5、6、7、10 等数字后缀按数值大小排列。"""
    parts = re.split(r"(\d+)", str(text))
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in parts
        if part
    )


def read_text_with_fallback(path: Path) -> str:
    """兼容 UTF-8 BOM / UTF-8 / GB18030 / GBK 读取文本文件。"""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别文件编码：{Path(path).name}")
