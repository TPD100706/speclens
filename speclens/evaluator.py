"""评测模块：把流水线输出与官方标注对齐，产出「效果数据表」所需指标。

指标口径（对齐赛题评分要点）：
  参数提取召回率/精确率 —— 抽取到的 (规格书, 参数) 对 vs 标注全集 510 行
  要求值抽取准确率      —— 抽取值归一化后与标注"客户要求值"逐字一致
  差异方向准确率        —— 113 条差异行上 高于/低于/不同 判定正确率
  满足/不满足误判数     —— 510 行上判定状态与标注是否差异的混淆
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .models import ComparisonResult


@dataclass
class EvalReport:
    extraction_recall: float
    extraction_precision: float
    value_accuracy: float
    direction_accuracy: float          # 在差异行上
    direction_correct: int
    direction_total: int
    satisfy_misjudged: int             # 标注一致但判为不满足/需评估
    gap_missed: int                    # 标注差异但判为一致
    n_specs: int
    n_params_expected: int
    n_params_extracted: int

    def as_lines(self) -> list[str]:
        return [
            f"参数提取召回率        {self.extraction_recall:.1%}  ({self.n_params_extracted}/{self.n_params_expected})",
            f"参数提取精确率        {self.extraction_precision:.1%}",
            f"要求值抽取准确率      {self.value_accuracy:.1%}",
            f"差异方向准确率        {self.direction_accuracy:.1%}  ({self.direction_correct}/{self.direction_total} 条差异行)",
            f"一致行误判为差异      {self.satisfy_misjudged}",
            f"差异行漏判为一致      {self.gap_missed}",
            f"覆盖规格书            {self.n_specs} 份",
        ]


def evaluate(results: list[ComparisonResult], ground_truth_csv: str | Path) -> EvalReport:
    with open(ground_truth_csv, encoding="utf-8-sig") as f:
        gt = {(r["规格书编号"], r["参数编号"]): r for r in csv.DictReader(f)}

    got = {(r.spec_id, r.param_id): r for r in results}
    hits = set(got) & set(gt)

    # 要求值准确率
    value_ok = sum(
        1 for k in hits if _norm(got[k].customer_value) == _norm(gt[k]["客户要求值"])
    )
    # 差异行方向判定
    gap_keys = [k for k in hits if gt[k]["是否存在差异"] == "是"]
    dir_ok = sum(1 for k in gap_keys if got[k].direction == gt[k]["差异方向"])
    # 满足/不满足混淆
    satisfy_misjudged = sum(
        1 for k in hits if gt[k]["是否存在差异"] == "否" and got[k].direction != "一致"
    )
    gap_missed = sum(
        1 for k in hits if gt[k]["是否存在差异"] == "是" and got[k].direction == "一致"
    )

    n = len(hits) or 1
    return EvalReport(
        extraction_recall=len(hits) / len(gt),
        extraction_precision=len(hits) / len(got) if got else 0.0,
        value_accuracy=value_ok / n,
        direction_accuracy=dir_ok / len(gap_keys) if gap_keys else 0.0,
        direction_correct=dir_ok,
        direction_total=len(gap_keys),
        satisfy_misjudged=satisfy_misjudged,
        gap_missed=gap_missed,
        n_specs=len({k[0] for k in got}),
        n_params_expected=len(gt),
        n_params_extracted=len(got),
    )


def _norm(v: str) -> str:
    v = str(v).strip().replace(" ", "")
    if v.endswith(".0"):
        v = v[:-2]
    if "." in v:
        v = v.rstrip("0").rstrip(".")
    return v
