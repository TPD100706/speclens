"""端到端流水线：PDF 解析 → 清单引导抽取 → 确定性对比 → 结果集合。

用法：
    from speclens.pipeline import SpecPipeline
    pipe = SpecPipeline(mode="hybrid")   # hybrid / llm / offline
    results = pipe.run_pdf(pdf_path)     # -> list[ComparisonResult]
    results = pipe.run_dir(data_dir)     # 批量
"""
from __future__ import annotations

import json
from pathlib import Path

from .baseline import load_baseline, load_customer_profiles
from .comparator import compare
from .extractor import Extractor
from .llm_client import LLMClient
from .models import ComparisonResult
from .value_parser import normalize_for_report

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BASELINE_CSV = DATA_DIR / "spec_internal_baseline.csv"
CUSTOMER_CSV = DATA_DIR / "spec_customer_requirements.csv"


class SpecPipeline:
    def __init__(self, mode: str = "hybrid", llm: LLMClient | None = None) -> None:
        self.mode = mode
        self.baseline = load_baseline(BASELINE_CSV)
        self.baseline_by_id = {b.param_id: b for b in self.baseline}
        self.profiles = load_customer_profiles(CUSTOMER_CSV)
        self.llm = llm or (LLMClient() if mode in ("hybrid", "llm") else None)
        self.extractor = Extractor(self.llm if self.llm and self.llm.available else None)

    def run_pdf(self, pdf_path: str | Path) -> list[ComparisonResult]:
        parsed = self.extractor.extract(pdf_path, self.baseline)
        spec_id = parsed["spec_id"]
        profile = self.profiles.get(spec_id, {"customer": "", "product": "", "trait": ""})

        results: list[ComparisonResult] = []
        for req in parsed["requirements"]:
            bl = self.baseline_by_id.get(req.param_id)
            if bl is None:
                continue
            direction, status, explanation = compare(spec_id, bl, req.raw_value)
            results.append(
                ComparisonResult(
                    spec_id=spec_id,
                    customer_name=profile["customer"],
                    product=profile["product"],
                    param_id=req.param_id,
                    param_name=req.param_name or bl.name,
                    category=bl.category,
                    customer_raw=req.raw_value,
                    customer_value=normalize_for_report(req.raw_value),
                    baseline_value=bl.value,
                    unit=bl.unit,
                    status=status,
                    direction=direction,
                    explanation=explanation,
                    evidence=req.evidence,
                    page=req.page,
                    confidence=req.confidence,
                    needs_review=req.confidence < 0.8,
                    source=req.source,
                )
            )
        return results

    def run_dir(self, pdf_dir: str | Path = DATA_DIR / "pdf") -> list[ComparisonResult]:
        all_results: list[ComparisonResult] = []
        pdfs = sorted(Path(pdf_dir).glob("*.pdf"))
        for pdf in pdfs:
            print(f"[pipeline] {pdf.name}")
            all_results.extend(self.run_pdf(pdf))
        return all_results

    @staticmethod
    def save_json(results: list[ComparisonResult], path: str | Path) -> None:
        Path(path).write_text(
            json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
