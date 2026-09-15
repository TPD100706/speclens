"""SpecLens —— 客户技术规格书 AI 智能解析与差异分析。"""
from .models import BaselineParam, ComparisonResult, ParsedRequirement
from .pipeline import SpecPipeline

__all__ = ["SpecPipeline", "ParsedRequirement", "BaselineParam", "ComparisonResult"]
__version__ = "0.1.0"
