"""PDF 解析层：基于 PyMuPDF 的表格/章节感知解析。

规格书版式固定（各厂商模板一致）：
  - 每个性能维度一节（2. 光学性能 / 3. 电气性能 / ...），节内一张表
  - 表列：No. | Parameter/参数 | Requirement/要求 | Unit/单位 | Test Method/测试方法
解析输出按"参数编号 → ParsedRequirement"组织，同时保留章节归属与页码证据。
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .models import ParsedRequirement

PARAM_ID_RE = re.compile(r"^(OPT|ELE|ENV|MEC|EMC|REG|REL)-\d{2}$")

# 章节/参数编号前缀 → 参数类别（与内部基线的"参数类别"一致）
SECTION_OF_PREFIX = {
    "OPT": "光学性能",
    "ELE": "电气性能",
    "ENV": "环境耐久",
    "MEC": "机械性能",
    "EMC": "EMC电磁兼容",
    "REG": "法规认证",
    "REL": "可靠性",
}

SPEC_ID_RE = re.compile(r"SPEC-\d{3}")


def parse_pdf(pdf_path: str | Path) -> dict:
    """解析一份规格书 PDF。

    返回 {"spec_id", "meta", "requirements": list[ParsedRequirement]}
    """
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    spec_id = _find_spec_id(doc)
    rows: list[ParsedRequirement] = []

    for pno, page in enumerate(doc, start=1):
        for table in page.find_tables().tables:
            extracted = table.extract()
            for row in extracted:
                cells = [(c or "").strip() for c in row]
                if len(cells) < 5 or not PARAM_ID_RE.match(cells[0]):
                    continue
                param_id, name, raw, unit, method = cells[:5]
                rows.append(
                    ParsedRequirement(
                        param_id=param_id,
                        param_name=name.replace("\n", ""),
                        raw_value=raw.replace("\n", " ").strip(),
                        unit=unit.replace("\n", ""),
                        test_method=method.replace("\n", " ").strip(),
                        section=SECTION_OF_PREFIX.get(param_id.split("-")[0], ""),
                        page=pno,
                        evidence=f"P{pno} [{param_id}] {name} {raw} {unit}",
                        source="rule",
                    )
                )
    doc.close()
    return {"spec_id": spec_id, "file": pdf_path.name, "requirements": rows}


def _find_spec_id(doc: pymupdf.Document) -> str:
    for page in doc:
        m = SPEC_ID_RE.search(page.get_text())
        if m:
            return m.group()
    return ""
