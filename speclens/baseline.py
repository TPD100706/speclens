"""基线库加载 + 规格书元信息对齐。"""
from __future__ import annotations

import csv
from pathlib import Path

from .models import BaselineParam


def load_baseline(path: str | Path) -> list[BaselineParam]:
    with open(path, encoding="utf-8-sig") as f:
        return [
            BaselineParam(
                param_id=r["参数编号"],
                name=r["参数名称"],
                category=r["参数类别"],
                unit=r["单位"],
                value=r["基线值"],
                compare=r["比较方式"],
            )
            for r in csv.DictReader(f)
        ]


def load_customer_profiles(requirements_csv: str | Path) -> dict[str, dict]:
    """从全量标注 CSV 提取 规格书编号 → {客户名称, 产品} 映射（含客户特征）。"""
    profiles: dict[str, dict] = {}
    with open(requirements_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            profiles.setdefault(
                r["规格书编号"],
                {
                    "customer": r["客户名称"],
                    "product": r["产品"],
                    "trait": r.get("客户特征", ""),
                },
            )
    return profiles
