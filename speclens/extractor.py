"""抽取层：清单引导 LLM 抽取 + 规则通道交叉校验（Extract–Verify–Repair 闭环）。

双通道设计：
  规则通道 —— PyMuPDF 表格结构化提取（离线可用，作为校验基准）
  LLM 通道 —— 按基线参数清单逐页定位抽取（处理版式变化的泛化能力）
合并策略：两通道一致 → 高置信；不一致 → 降置信并标记人工复核（修复提示）；
单通道缺失 → 保留另一方并降置信。LLM 未配置时自动退化为纯规则通道。
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .llm_client import LLMClient
from .models import BaselineParam, ParsedRequirement
from .pdf_parser import parse_pdf
from .prompts import SYSTEM_PROMPT, build_extraction_prompt
from .value_parser import normalize_for_report


class Extractor:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def extract(self, pdf_path: str | Path, baseline: list[BaselineParam]) -> dict:
        parsed = parse_pdf(pdf_path)
        rule_rows = {r.param_id: r for r in parsed["requirements"]}

        llm_rows: dict[str, ParsedRequirement] = {}
        if self.llm is not None and self.llm.available:
            try:
                llm_rows = self._llm_extract(pdf_path, parsed["spec_id"], baseline, rule_rows)
            except Exception as exc:  # LLM 失败不应中断流水线
                print(f"[warn] LLM 抽取失败，回退规则通道：{exc}")

        merged = self._merge(rule_rows, llm_rows)
        parsed["requirements"] = [merged[p] for p in sorted(merged)]
        parsed["llm_used"] = bool(llm_rows)
        return parsed

    # ---- LLM 通道：按页抽取 ----
    def _llm_extract(
        self,
        pdf_path: Path,
        spec_id: str,
        baseline: list[BaselineParam],
        rule_rows: dict[str, ParsedRequirement],
    ) -> dict[str, ParsedRequirement]:
        assert self.llm is not None
        by_page: dict[int, list[str]] = {}
        for pid, r in rule_rows.items():
            by_page.setdefault(r.page, []).append(pid)

        bl_by_id = {b.param_id: b for b in baseline}
        out: dict[str, ParsedRequirement] = {}
        doc = pymupdf.open(pdf_path)
        try:
            for page, pids in sorted(by_page.items()):
                checklist = [bl_by_id[p] .__dict__ | {"param_id": p} for p in pids if p in bl_by_id]
                page_text = doc[page - 1].get_text()
                prompt = build_extraction_prompt(spec_id, page_text, page, checklist)
                items = self.llm.chat_json(SYSTEM_PROMPT, prompt)
                for it in items:
                    if not isinstance(it, dict) or it.get("raw_value") in (None, "未找到"):
                        continue
                    pid = str(it.get("param_id", "")).strip()
                    if pid not in rule_rows:
                        continue  # 清单外参数暂不入库，避免与基线对不齐
                    base = rule_rows[pid]
                    out[pid] = ParsedRequirement(
                        param_id=pid,
                        param_name=str(it.get("param_name") or base.param_name),
                        raw_value=str(it.get("raw_value")).strip(),
                        unit=str(it.get("unit") or base.unit),
                        test_method=str(it.get("test_method") or base.test_method),
                        section=base.section,
                        page=page,
                        evidence=str(it.get("evidence") or ""),
                        confidence=float(it.get("confidence") or 0.8),
                        note=str(it.get("note") or ""),
                        source="llm",
                    )
        finally:
            doc.close()
        return out

    # ---- 校验合并：两通道交叉对账 ----
    def _merge(
        self, rule_rows: dict[str, ParsedRequirement], llm_rows: dict[str, ParsedRequirement]
    ) -> dict[str, ParsedRequirement]:
        merged: dict[str, ParsedRequirement] = {}
        for pid, rule in rule_rows.items():
            llm = llm_rows.get(pid)
            if llm is None:
                rule.confidence = 0.9
                merged[pid] = rule
                continue
            if normalize_for_report(rule.raw_value) == normalize_for_report(llm.raw_value):
                rule.confidence = 1.0
                rule.source = "hybrid"
                rule.note = llm.note
                merged[pid] = rule
            else:
                # 不一致：以规则通道结构化值为准，保留 LLM 值供人工复核
                rule.confidence = 0.5
                rule.source = "conflict"
                rule.note = f"两通道不一致：LLM={llm.raw_value!r} 规则={rule.raw_value!r}"
                merged[pid] = rule
        for pid, llm in llm_rows.items():
            if pid not in merged:
                llm.confidence = min(llm.confidence, 0.6)
                merged[pid] = llm
        return merged
