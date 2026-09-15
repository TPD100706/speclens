"""数据模型：解析结果、基线参数、对比结果。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class ParsedRequirement:
    """从客户规格书中抽取出的一条参数要求。"""

    param_id: str            # 参数编号，如 OPT-01（与内部基线对齐的主键）
    param_name: str          # 参数名称，如 近光光通量
    raw_value: str           # 原文要求值，如 "≥ 1200" / "5500-6500" / "IP67"
    unit: str                # 单位，如 lm
    test_method: str         # 测试方法/法规引用
    section: str             # 所属章节（参数类别），如 光学性能
    page: int = 0            # 证据所在页码（1-based）
    evidence: str = ""       # 原文证据片段（可审计）
    confidence: float = 1.0  # 抽取置信度 0~1
    note: str = ""           # LLM 补充说明（特殊要求、备注等）
    source: str = "rule"     # 抽取来源: rule / llm / hybrid

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BaselineParam:
    """内部能力基线中的一条参数。"""

    param_id: str
    name: str
    category: str
    unit: str
    value: str
    compare: str             # min / max / exact / range / enum


@dataclass
class ComparisonResult:
    """一个参数的客户要求 vs 内部基线对比结果。"""

    spec_id: str             # 规格书编号，如 SPEC-001
    customer_name: str       # 客户名称
    product: str             # 产品
    param_id: str
    param_name: str
    category: str
    customer_raw: str        # 客户要求原文
    customer_value: str      # 归一化后的客户要求值（对齐评测 CSV 口径）
    baseline_value: str
    unit: str
    status: str              # 满足 / 需评估 / 不满足 / 缺失
    direction: str           # 高于 / 低于 / 不同 / 一致 / 缺失
    explanation: str         # 差异说明
    evidence: str            # PDF 原文证据
    page: int = 0
    confidence: float = 1.0
    needs_review: bool = False   # 低置信度，建议人工复核
    source: str = "rule"

    def to_dict(self) -> dict:
        return asdict(self)
