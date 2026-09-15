"""抽取提示词：清单引导式抽取（Checklist-driven Extraction）。

与常规"开放抽取"不同，我们把内部基线的参数清单作为 checklist 注入提示词，
要求 LLM 逐参数在原文中定位并给出证据 —— 召回率天然向 100% 收敛，
且每个参数携带可审计的原文证据锚点。
"""

SYSTEM_PROMPT = """你是汽车车灯行业的规格书解析专家。你的任务是从客户技术规格书的文本中，
抽取指定的参数要求。严格遵守：
1. 只输出 JSON，不要输出任何解释性文字；
2. 每个参数必须给出原文中出现的原始要求值（含 ≥/≤/± 等前缀，保持原样）；
3. 给出 evidence 字段：原文中承载该要求的最短片段；
4. 不确定的参数 confidence 给 0~0.6，并在 note 中说明原因；绝不要编造数值。
"""

USER_PROMPT_TEMPLATE = """【规格书元信息】
规格书编号：{spec_id}

【章节原文】（来自 PDF 第 {page} 页，表格已转为文本行）
{section_text}

【待抽取参数清单】（内部基线参数，请逐个在原文中定位）
{checklist}

请输出 JSON 数组，每个元素形如：
{{
  "param_id": "OPT-01",
  "param_name": "近光光通量",
  "raw_value": "≥ 1200",
  "unit": "lm",
  "test_method": "GB/T 15766 / ECE R149 Annex 4",
  "evidence": "OPT-01 近光光通量 ≥ 1200 lm",
  "confidence": 0.98,
  "note": ""
}}
清单中每个参数都要包含在输出里；原文中确实不存在的参数，raw_value 填 "未找到" 且 confidence ≤ 0.3。
"""


def build_extraction_prompt(spec_id: str, section_text: str, page: int, checklist: list[dict]) -> str:
    checklist_lines = [
        f"- {p['param_id']} {p['name']}（{p['category']}，基线值 {p['value']} {p['unit']}）"
        for p in checklist
    ]
    return USER_PROMPT_TEMPLATE.format(
        spec_id=spec_id,
        page=page,
        section_text=section_text,
        checklist="\n".join(checklist_lines),
    )
