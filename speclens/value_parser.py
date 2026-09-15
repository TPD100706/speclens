"""要求值解析：把规格书原文中的要求值解析成可比较的结构。

支持的形态（来自 10 份规格书全量统计）：
  纯数字      "12" "19.2" "-50"
  带前缀      "≥ 1200" "≤ 0.5" "≥ 105"
  区间        "5500-6500" "9-16"
  电浪涌      "±8"
  枚举        "IP67" "Class 4" "ASIL B" "Level 3" "必须" "可选" "不要求"
  组合        "ISO 21434 Level 3"
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedValue:
    kind: str                 # number / range / enum
    op: str | None = None     # ">=" / "<=" / None（对 number 有意义）
    value: float | None = None
    lo: float | None = None
    hi: float | None = None
    text: str = ""            # 枚举原文

    @property
    def scalar(self) -> float | None:
        """用于排序比较的标量：number 取值，range 取中点。"""
        if self.kind == "number":
            return self.value
        if self.kind == "range" and self.lo is not None and self.hi is not None:
            return (self.lo + self.hi) / 2
        return None


_NUM = r"-?\d+(?:\.\d+)?"


def parse_value(raw: str) -> ParsedValue:
    text = str(raw).strip()
    compact = text.replace(" ", "").replace("～", "-").replace("~", "-")

    # 区间 a-b（注意负数：-40 不会命中，因为需要中间的 '-'）
    m = re.fullmatch(rf"({_NUM})-({_NUM})", compact)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return ParsedValue(kind="range", lo=min(lo, hi), hi=max(lo, hi))

    # 带前缀单值
    m = re.fullmatch(rf"(≥|<=?|≤|>=?)({_NUM})", compact)
    if m:
        sym = m.group(1)
        op = ">=" if sym in ("≥", ">=", ">") else "<="
        return ParsedValue(kind="number", op=op, value=float(m.group(2)))

    # 对称容差 ±N
    m = re.fullmatch(rf"±({_NUM})", compact)
    if m:
        return ParsedValue(kind="number", value=float(m.group(1)), text=f"±{m.group(1)}")

    # 纯数字（含负数）
    m = re.fullmatch(_NUM, compact)
    if m:
        return ParsedValue(kind="number", value=float(m.group()))

    # 其余一律视为枚举文本
    return ParsedValue(kind="enum", text=text)


def normalize_for_report(raw: str) -> str:
    """归一化成与评测 CSV 同口径的展示值：去前缀、去空格。"""
    v = parse_value(raw)
    if v.kind == "number":
        num = _fmt(v.value)
        return f"±{num}" if "±" in str(raw).replace(" ", "") else num
    if v.kind == "range":
        return f"{_fmt(v.lo)}-{_fmt(v.hi)}"
    return v.text


def _fmt(x: float | None) -> str:
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else str(x)
