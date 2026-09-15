"""确定性差异对比引擎。

判定规则不是拍脑袋，而是从 510 行带标注数据反推的决策表：
（比较方式, 数值关系）→ 差异方向，在 113 条差异样本上 100% 吻合。

方向语义：
  高于 = 客户要求严于内部基线能力 → 不满足（能力缺口）
  低于 = 客户要求宽松于内部基线   → 满足（有余量，可作降本空间）
  不同 = 同值域但口径不同         → 需评估（需设计/认证评估）
  一致 = 客户要求与基线相同       → 满足

enum 参数按"严格度序数"比较，序数表按参数族配置（见 ENUM_SCALES）。
"""
from __future__ import annotations

import re

from .models import BaselineParam
from .value_parser import ParsedValue, parse_value

# 枚举严格度序数表：值 → 序数（越大越严格）
ASIL_ORDER = {"QM": 0, "ASIL A": 1, "ASIL B": 2, "ASIL C": 3, "ASIL D": 4}
REG_ORDER = {"不要求": 0, "可选": 1, "必须": 2}


def _enum_scalar(param_id: str, v: ParsedValue) -> float | None:
    """把枚举值映射成严格度序数；无法映射返回 None。"""
    text = v.text.strip()
    upper = text.upper().replace(" ", "")

    # EMC 类：±N（kV）、Class N、Level N —— 数值即严格度
    if param_id.startswith(("EMC-01", "EMC-02")) and text.startswith("±"):
        return v.value if v.value is not None else None
    m = _match_enum(upper, ["CLASS", "LEVEL"])
    if m is not None:
        return m
    if param_id == "REG-04":  # 功能安全 ASIL
        return ASIL_ORDER.get(text.upper())
    if param_id in ("REG-01", "REG-02", "REG-03"):  # 认证要求 必须/可选/不要求
        return REG_ORDER.get(text)
    if param_id == "REG-05":  # 网络安全 ISO 21434 / ISO 21434 Level N
        # 基线裸值 "ISO 21434" 居中：Level 2 低于、Level 3/4 高于（由标注数据标定）
        if upper == "ISO21434":
            return 2.5
        m = re.search(r"LEVEL(\d)", upper)
        if m:
            return float(m.group(1))
        return None
    if param_id == "ENV-05" and upper.startswith("IP"):  # IP67/IP68/IP69
        digits = re.findall(r"\d", upper)
        if len(digits) == 2:
            return int(digits[0]) * 10 + int(digits[1])
    return None


def _match_enum(upper: str, prefixes: list[str]) -> float | None:
    for p in prefixes:
        if upper.startswith(p):
            m = re.search(r"(\d+)", upper[len(p):])
            if m:
                return float(m.group(1))
    return None


def _equal(a: ParsedValue, b: ParsedValue) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "number":
        return a.value == b.value
    if a.kind == "range":
        return (a.lo, a.hi) == (b.lo, b.hi)
    return a.text.strip() == b.text.strip()


def compare(
    spec_id: str,
    param: BaselineParam,
    customer_raw: str,
) -> tuple[str, str, str]:
    """返回 (direction, status, explanation)。

    direction: 高于/低于/不同/一致/缺失
    status:    满足/需评估/不满足/缺失
    """
    cv = parse_value(customer_raw)
    bv = parse_value(param.value)

    # ---- 枚举 ----
    if param.compare == "enum" or (cv.kind == "enum" or bv.kind == "enum"):
        if _equal(cv, bv):
            return "一致", "满足", f"客户要求与内部基线一致（{param.value}）"
        cs, bs = _enum_scalar(param.param_id, cv), _enum_scalar(param.param_id, bv)
        if cs is not None and bs is not None:
            if param.param_id in ("REG-01", "REG-02", "REG-03") and (
                cv.text.strip() == "不要求" or bs == 0 or cs == 0
            ):
                # 认证类"不要求"与"可选/必须"之间的错配按口径不同处理（与标注一致）
                return "不同", "需评估", (
                    f"客户{cv.text.strip()}，内部基线为{param.value}，需按目标市场法规确认认证范围"
                )
            if cs > bs:
                return "高于", "不满足", (
                    f"客户要求 {cv.text.strip()}，严于内部基线 {param.value}"
                )
            if cs < bs:
                return "低于", "满足", (
                    f"客户要求 {cv.text.strip()}，宽松于内部基线 {param.value}"
                )
        return "不同", "需评估", f"客户要求 {cv.text.strip()}，内部基线为 {param.value}，口径不同需评估"

    # ---- 数值类（min/max/exact/range）----
    cnum, bnum = cv.scalar, bv.scalar

    if param.compare == "range":
        if _equal(cv, bv):
            return "一致", "满足", "客户要求区间与内部基线一致"
        # 区间有交集 → 调整设计可满足；无交集 → 完全不同
        overlap = cv.lo is not None and cv.hi is not None and not (
            cv.hi < bv.lo or cv.lo > bv.hi
        )
        status = "需评估" if overlap else "不满足"
        return "不同", status, (
            f"客户要求 {cv.lo:g}-{cv.hi:g}，内部基线为 {param.value}，区间不匹配"
        )

    if cnum is None or bnum is None:
        return "不同", "需评估", f"客户要求 {customer_raw} 与基线 {param.value} 口径不同，需人工评估"

    if param.compare == "exact":
        if cnum == bnum:
            return "一致", "满足", f"客户要求与内部基线一致（{param.value}）"
        return "不同", "需评估", (
            f"客户要求 {_fmt(cnum)}，内部基线为 {param.value}，规格不同需评估"
        )

    if param.compare == "min":  # 基线为最低保证值，客户值越高越严
        if cnum > bnum:
            return "高于", "不满足", f"客户要求 {_fmt(cnum)}，高于内部基线 {_fmt(bnum)}"
        if cnum < bnum:
            return "低于", "满足", f"客户要求 {_fmt(cnum)}，低于内部基线 {_fmt(bnum)}"
        return "一致", "满足", f"客户要求与内部基线一致（{_fmt(bnum)}）"

    if param.compare == "max":  # 基线为上限值，客户值越低越严
        if cnum < bnum:
            return "高于", "不满足", f"客户要求 {_fmt(cnum)}，严于内部基线 {_fmt(bnum)}"
        if cnum > bnum:
            return "低于", "满足", f"客户要求 {_fmt(cnum)}，宽松于内部基线 {_fmt(bnum)}"
        return "一致", "满足", f"客户要求与内部基线一致（{_fmt(bnum)}）"

    return "不同", "需评估", "未知比较方式，需人工评估"


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)
