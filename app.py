"""SpecLens Demo —— Streamlit 交互界面。

运行： streamlit run app.py
支持批量上传任意规格书 PDF 实时解析，或直接查看 10 份样例的全量对比结果。
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from speclens.llm_client import LLMClient
from speclens.models import ComparisonResult
from speclens.pipeline import DATA_DIR, SpecPipeline
from speclens.reporter import save_excel, save_markdown

st.set_page_config(page_title="SpecLens · 规格书智能解析与差异分析", page_icon="🔍", layout="wide")

SAMPLE_DIR = DATA_DIR / "pdf"
PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_STATE_DIR = PROJECT_ROOT / ".speclens_state"
MODEL_CONFIG_FILE = LOCAL_STATE_DIR / "models.json"
HISTORY_DIR = LOCAL_STATE_DIR / "history"
STATUS_COLOR = {
    "不满足": "background-color:#F8CBAD;color:#9C0006;font-weight:600",
    "需评估": "background-color:#FFE699;color:#7F6000;font-weight:600",
    "满足": "background-color:#C6E0B4;color:#215E21;font-weight:600",
}
SEV_COLOR = {"不满足": "#C00000", "需评估": "#E6A700", "满足": "#2E8B57", "宽松": "#70AD47"}


@st.cache_resource(show_spinner=False)
def get_pipeline(
    mode: str,
    base_url: str = "",
    model_id: str = "",
    api_key: str = "",
    temperature: float = 0.1,
    timeout: int = 120,
) -> SpecPipeline:
    llm = None
    if mode in ("hybrid", "llm"):
        llm = LLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model_id,
            temperature=temperature,
            timeout=timeout,
        )
    return SpecPipeline(mode=mode, llm=llm)


@st.cache_data(show_spinner=False)
def run_sample(
    spec_file: str,
    mode: str,
    base_url: str = "",
    model_id: str = "",
    api_key: str = "",
    temperature: float = 0.1,
    timeout: int = 120,
) -> list[dict]:
    pipe = get_pipeline(mode, base_url, model_id, api_key, temperature, timeout)
    return [r.to_dict() for r in pipe.run_pdf(SAMPLE_DIR / spec_file)]


@st.cache_data(show_spinner=False)
def run_uploaded(
    file_name: str,
    file_bytes: bytes,
    mode: str,
    base_url: str = "",
    model_id: str = "",
    api_key: str = "",
    temperature: float = 0.1,
    timeout: int = 120,
) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        pipe = get_pipeline(mode, base_url, model_id, api_key, temperature, timeout)
        return [r.to_dict() for r in pipe.run_pdf(tmp_path)]
    except Exception:
        return []
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def load_model_configs() -> list[dict]:
    configs = _load_json(MODEL_CONFIG_FILE, [])
    return configs if isinstance(configs, list) else []


def save_model_config(config: dict) -> None:
    configs = load_model_configs()
    config["updated_at"] = datetime.now().isoformat(timespec="seconds")
    name_key = config["name"].strip().casefold()
    configs = [c for c in configs if str(c.get("name", "")).strip().casefold() != name_key]
    configs.append(config)
    configs.sort(key=lambda item: str(item.get("name", "")).casefold())
    _save_json(MODEL_CONFIG_FILE, configs)


def is_valid_base_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def current_reviewer() -> str:
    """Use the local computer name as the temporary reviewer identity."""
    return platform.node().strip() or "本机"


def history_summary(results: list[dict]) -> dict:
    return {
        "parameter_count": len(results),
        "gap_count": sum(r.get("direction") != "一致" for r in results),
        "hard_gap_count": sum(r.get("status") == "不满足" for r in results),
        "review_count": sum(r.get("status") == "需评估" for r in results),
    }


def save_history_record(
    file_name: str,
    file_bytes: bytes,
    results: list[dict],
    mode_label: str,
    model_name: str,
) -> str:
    identity = hashlib.sha256(
        file_bytes + f"|{mode_label}|{model_name}".encode("utf-8")
    ).hexdigest()[:20]
    path = HISTORY_DIR / f"{identity}.json"
    existing = _load_json(path, {})
    analyzed_at = existing.get("analyzed_at") or datetime.now().isoformat(timespec="seconds")
    stored_results = existing.get("results") or results
    summary = history_summary(stored_results)
    customers = sorted(
        {
            str(r.get("customer_name", "")).strip()
            for r in stored_results
            if r.get("customer_name")
        }
    )
    _save_json(
        path,
        {
            "id": identity,
            "document_hash": hashlib.sha256(file_bytes).hexdigest(),
            "file_name": file_name,
            "analyzed_at": analyzed_at,
            "mode": mode_label,
            "model_name": model_name,
            "customers": customers,
            "summary": summary,
            "results": stored_results,
            "reviews": existing.get("reviews", {}),
            "audit_log": existing.get("audit_log", []),
            "updated_at": existing.get("updated_at", analyzed_at),
        },
    )
    return identity


def save_review_decision(
    record_id: str,
    param_id: str,
    direction: str,
    status: str,
    explanation: str,
    note: str,
) -> bool:
    """Persist the reviewed result and append an immutable audit entry."""
    path = HISTORY_DIR / f"{record_id}.json"
    record = _load_json(path, {})
    if not isinstance(record, dict) or not record.get("results"):
        return False

    target = next(
        (result for result in record["results"] if result.get("param_id") == param_id),
        None,
    )
    if target is None:
        return False

    reviewed_at = datetime.now().isoformat(timespec="seconds")
    reviewer = current_reviewer()
    before = {
        "direction": str(target.get("direction", "")),
        "status": str(target.get("status", "")),
        "explanation": str(target.get("explanation", "")),
    }
    after = {
        "direction": direction,
        "status": status,
        "explanation": "" if direction == "一致" else explanation.strip(),
    }
    target.update(after)
    target["needs_review"] = False

    reviews = record.get("reviews", {})
    if not isinstance(reviews, dict):
        reviews = {}
    reviews[param_id] = {
        "status": "已确认",
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "note": note.strip(),
    }

    audit_log = record.get("audit_log", [])
    if not isinstance(audit_log, list):
        audit_log = []
    changed_fields = [field for field in after if before.get(field) != after.get(field)]
    audit_log.append(
        {
            "timestamp": reviewed_at,
            "reviewer": reviewer,
            "action": "修改并确认" if changed_fields else "审核确认",
            "param_id": param_id,
            "param_name": str(target.get("param_name", "")),
            "before": before,
            "after": after,
            "changed_fields": changed_fields,
            "note": note.strip(),
        }
    )

    record["reviews"] = reviews
    record["audit_log"] = audit_log
    record["summary"] = history_summary(record["results"])
    record["updated_at"] = reviewed_at
    _save_json(path, record)
    return True


def load_history_records() -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    records = []
    for path in HISTORY_DIR.glob("*.json"):
        record = _load_json(path, {})
        if isinstance(record, dict) and record.get("id") and record.get("results"):
            records.append(record)
    return sorted(
        records,
        key=lambda item: item.get("updated_at") or item.get("analyzed_at", ""),
        reverse=True,
    )


def find_history_record(record_id: str | None) -> dict | None:
    if not record_id:
        return None
    record = _load_json(HISTORY_DIR / f"{record_id}.json", {})
    return record if isinstance(record, dict) and record.get("results") else None


def history_document_key(record: dict) -> str:
    """Return a stable document identity across modes, models, and legacy records."""
    spec_ids = sorted(
        {
            str(result.get("spec_id", "")).strip().casefold()
            for result in record.get("results", [])
            if str(result.get("spec_id", "")).strip()
        }
    )
    if len(spec_ids) == 1:
        return f"spec:{spec_ids[0]}"

    document_hash = str(record.get("document_hash", "")).strip().casefold()
    if document_hash:
        return f"hash:{document_hash}"

    file_name = Path(str(record.get("file_name", "未命名文档"))).stem
    normalized_name = "".join(file_name.split()).casefold()
    return f"name:{normalized_name}"


def history_record_priority(record: dict) -> tuple:
    """Prefer reviewed records, then the most recently reviewed/analyzed run."""
    reviews = record.get("reviews", {})
    review_count = len(reviews) if isinstance(reviews, dict) else 0
    audit_log = record.get("audit_log", [])
    if not isinstance(audit_log, list):
        audit_log = []
    review_times = [
        str(entry.get("timestamp", ""))
        for entry in audit_log
        if isinstance(entry, dict) and entry.get("timestamp")
    ]
    latest_review_time = max(review_times, default="")
    activity_time = str(record.get("updated_at") or record.get("analyzed_at", ""))
    has_review = bool(review_count or audit_log)
    return has_review, latest_review_time, activity_time, review_count


def deduplicate_history_records(records: list[dict]) -> list[dict]:
    """Keep one representative per specification across modes and legacy records."""
    representatives: dict[str, dict] = {}
    for record in records:
        document_key = history_document_key(record)
        current = representatives.get(document_key)
        if current is None or history_record_priority(record) > history_record_priority(current):
            representatives[document_key] = record
    return sorted(
        representatives.values(),
        key=lambda item: str(item.get("updated_at") or item.get("analyzed_at", "")),
        reverse=True,
    )


def pipeline_args(config: dict | None) -> tuple[str, str, str, float, int]:
    if not config:
        return "", "", "", 0.1, 120
    return (
        str(config.get("base_url", "")),
        str(config.get("model_id", "")),
        str(config.get("api_key", "")),
        float(config.get("temperature", 0.1)),
        int(config.get("timeout", 120)),
    )


def to_df(results: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(results)


def spec_label(name: str) -> str:
    """文件名 → 友好标签：SPEC-001_AUTOLUX_GmbH_... → SPEC-001 · AUTOLUX"""
    stem = name.removesuffix(".pdf")
    parts = stem.split("_", 2)
    return f"{parts[0]} · {parts[1]}" if len(parts) >= 2 else stem


def customer_card(spec_id: str, profile: dict | None) -> None:
    """客户信息卡：客户名称 · 产品 · 客户特征（档案未收录时降级显示编号）。"""
    if profile:
        trait = f' · {profile["trait"]}' if profile.get("trait") else ""
        st.markdown(
            f'<div style="background:#EEF3F8;border-left:4px solid #1F4E79;border-radius:8px;'
            f'padding:12px 18px;margin:4px 0 14px 0">'
            f'<b style="font-size:17px">{profile["customer"]}</b>'
            f'<span style="color:#4B5563"> · {profile["product"]}</span><br>'
            f'<span style="color:#6B7280;font-size:13px">{spec_id}{trait}</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"{spec_id} · 样例库外的规格书，客户档案未收录")


def show_metrics(df: pd.DataFrame) -> None:
    n_gap = int((df["direction"] != "一致").sum())
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("参数总数", len(df))
    c2.metric("差异项", n_gap)
    c3.metric("不满足", int((df["status"] == "不满足").sum()))
    c4.metric("需评估", int((df["status"] == "需评估").sum()))
    c5.metric("宽松(降本空间)", int((df["direction"] == "低于").sum()))


def show_params_table(df: pd.DataFrame) -> None:
    vc = df["source"].value_counts()
    if {"hybrid", "conflict", "llm"} & set(vc.index):
        st.info(
            f"🔬 双通道对账结果：两通道一致 **{vc.get('hybrid', 0)}** 项（置信度 1.0）· "
            f"冲突待人工复核 **{vc.get('conflict', 0)}** 项（置信度 0.5，以规则结构化值为准）· "
            f"仅规则通道 **{vc.get('rule', 0)}** 项（置信度 0.9）"
        )
    table_data = df[[
        "param_id", "param_name", "category", "customer_raw", "baseline_value",
        "unit", "direction", "status", "explanation", "evidence", "page", "source",
    ]].copy()
    table_data.loc[table_data["direction"] == "一致", "explanation"] = ""
    st.dataframe(
        table_data,
        width="stretch",
        height=480,
        column_config={
            "customer_raw": st.column_config.TextColumn("客户要求(原文)"),
            "baseline_value": st.column_config.TextColumn("内部基线"),
            "direction": st.column_config.TextColumn("差异方向"),
            "status": st.column_config.TextColumn("判定"),
            "evidence": st.column_config.TextColumn("PDF 原文证据", help="判定依据的原文片段与页码，可审计"),
            "page": st.column_config.NumberColumn("证据页", format="%d"),
            "source": st.column_config.TextColumn(
                "抽取来源",
                help="hybrid=双通道一致 · conflict=两通道不一致（已降置信待复核）· rule=仅规则通道 · llm=仅LLM通道",
            ),
        },
        hide_index=True,
    )


def show_compliance_overview(df: pd.DataFrame) -> None:
    """当前规格书参数符合性百分比环形图。"""
    total = len(df)
    if total == 0:
        st.info("当前规格书没有可统计的参数。")
        return

    overview = pd.DataFrame(
        [
            {"判定": "符合", "数量": int((df["status"] == "满足").sum()), "顺序": 0},
            {"判定": "不符合", "数量": int((df["status"] == "不满足").sum()), "顺序": 1},
            {"判定": "需评估", "数量": int((df["status"] == "需评估").sum()), "顺序": 2},
        ]
    )
    overview["占比"] = overview["数量"] / total
    overview["百分比"] = overview["占比"].map(lambda value: f"{value:.1%}")
    plotted = overview[overview["数量"] > 0].copy()
    label_data = plotted[plotted["占比"] >= 0.06]
    compliant_rate = float(overview.loc[overview["判定"] == "符合", "占比"].iloc[0])

    color_scale = alt.Scale(
        domain=["符合", "不符合", "需评估"],
        range=[SEV_COLOR["满足"], SEV_COLOR["不满足"], SEV_COLOR["需评估"]],
    )
    theta = alt.Theta("数量:Q", stack=True)
    order = alt.Order("顺序:Q", sort="ascending")
    arcs = (
        alt.Chart(plotted)
        .mark_arc(innerRadius=78, outerRadius=125, padAngle=0.025, cornerRadius=5)
        .encode(
            theta=theta,
            order=order,
            color=alt.Color(
                "判定:N",
                scale=color_scale,
                sort=["符合", "不符合", "需评估"],
                legend=alt.Legend(title=None, orient="right", labelFontSize=14),
            ),
            tooltip=[
                alt.Tooltip("判定:N"),
                alt.Tooltip("数量:Q", title="参数数量"),
                alt.Tooltip("百分比:N", title="占比"),
            ],
        )
    )
    labels = (
        alt.Chart(label_data)
        .mark_text(radius=101, color="white", fontSize=13, fontWeight="bold")
        .encode(theta=theta, order=order, text="百分比:N")
    )
    center_rate = (
        alt.Chart(pd.DataFrame({"文本": [f"{compliant_rate:.1%}"]}))
        .mark_text(fontSize=28, fontWeight="bold", color="#1F4E79", dy=-7)
        .encode(text="文本:N")
    )
    center_label = (
        alt.Chart(pd.DataFrame({"文本": ["符合率"]}))
        .mark_text(fontSize=13, color="#6B7280", dy=22)
        .encode(text="文本:N")
    )
    st.altair_chart(
        (arcs + labels + center_rate + center_label).properties(height=300),
        width="stretch",
    )
    st.caption(
        " · ".join(
            f"{row['判定']} {int(row['数量'])} 项（{row['百分比']}）"
            for _, row in overview.iterrows()
        )
    )


def risk_table(dfs: dict[str, pd.DataFrame], pipe: SpecPipeline) -> pd.DataFrame:
    """每份规格书一行：不满足/需评估/宽松计数 + 加权风险分 + 风险等级。"""
    rows = []
    for name, d in dfs.items():
        sid = d["spec_id"].iloc[0]
        prof = pipe.profiles.get(sid, {})
        rows.append({
            "规格书": spec_label(name) if name.endswith(".pdf") else sid,
            "客户": prof.get("customer") or d["customer_name"].iloc[0] or "（未收录）",
            "参数总数": len(d),
            "不满足": int((d["status"] == "不满足").sum()),
            "需评估": int((d["status"] == "需评估").sum()),
            "宽松": int((d["direction"] == "低于").sum()),
        })
    rk = pd.DataFrame(rows)
    rk["风险分"] = rk["不满足"] * 3 + rk["需评估"] * 2
    rk = rk.sort_values("风险分", ascending=False).reset_index(drop=True)
    rk.insert(0, "排名", range(1, len(rk) + 1))
    rk["风险等级"] = pd.cut(rk["风险分"], bins=[-1, 4, 14, 10**6], labels=["低", "中", "高"]).astype(str)
    return rk


def show_risk_ranking(rk: pd.DataFrame) -> None:
    left, right = st.columns([5, 4])
    with left:
        st.dataframe(
            rk,
            width="stretch",
            hide_index=True,
            column_config={
                "风险分": st.column_config.ProgressColumn(
                    "风险分（不满足×3 + 需评估×2）",
                    min_value=0,
                    max_value=max(int(rk["风险分"].max()), 1),
                    format="%d",
                ),
            },
        )
    with right:
        m = rk.melt(id_vars=["规格书"], value_vars=["不满足", "需评估", "宽松"], var_name="status", value_name="count")
        chart = (
            alt.Chart(m)
            .mark_bar()
            .encode(
                x=alt.X("count:Q", title="参数项数", stack=True),
                y=alt.Y("规格书:N", sort=rk["规格书"].tolist(), title=None),
                color=alt.Color(
                    "status:N",
                    scale=alt.Scale(domain=["不满足", "需评估", "宽松"], range=[SEV_COLOR["不满足"], SEV_COLOR["需评估"], SEV_COLOR["宽松"]]),
                    legend=alt.Legend(title=None),
                ),
                tooltip=[alt.Tooltip("规格书:N"), alt.Tooltip("status:N", title="类别"), alt.Tooltip("count:Q", title="项数")],
            )
            .properties(height=max(160, 30 * len(rk) + 30))
        )
        st.altair_chart(chart, width="stretch")


def show_heatmap(cached: dict[str, pd.DataFrame]) -> None:
    """真热力图：规格书 × 参数类别，颜色深浅 = 加权差异分（不满足×3 / 需评估×2 / 宽松×1）。"""
    labeled_frames = []
    specs = []
    used_labels: dict[str, int] = {}
    for file_name, data in cached.items():
        base_label = (
            str(data["spec_id"].iloc[0])
            if not data.empty and "spec_id" in data.columns and str(data["spec_id"].iloc[0]).strip()
            else Path(file_name).stem[:20]
        )
        used_labels[base_label] = used_labels.get(base_label, 0) + 1
        label = base_label if used_labels[base_label] == 1 else f"{base_label}-{used_labels[base_label]}"
        specs.append(label)
        labeled_frames.append(data.assign(spec=label))

    gaps = pd.concat(labeled_frames)
    gaps = gaps[gaps["direction"].isin(["高于", "低于", "不同"])].copy()
    gaps["w"] = gaps["status"].map({"不满足": 3, "需评估": 2}).fillna(1)
    hl = (
        gaps.groupby(["spec", "category"])
        .agg(score=("w", "sum"), n_hard=("status", lambda s: (s == "不满足").sum()),
             n_eval=("status", lambda s: (s == "需评估").sum()), n_loose=("direction", lambda s: (s == "低于").sum()))
        .reset_index()
    )
    cats = hl.groupby("category")["score"].sum().sort_values(ascending=False).index.tolist()
    full = pd.MultiIndex.from_product([specs, cats], names=["spec", "category"]).to_frame(index=False)
    hl = full.merge(hl, how="left", on=["spec", "category"]).fillna({"score": 0, "n_hard": 0, "n_eval": 0, "n_loose": 0})
    hl[["score", "n_hard", "n_eval", "n_loose"]] = hl[["score", "n_hard", "n_eval", "n_loose"]].astype(int)

    rect = (
        alt.Chart(hl)
        .mark_rect()
        .encode(
            x=alt.X("spec:N", sort=specs, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("category:N", sort=cats, title=None),
            color=alt.Color("score:Q", scale=alt.Scale(scheme="orangered"), legend=alt.Legend(title="加权差异分")),
            tooltip=[
                alt.Tooltip("spec:N", title="规格书"),
                alt.Tooltip("category:N", title="参数类别"),
                alt.Tooltip("n_hard:Q", title="不满足"),
                alt.Tooltip("n_eval:Q", title="需评估"),
                alt.Tooltip("n_loose:Q", title="宽松"),
                alt.Tooltip("score:Q", title="加权分"),
            ],
        )
    )
    text = (
        alt.Chart(hl[hl["score"] > 0])
        .mark_text(fontSize=13, fontWeight="bold")
        .encode(
            x=alt.X("spec:N", sort=specs, axis=None),
            y=alt.Y("category:N", sort=cats, axis=None),
            text="score:Q",
            color=alt.condition(alt.datum.score > 6, alt.value("white"), alt.value("#1F2937")),
        )
    )
    st.altair_chart((rect + text).properties(height=max(200, 36 * len(cats) + 50)), width="stretch")


def audit_change_summary(entry: dict) -> str:
    labels = {"direction": "差异方向", "status": "判定", "explanation": "差异说明"}
    before = entry.get("before", {})
    after = entry.get("after", {})
    changes = []
    for field in entry.get("changed_fields", []):
        old_value = str(before.get(field, "") or "空")
        new_value = str(after.get(field, "") or "空")
        changes.append(f"{labels.get(field, field)}：{old_value} → {new_value}")
    return "；".join(changes) if changes else "字段未修改，仅完成审核确认"


def render_review_panel(df: pd.DataFrame, record_id: str, key_prefix: str) -> None:
    """Render parameter review controls and the persistent audit trail."""
    record = find_history_record(record_id)
    if not record:
        return

    results = record.get("results", [])
    reviews = record.get("reviews", {}) if isinstance(record.get("reviews", {}), dict) else {}
    audit_log = record.get("audit_log", []) if isinstance(record.get("audit_log", []), list) else []
    result_by_id = {str(result.get("param_id", "")): result for result in results}
    param_ids = [param_id for param_id in result_by_id if param_id]
    reviewer = current_reviewer()

    with st.expander(f"✍️ 人工审核与修改 · 已确认 {len(reviews)} / {len(results)} 项"):
        st.caption(
            f"当前审核人：{reviewer}（暂取电脑名称） · 保存后立即更新历史版本、统计图和导出报告。"
        )
        if not param_ids:
            st.info("当前记录没有可审核的参数。")
        else:
            param_query = st.text_input(
                "搜索要审核的参数",
                placeholder="输入编号前缀、完整编号、名称或类别，例如 ELE",
                key=f"{key_prefix}_review_search",
            )
            query = param_query.strip().casefold()
            matching_param_ids = [
                param_id
                for param_id in param_ids
                if not query
                or query
                in " ".join(
                    [
                        param_id,
                        str(result_by_id[param_id].get("param_name", "")),
                        str(result_by_id[param_id].get("category", "")),
                        str(result_by_id[param_id].get("direction", "")),
                        str(result_by_id[param_id].get("status", "")),
                    ]
                ).casefold()
            ]
            st.caption(f"匹配 {len(matching_param_ids)} / {len(param_ids)} 项")
            if not matching_param_ids:
                st.warning("没有找到匹配参数，请更换编号前缀或关键词。")
                return

            selected_param = st.selectbox(
                "选择要审核的参数",
                matching_param_ids,
                format_func=lambda param_id: (
                    f"{param_id} · {result_by_id[param_id].get('param_name', '')} · "
                    f"{result_by_id[param_id].get('direction', '')} / "
                    f"{result_by_id[param_id].get('status', '')}"
                ),
                key=f"{key_prefix}_review_param",
            )
            selected_result = result_by_id[selected_param]
            existing_review = reviews.get(selected_param, {})
            if existing_review:
                st.success(
                    f"已由 {existing_review.get('reviewer', '未知')} 于 "
                    f"{str(existing_review.get('reviewed_at', '')).replace('T', ' ')} 确认"
                )

            direction_options = ["一致", "高于", "低于", "不同", "缺失"]
            status_options = ["满足", "需评估", "不满足", "缺失"]
            current_direction = str(selected_result.get("direction", "一致"))
            current_status = str(selected_result.get("status", "满足"))
            if current_direction not in direction_options:
                direction_options.append(current_direction)
            if current_status not in status_options:
                status_options.append(current_status)

            with st.form(f"{key_prefix}_{selected_param}_review_form"):
                direction_col, status_col = st.columns(2)
                with direction_col:
                    reviewed_direction = st.selectbox(
                        "差异方向",
                        direction_options,
                        index=direction_options.index(current_direction),
                    )
                with status_col:
                    reviewed_status = st.selectbox(
                        "判定",
                        status_options,
                        index=status_options.index(current_status),
                    )
                reviewed_explanation = st.text_area(
                    "差异说明",
                    value=(
                        ""
                        if current_direction == "一致"
                        else str(selected_result.get("explanation", ""))
                    ),
                    help="差异方向选择“一致”时，保存后该说明会自动清空。",
                )
                review_note = st.text_area(
                    "审核备注",
                    value=str(existing_review.get("note", "")),
                    placeholder="可填写修改依据、评审结论或会议纪要编号",
                )
                submitted = st.form_submit_button(
                    "保存修改并确认审核",
                    type="primary",
                    width="stretch",
                )

            if submitted:
                if save_review_decision(
                    record_id,
                    selected_param,
                    reviewed_direction,
                    reviewed_status,
                    reviewed_explanation,
                    review_note,
                ):
                    st.toast("审核结果已保存，历史版本和导出报告已更新", icon="✅")
                    st.rerun()
                else:
                    st.error("审核结果保存失败，请刷新页面后重试。")

    with st.expander(f"🕘 历史修改记录 · {len(audit_log)} 条"):
        if not audit_log:
            st.caption("暂无修改或审核记录。")
        else:
            for entry in reversed(audit_log):
                timestamp = str(entry.get("timestamp", "")).replace("T", " ")
                reviewer_name = str(entry.get("reviewer", "未知"))
                action = str(entry.get("action", "审核确认"))
                parameter = f"{entry.get('param_id', '')} · {entry.get('param_name', '')}"
                note = str(entry.get("note", "")).strip()
                with st.container(border=True):
                    header_col, action_col = st.columns([5, 1])
                    with header_col:
                        st.markdown(f"**{parameter}**")
                        st.caption(f"{timestamp} · 修改人：{reviewer_name}")
                    with action_col:
                        st.markdown(f"**{action}**")
                    st.markdown("**修改内容**")
                    st.write(audit_change_summary(entry))
                    st.markdown("**审核备注**")
                    st.write(note or "未填写")


def render_analysis_workspace(
    dfs: dict[str, pd.DataFrame],
    pipe: SpecPipeline,
    key_prefix: str,
    context_caption: str = "",
    history_ids: dict[str, str] | None = None,
) -> None:
    """单页结果工作台：导出、汇总、筛选矩阵与可视化。"""
    all_results = [
        ComparisonResult(**row)
        for data in dfs.values()
        for row in data.to_dict(orient="records")
    ]
    out_dir = Path("output")
    excel_path = save_excel(all_results, out_dir / f"{key_prefix}_差异分析报告.xlsx")
    md_path = save_markdown(all_results, out_dir / f"{key_prefix}_差异分析报告.md")

    title_col, excel_col, markdown_col = st.columns([5, 1.7, 1.7])
    with title_col:
        st.subheader("全量差异矩阵")
        if context_caption:
            st.caption(context_caption)
    with excel_col:
        st.download_button(
            "⬇️ 导出 Excel",
            data=excel_path.read_bytes(),
            file_name="SpecLens_差异分析报告.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_excel_download",
            width="stretch",
        )
    with markdown_col:
        st.download_button(
            "⬇️ 导出 Markdown",
            data=md_path.read_text(encoding="utf-8"),
            file_name="SpecLens_差异分析报告.md",
            mime="text/markdown",
            key=f"{key_prefix}_markdown_download",
            width="stretch",
        )

    if len(dfs) > 1:
        st.markdown("#### 批量风险概览")
        show_risk_ranking(risk_table(dfs, pipe))

    if len(dfs) == 1:
        selected_name, df = next(iter(dfs.items()))
    else:
        selected_name = st.selectbox(
            "选择要查看的规格书",
            list(dfs),
            format_func=lambda name: (
                f"{spec_label(name)} · 差异 {int((dfs[name]['direction'] != '一致').sum())} 项"
            ),
            key=f"{key_prefix}_document_select",
        )
        df = dfs[selected_name]

    active_record_id = (history_ids or {}).get(selected_name)
    active_record = find_history_record(active_record_id) if active_record_id else None

    spec_id = str(df["spec_id"].iloc[0])
    customer_card(spec_id, pipe.profiles.get(spec_id))
    show_metrics(df)

    filter_col, category_col = st.columns([3, 3])
    with filter_col:
        status_filter = st.segmented_control(
            "判定筛选",
            ["全部", "仅差异", "不满足", "需评估", "满足"],
            default="全部",
            key=f"{key_prefix}_status_filter",
        )
    with category_col:
        category_filter = st.multiselect(
            "参数类别",
            sorted(df["category"].unique().tolist()),
            key=f"{key_prefix}_category_filter",
        )

    filtered = df
    if status_filter == "仅差异":
        filtered = filtered[filtered["direction"] != "一致"]
    elif status_filter not in (None, "全部"):
        filtered = filtered[filtered["status"] == status_filter]
    if category_filter:
        filtered = filtered[filtered["category"].isin(category_filter)]

    if filtered.empty:
        st.info("当前筛选条件下没有参数行，请调整筛选条件。")
    else:
        margin_count = int((filtered["direction"] == "低于").sum())
        st.caption(
            f"显示 {len(filtered)} / {len(df)} 行 · "
            f"不满足 {int((filtered['status'] == '不满足').sum())} · "
            f"需评估 {int((filtered['status'] == '需评估').sum())} · "
            f"能力余量 {margin_count}"
        )
        table_data = filtered[
            [
                "param_id", "param_name", "category", "customer_value", "baseline_value",
                "unit", "direction", "status", "explanation", "evidence", "page",
                "source",
            ]
        ].copy()
        table_data.loc[table_data["direction"] == "一致", "explanation"] = ""
        if active_record:
            review_map = active_record.get("reviews", {})
            table_data["review_status"] = table_data["param_id"].map(
                lambda param_id: (
                    f"已确认 · {review_map[param_id].get('reviewer', '未知')}"
                    if param_id in review_map
                    else "待确认"
                )
            )
        styled = table_data.style.apply(
            lambda row: [
                STATUS_COLOR.get(row["status"], "")
                if column == "status"
                and (row["status"] != "满足" or row["direction"] == "低于")
                else ""
                for column in row.index
            ],
            axis=1,
        )
        st.dataframe(
            styled,
            width="stretch",
            height=480,
            hide_index=True,
            column_config={"review_status": st.column_config.TextColumn("审核状态")},
        )

    if active_record_id:
        render_review_panel(df, active_record_id, key_prefix)

    st.markdown("#### 当前文件符合性概览")
    st.caption("按全部参数的最终判定统计；能力余量属于符合项。")
    show_compliance_overview(df)

    if len(dfs) > 1:
        st.markdown("#### 跨文档差异热力图")
        st.caption("加权分 = 不满足×3 + 需评估×2 + 宽松×1；悬停可查看分类明细。")
        show_heatmap(dfs)


def history_results_frame(records: list[dict]) -> pd.DataFrame:
    """将去重后的历史分析记录整理为可聚合的参数明细。"""
    rows: list[dict] = []
    for record in records:
        fallback_vendor = next(iter(record.get("customers", [])), "未识别厂商")
        for result in record.get("results", []):
            row = dict(result)
            row["厂商"] = str(result.get("customer_name") or fallback_vendor or "未识别厂商").strip()
            row["文档"] = str(record.get("file_name", "未命名文档"))
            row["分析时间"] = str(record.get("analyzed_at", "")).replace("T", " ")
            row["分析模式"] = str(record.get("mode", "离线模式"))
            row["模型"] = str(record.get("model_name", "规则引擎"))
            row["存在差异"] = result.get("direction") != "一致"
            row["技术缺口"] = result.get("direction") == "高于"
            row["规格差异"] = result.get("direction") not in {"一致", "高于", "低于"}
            row["能力余量"] = result.get("direction") == "低于"
            rows.append(row)
    return pd.DataFrame(rows)


def aggregate_dimension(df: pd.DataFrame, dimension: str) -> pd.DataFrame:
    grouped = (
        df.groupby(dimension, dropna=False)
        .agg(
            参数总数=("param_id", "size"),
            差异点=("存在差异", "sum"),
            技术缺口=("技术缺口", "sum"),
            规格差异=("规格差异", "sum"),
            能力余量=("能力余量", "sum"),
            文档数=("文档", "nunique"),
        )
        .reset_index()
    )
    count_columns = ["参数总数", "差异点", "技术缺口", "规格差异", "能力余量", "文档数"]
    grouped[count_columns] = grouped[count_columns].astype(int)
    grouped["差异率"] = (grouped["差异点"] / grouped["参数总数"] * 100).round(1)
    grouped["技术缺口率"] = (grouped["技术缺口"] / grouped["参数总数"] * 100).round(1)
    grouped["风险分"] = grouped["技术缺口"] * 3 + grouped["规格差异"] * 2 + grouped["能力余量"]
    return grouped


def render_aggregate_dashboard(records: list[dict]) -> None:
    """跨历史文档聚合厂商和参数种类，展示差异、缺口与能力余量。"""
    unique_records = deduplicate_history_records(records)
    frame = history_results_frame(unique_records)

    title_col, back_col = st.columns([6, 1.35])
    with title_col:
        st.subheader("技术竞争力洞察 · 历史分析驾驶舱")
        st.caption(
            f"汇总 {len(records)} 条历史分析，按文档去重后计入 {len(unique_records)} 份独立规格书。"
        )
    with back_col:
        if st.button("返回当前分析", key="close_aggregate_stats", width="stretch"):
            st.session_state["show_aggregate_stats"] = False
            st.rerun()

    if frame.empty:
        st.info("暂无可统计的历史数据。完成一次 PDF 分析后，这里会自动形成全量洞察。")
        return

    st.markdown("#### 统计范围")
    vendor_col, category_col = st.columns(2)
    vendors = sorted(frame["厂商"].dropna().astype(str).unique().tolist())
    categories = sorted(frame["category"].dropna().astype(str).unique().tolist())
    with vendor_col:
        selected_vendors = st.multiselect(
            "厂商",
            vendors,
            placeholder="全部厂商",
            key="aggregate_vendor_filter",
        )
    with category_col:
        selected_categories = st.multiselect(
            "参数种类",
            categories,
            placeholder="全部参数种类",
            key="aggregate_category_filter",
        )

    filtered = frame
    if selected_vendors:
        filtered = filtered[filtered["厂商"].isin(selected_vendors)]
    if selected_categories:
        filtered = filtered[filtered["category"].isin(selected_categories)]
    if filtered.empty:
        st.info("当前厂商与参数种类组合下暂无数据，请调整筛选条件。")
        return

    total = len(filtered)
    gap_count = int(filtered["存在差异"].sum())
    hard_gap_count = int(filtered["技术缺口"].sum())
    direction_diff_count = int(filtered["规格差异"].sum())
    advantage_count = int(filtered["能力余量"].sum())
    consistent_count = int((filtered["direction"] == "一致").sum())
    metric_cols = st.columns(6)
    metric_cols[0].metric("独立文档", filtered["文档"].nunique())
    metric_cols[1].metric("覆盖厂商", filtered["厂商"].nunique())
    metric_cols[2].metric("参数总量", total)
    metric_cols[3].metric("差异点", gap_count, f"{gap_count / total:.1%}")
    metric_cols[4].metric("技术缺口", hard_gap_count)
    metric_cols[5].metric("能力余量", advantage_count)
    st.caption(
        f"其中规格差异 {direction_diff_count} 项。全部统计仅依据差异方向，不受人工审核后的满足/不满足判定影响；"
        "技术缺口指客户要求高于内部基线，能力余量指客户要求低于内部基线。"
    )

    category_stats = aggregate_dimension(filtered, "category").sort_values(
        ["差异点", "差异率"], ascending=False
    )
    vendor_stats = aggregate_dimension(filtered, "厂商").sort_values(
        ["差异点", "差异率"], ascending=False
    )

    def short_vendor_name(value: str) -> str:
        name = str(value).replace("（项目代号", " · ").replace("）", "")
        return name if len(name) <= 18 else f"{name[:17]}…"

    st.markdown("#### 差异结构概览")
    st.caption("总体结构只按高于、低于、差异和一致统计；种类与厂商使用排名条展示差异数量和占比。")
    pie_cols = st.columns(3)

    judgment_pie = pd.DataFrame(
        {
            "方向": ["高于", "低于", "差异", "一致"],
            "数量": [hard_gap_count, advantage_count, direction_diff_count, consistent_count],
        }
    )
    category_pie = category_stats[category_stats["差异点"] > 0][["category", "差异点"]].rename(
        columns={"category": "参数种类", "差异点": "数量"}
    )
    vendor_pie = vendor_stats[vendor_stats["差异点"] > 0][["厂商", "差异点"]].rename(
        columns={"差异点": "数量"}
    )

    category_pie = category_pie.sort_values("数量", ascending=False).reset_index(drop=True)
    vendor_pie = vendor_pie.sort_values("数量", ascending=False).reset_index(drop=True)

    def donut_chart(data: pd.DataFrame, label: str, colors=None) -> alt.LayerChart:
        visible = data[data["数量"] > 0].copy()
        total_value = int(visible["数量"].sum())
        color = alt.Color(
            f"{label}:N",
            title=None,
            scale=alt.Scale(scheme="tableau20"),
            legend=alt.Legend(orient="bottom", columns=2, labelLimit=150),
        )
        if colors:
            color = alt.Color(
                f"{label}:N",
                title=None,
                scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                legend=alt.Legend(orient="bottom", columns=2, labelLimit=150),
            )
        base = alt.Chart(visible).encode(
            theta=alt.Theta("数量:Q", stack=True),
            color=color,
            tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip("数量:Q", title="参数数量")],
        )
        arcs = base.mark_arc(innerRadius=53, outerRadius=86, cornerRadius=4, stroke="white", strokeWidth=1.5)
        center = alt.Chart(pd.DataFrame({"圆心": [f"{total_value} 项"]})).mark_text(
            fontSize=20, fontWeight="bold", color="#12304A"
        ).encode(
            text="圆心:N"
        )
        return (arcs + center).properties(height=260)

    def ranked_share_chart(
        data: pd.DataFrame,
        label: str,
        *,
        shorten=None,
        color: str = "#2E75B6",
    ) -> alt.LayerChart:
        visible = data[data["数量"] > 0].copy()
        total_value = int(visible["数量"].sum())
        visible["显示名称"] = visible[label].map(shorten) if shorten else visible[label].astype(str)
        visible["占比"] = visible["数量"] / total_value * 100
        visible["结果标注"] = visible.apply(
            lambda row: f'{int(row["数量"])} 项 · {row["占比"]:.1f}%', axis=1
        )
        max_value = max(float(visible["数量"].max()), 1.0)
        base = alt.Chart(visible).encode(
            x=alt.X(
                "数量:Q",
                title=None,
                scale=alt.Scale(domain=[0, max_value * 1.42], nice=False),
                axis=alt.Axis(labels=False, ticks=False, grid=False, domain=False),
            ),
            y=alt.Y(
                "显示名称:N",
                title=None,
                sort=alt.EncodingSortField(field="数量", order="descending"),
                axis=alt.Axis(labelLimit=155, labelPadding=8, domain=False, ticks=False),
            ),
            tooltip=[
                alt.Tooltip(f"{label}:N"),
                alt.Tooltip("数量:Q", title="差异数量"),
                alt.Tooltip("占比:Q", title="占比 (%)", format=".1f"),
            ],
        )
        bars = base.mark_bar(cornerRadiusEnd=6, height=22, color=color)
        labels = base.mark_text(
            align="left", baseline="middle", dx=7, fontSize=11, fontWeight="bold", color="#344054"
        ).encode(text="结果标注:N")
        return (
            (bars + labels)
            .properties(height=max(220, len(visible) * 30))
            .configure_view(strokeWidth=0)
        )

    with pie_cols[0]:
        st.markdown("**总体差异方向结构**")
        st.altair_chart(
            donut_chart(
                judgment_pie,
                "方向",
                {"高于": "#C00000", "低于": "#2E75B6", "差异": "#E6A700", "一致": "#70AD47"},
            ),
            width="stretch",
        )
        st.caption(
            f"高于 {hard_gap_count} 项 · 低于 {advantage_count} 项 · "
            f"差异 {direction_diff_count} 项 · 一致 {consistent_count} 项"
        )
    with pie_cols[1]:
        st.markdown("**差异种类构成**")
        if category_pie.empty:
            st.info("当前范围内暂无差异。")
        else:
            st.altair_chart(
                ranked_share_chart(category_pie, "参数种类", color="#4C78A8"),
                width="stretch",
            )
            st.caption(f'{category_stats.iloc[0]["category"]}最多 · {int(category_stats.iloc[0]["差异点"])} 项')
    with pie_cols[2]:
        st.markdown("**厂商差异占比**")
        if vendor_pie.empty:
            st.info("当前范围内暂无差异。")
        else:
            st.altair_chart(
                ranked_share_chart(vendor_pie, "厂商", shorten=short_vendor_name, color="#F28E2B"),
                width="stretch",
            )
            st.caption(f'{short_vendor_name(vendor_stats.iloc[0]["厂商"])}最多 · {int(vendor_stats.iloc[0]["差异点"])} 项')

    top_diff_category = category_stats.iloc[0]
    top_rate_category = category_stats.sort_values(["差异率", "差异点"], ascending=False).iloc[0]
    top_vendor = vendor_stats.iloc[0]
    top_gap_category = category_stats.sort_values(["技术缺口", "技术缺口率"], ascending=False).iloc[0]
    top_advantage_category = category_stats.sort_values(["能力余量", "差异率"], ascending=False).iloc[0]

    st.markdown("#### 关键经营洞察")
    insight_cols = st.columns(5)
    insight_cols[0].metric("差异最多种类", top_diff_category["category"], f'{int(top_diff_category["差异点"])} 项')
    insight_cols[1].metric("差异率最高种类", top_rate_category["category"], f'{top_rate_category["差异率"]:.1f}%')
    insight_cols[2].metric("差异最多厂商", short_vendor_name(top_vendor["厂商"]), f'{int(top_vendor["差异点"])} 项')
    insight_cols[3].metric("首要补强方向", top_gap_category["category"], f'{int(top_gap_category["技术缺口"])} 项缺口')
    insight_cols[4].metric(
        "优势最集中种类",
        top_advantage_category["category"] if int(top_advantage_category["能力余量"]) else "暂未识别",
        f'{int(top_advantage_category["能力余量"])} 项余量',
    )

    st.markdown("#### 参数种类差异排行")
    category_chart_data = category_stats.sort_values("差异点", ascending=True)
    category_chart = (
        alt.Chart(category_chart_data)
        .mark_bar(cornerRadiusEnd=4, color="#2E75B6")
        .encode(
            x=alt.X("差异点:Q", title="差异点数量"),
            y=alt.Y("category:N", title=None, sort=alt.EncodingSortField(field="差异点", order="descending")),
            tooltip=[
                alt.Tooltip("category:N", title="参数种类"),
                alt.Tooltip("参数总数:Q", title="参数总数"),
                alt.Tooltip("差异点:Q", title="差异点"),
                alt.Tooltip("技术缺口:Q", title="技术缺口"),
                alt.Tooltip("规格差异:Q", title="规格差异"),
                alt.Tooltip("能力余量:Q", title="能力余量"),
            ],
        )
        .properties(height=max(240, len(category_stats) * 34))
    )
    st.altair_chart(category_chart, width="stretch")
    st.dataframe(
        category_stats.rename(columns={"category": "参数种类"})[
            ["参数种类", "参数总数", "差异点", "技术缺口", "技术缺口率", "规格差异", "能力余量", "风险分"]
        ],
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### 厂商差异排行")
    st.caption("条形长度代表差异数量，颜色深浅代表差异率；悬停可查看高于、低于和规格差异。")
    vendor_chart_data = vendor_stats.copy()
    vendor_chart_data["厂商简称"] = vendor_chart_data["厂商"].map(short_vendor_name)
    vendor_chart_data["结果标注"] = vendor_chart_data.apply(
        lambda row: f'{int(row["差异点"])} 项 · {row["差异率"]:.1f}%', axis=1
    )
    vendor_base = alt.Chart(vendor_chart_data).encode(
        x=alt.X("差异点:Q", title="差异点数量", scale=alt.Scale(zero=True, nice=True)),
        y=alt.Y(
            "厂商简称:N",
            title=None,
            sort=alt.EncodingSortField(field="差异点", order="descending"),
            axis=alt.Axis(labelLimit=190),
        ),
        tooltip=[
            alt.Tooltip("厂商:N"),
            alt.Tooltip("参数总数:Q"),
            alt.Tooltip("差异点:Q"),
            alt.Tooltip("差异率:Q", format=".1f"),
            alt.Tooltip("技术缺口:Q"),
            alt.Tooltip("规格差异:Q"),
            alt.Tooltip("能力余量:Q"),
        ],
    )
    vendor_bars = vendor_base.mark_bar(cornerRadiusEnd=5, height=22).encode(
        color=alt.Color(
            "差异率:Q",
            legend=None,
            scale=alt.Scale(scheme="orangered", domain=[0, 100]),
        )
    )
    vendor_labels = vendor_base.mark_text(
        align="left", baseline="middle", dx=8, fontSize=12, fontWeight="bold", color="#344054"
    ).encode(
        text="结果标注:N"
    )
    vendor_chart = (
        (vendor_bars + vendor_labels)
        .properties(height=max(260, len(vendor_stats) * 38))
        .configure_view(strokeWidth=0)
        .configure_axis(gridColor="#E9EEF3", domain=False)
    )
    st.altair_chart(vendor_chart, width="stretch")
    st.dataframe(
        vendor_stats[["厂商", "文档数", "参数总数", "差异点", "差异率", "技术缺口", "规格差异", "能力余量", "风险分"]],
        width="stretch",
        hide_index=True,
        column_config={"差异率": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)},
    )

    st.markdown("#### 重点差异组合")
    st.caption("直接展示“哪个厂商的哪个技术种类”问题最集中；优先排列技术缺口多、差异比例高的组合。")
    cross = (
        filtered.groupby(["厂商", "category"], dropna=False)
        .agg(
            参数总数=("param_id", "size"),
            差异点=("存在差异", "sum"),
            技术缺口=("技术缺口", "sum"),
            规格差异=("规格差异", "sum"),
            能力余量=("能力余量", "sum"),
        )
        .reset_index()
    )
    cross[["参数总数", "差异点", "技术缺口", "规格差异", "能力余量"]] = cross[
        ["参数总数", "差异点", "技术缺口", "规格差异", "能力余量"]
    ].astype(int)
    cross["差异率"] = cross["差异点"] / cross["参数总数"] * 100
    cross["技术缺口率"] = cross["技术缺口"] / cross["参数总数"] * 100
    cross["风险分"] = cross["技术缺口"] * 3 + cross["规格差异"] * 2 + cross["能力余量"]
    cross = cross[cross["差异点"] > 0].sort_values(
        ["风险分", "差异率", "差异点"], ascending=False
    )

    if cross.empty:
        st.success("当前筛选范围内，各厂商与技术种类均未发现差异。")
    else:
        focus = cross.head(15).copy()
        focus["组合"] = focus.apply(
            lambda row: f'{short_vendor_name(row["厂商"])} · {row["category"]}', axis=1
        )
        focus["结果标注"] = focus.apply(
            lambda row: f'{int(row["差异点"])}/{int(row["参数总数"])} 项 · {row["差异率"]:.0f}%', axis=1
        )
        focus_base = alt.Chart(focus).encode(
            x=alt.X("差异点:Q", title="差异点数量", scale=alt.Scale(zero=True, nice=True)),
            y=alt.Y(
                "组合:N",
                title=None,
                sort=alt.EncodingSortField(field="风险分", order="descending"),
                axis=alt.Axis(labelLimit=260),
            ),
            tooltip=[
                alt.Tooltip("厂商:N"),
                alt.Tooltip("category:N", title="技术种类"),
                alt.Tooltip("参数总数:Q"),
                alt.Tooltip("差异点:Q"),
                alt.Tooltip("差异率:Q", format=".1f"),
                alt.Tooltip("技术缺口:Q"),
                alt.Tooltip("规格差异:Q"),
                alt.Tooltip("能力余量:Q"),
            ],
        )
        focus_bars = focus_base.mark_bar(cornerRadiusEnd=5, height=20, color="#E8743B")
        focus_labels = focus_base.mark_text(
            align="left", baseline="middle", dx=8, fontSize=11, color="#344054"
        ).encode(text="结果标注:N")
        focus_chart = (
            (focus_bars + focus_labels)
            .properties(height=max(300, len(focus) * 32))
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor="#E9EEF3", domain=False)
        )
        st.altair_chart(focus_chart, width="stretch")

        st.dataframe(
            cross.rename(columns={"category": "技术种类"})[
                ["厂商", "技术种类", "参数总数", "差异点", "差异率", "技术缺口", "规格差异", "能力余量", "风险分"]
            ],
            width="stretch",
            hide_index=True,
            column_config={"差异率": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)},
        )

    with st.expander("查看聚合差异明细"):
        detail = filtered[filtered["存在差异"]][
            ["厂商", "文档", "category", "param_id", "param_name", "customer_value", "baseline_value", "unit", "direction", "status", "explanation"]
        ].rename(
            columns={
                "category": "参数种类",
                "param_id": "参数编号",
                "param_name": "参数名称",
                "customer_value": "客户要求",
                "baseline_value": "内部基线",
                "unit": "单位",
                "direction": "差异方向",
                "status": "结论",
                "explanation": "说明",
            }
        )
        st.dataframe(detail, width="stretch", height=420, hide_index=True)


# ---------------- Hero 横幅 ----------------
st.markdown(
    """
    <style>
    .hero{background:linear-gradient(135deg,#1F4E79 0%,#2E75B6 60%,#41A0D8 100%);
          border-radius:14px;padding:28px 34px;color:#FFFFFF;margin-bottom:10px}
    .hero h1{margin:0;font-size:28px;line-height:1.3}
    .hero .tagline{margin:12px 0 5px;font-size:19px;font-weight:650;letter-spacing:.2px}
    .hero .intro{margin:0;max-width:780px;font-size:15px;line-height:1.75;opacity:.92}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.65rem}
    </style>
    <div class="hero">
      <h1>SpecLens · 规格书智能解析与差异分析</h1>
      <p class="tagline">让复杂规格快速变成清晰、可信、可行动的工程洞察</p>
      <p class="intro">从参数抽取、基线核验到风险定位，一站式读懂客户需求。让每一次技术评审更高效，让每一项工程决策都有据可依。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------- 侧边栏 ----------------
model_configs = load_model_configs()
env_client = LLMClient()
if env_client.available:
    model_configs = [
        {
            "name": "环境变量默认模型",
            "base_url": env_client.base_url,
            "model_id": env_client.model,
            "api_key": env_client.api_key,
            "temperature": env_client.temperature,
            "timeout": int(env_client.timeout),
            "source": "env",
        }
    ] + model_configs

active_model: dict | None = None
mode_choice = "offline"
with st.sidebar:
    st.title("🔍 SpecLens")
    st.caption("客户技术规格书 AI 智能解析与差异分析")

    st.markdown("#### 分析模式")
    mode_choice = st.segmented_control(
        "分析模式",
        ["offline", "model"],
        format_func={"offline": "⚡ 离线模式", "model": "✨ 模型分析"}.get,
        default="offline",
        label_visibility="collapsed",
        key="analysis_mode_choice",
    ) or "offline"

    if mode_choice == "offline":
        st.markdown(
            '<div style="background:#EEF6FF;border-left:4px solid #2E75B6;border-radius:7px;'
            'padding:8px 12px;font-size:13px;color:#1F4E79">'
            '<b>离线规则分析</b><br>数据不外发，无需 API Key，适合快速比对。</div>',
            unsafe_allow_html=True,
        )
    else:
        if model_configs:
            model_names = [str(config.get("name", "未命名模型")) for config in model_configs]
            selected_model_name = st.selectbox("选择已配置模型", model_names, key="selected_model_name")
            active_model = next((config for config in model_configs if config.get("name") == selected_model_name), None)
        else:
            st.warning("还没有模型配置，请先在下方添加。")

        with st.expander("➕ 添加或更新模型配置", expanded=not model_configs):
            with st.form("model_config_form", clear_on_submit=True):
                config_name = st.text_input("自定义名称", placeholder="例如：公司内部 Qwen")
                config_base_url = st.text_input(
                    "模型地址",
                    placeholder="https://api.example.com/v1",
                    help="填写 OpenAI 兼容接口的 Base URL",
                )
                config_model_id = st.text_input("模型标识", placeholder="例如：qwen-plus / gpt-4.1-mini")
                config_api_key = st.text_input("API Key", type="password", placeholder="sk-…")
                col_temp, col_timeout = st.columns(2)
                config_temperature = col_temp.number_input(
                    "温度", min_value=0.0, max_value=2.0, value=0.1, step=0.1
                )
                config_timeout = col_timeout.number_input(
                    "超时(秒)", min_value=10, max_value=600, value=120, step=10
                )
                save_config = st.form_submit_button("保存模型配置", type="primary", width="stretch")

            if save_config:
                errors = []
                if not config_name.strip():
                    errors.append("请填写自定义名称")
                if not is_valid_base_url(config_base_url):
                    errors.append("请填写有效的 http/https 模型地址")
                if not config_model_id.strip():
                    errors.append("请填写模型标识")
                if not config_api_key.strip():
                    errors.append("请填写 API Key")
                if errors:
                    st.error("；".join(errors))
                else:
                    save_model_config(
                        {
                            "name": config_name.strip(),
                            "base_url": config_base_url.strip().rstrip("/"),
                            "model_id": config_model_id.strip(),
                            "api_key": config_api_key.strip(),
                            "temperature": float(config_temperature),
                            "timeout": int(config_timeout),
                            "source": "local",
                        }
                    )
                    st.toast("模型配置已保存", icon="✅")
                    st.rerun()

            st.caption("API Key 仅保存在本机 `.speclens_state/`，不会写入 Git。")

    st.divider()
    st.markdown("#### 历史文档")
    all_history_records = load_history_records()
    unique_history_records = deduplicate_history_records(all_history_records)
    history_records = list(unique_history_records)
    history_query = st.text_input(
        "搜索历史记录",
        placeholder="搜索文件名、客户或模型",
        label_visibility="collapsed",
        key="history_query",
    )
    if history_query.strip():
        keyword = history_query.strip().casefold()
        history_records = [
            record
            for record in history_records
            if keyword
            in " ".join(
                [
                    str(record.get("file_name", "")),
                    " ".join(record.get("customers", [])),
                    str(record.get("model_name", "")),
                ]
            ).casefold()
        ]

    if history_records:
        history_by_id = {record["id"]: record for record in history_records}
        history_id = st.selectbox(
            "历史分析记录",
            list(history_by_id),
            format_func=lambda record_id: (
                f'{history_by_id[record_id].get("file_name", "未命名文档")} · '
                f'{str(history_by_id[record_id].get("updated_at") or history_by_id[record_id].get("analyzed_at", "")).replace("T", " ")[5:16]}'
            ),
            label_visibility="collapsed",
            key="history_record_select",
        )
        selected_summary = history_by_id[history_id].get("summary", {})
        st.caption(
            f'差异 {selected_summary.get("gap_count", 0)} 项 · '
            f'已审核 {len(history_by_id[history_id].get("reviews", {}))} 项 · '
            f'{history_by_id[history_id].get("mode", "离线模式")} · '
            f'{history_by_id[history_id].get("model_name", "规则引擎")}'
        )
        if st.button("查看历史分析", width="stretch"):
            st.session_state["selected_history_id"] = history_id
            st.session_state["show_aggregate_stats"] = False
            st.rerun()
    else:
        st.caption("暂无匹配记录。完成一次 PDF 分析后会自动出现在这里。")

    if st.button(
        "📊 技术竞争力洞察",
        width="stretch",
        type="primary" if st.session_state.get("show_aggregate_stats") else "secondary",
        disabled=not all_history_records,
    ):
        st.session_state["show_aggregate_stats"] = True
        st.session_state.pop("selected_history_id", None)
        st.rerun()
    if all_history_records:
        independent_count = len(unique_history_records)
        st.caption(f"汇总全部历史 · {independent_count} 份独立文档")

    with st.expander("处理流程"):
        st.markdown(
            """
            1. PDF 表格/章节解析
            2. 清单引导式参数抽取
            3. 双通道交叉校验与置信度评分
            4. 确定性差异对比
            5. 结构化报告输出
            """
        )
    st.divider()
    st.caption("扬帆队 · 星宇车灯黑客松 AI 创造力大赛")

mode = "hybrid" if mode_choice == "model" and active_model else "offline"
model_name = str(active_model.get("name", "")) if active_model else "规则引擎"
model_params = pipeline_args(active_model)
model_mode_blocked = mode_choice == "model" and active_model is None

# ---------------- 主界面 ----------------
show_aggregate_stats = bool(st.session_state.get("show_aggregate_stats"))
uploaded = None
if not show_aggregate_stats:
    st.subheader("选择要解析的客户规格书")
    uploaded = st.file_uploader(
        "PDF 规格书",
        type=["pdf"],
        accept_multiple_files=True,
        help="支持一次选择多份 PDF；解析完成后，差异矩阵会直接显示在下方。",
    )

selected_history = find_history_record(st.session_state.get("selected_history_id"))

if show_aggregate_stats:
    render_aggregate_dashboard(load_history_records())

elif selected_history:
    history_file_name = selected_history.get("file_name", "未命名文档")
    history_df = to_df(selected_history["results"])
    history_dfs = {history_file_name: history_df}
    history_pipe = get_pipeline("offline")
    history_spec_id = str(history_df["spec_id"].iloc[0]) if not history_df.empty else "历史分析"
    history_profile = history_pipe.profiles.get(history_spec_id, {})
    history_customer = str(
        history_profile.get("customer")
        or (history_df["customer_name"].iloc[0] if "customer_name" in history_df and not history_df.empty else "")
        or "未识别客户"
    )
    history_product = str(history_profile.get("product") or "客户规格书")
    history_time = str(selected_history.get("analyzed_at", "")).replace("T", " ")
    history_updated_time = str(
        selected_history.get("updated_at") or selected_history.get("analyzed_at", "")
    ).replace("T", " ")
    history_reviewed_count = len(selected_history.get("reviews", {}))

    with st.container(border=True):
        history_header, history_close = st.columns([5, 1.45], vertical_alignment="center")
        with history_header:
            st.caption("历史分析记录")
            st.markdown(f"### {history_spec_id} · {history_customer}")
            st.markdown(f"**{history_product}**")
            st.caption(
                f"分析时间：{history_time}　·　"
                f"最后更新：{history_updated_time}　·　"
                f"已审核：{history_reviewed_count}/{len(history_df)}　·　"
                f"{selected_history.get('mode', '离线模式')}　·　"
                f"{selected_history.get('model_name', '规则引擎')}"
            )
            st.caption(f"源文件：{history_file_name}")
        with history_close:
            if st.button("← 返回当前分析", width="stretch"):
                st.session_state.pop("selected_history_id", None)
                st.rerun()

    render_analysis_workspace(
        history_dfs,
        history_pipe,
        key_prefix=f'history_{selected_history["id"]}',
        context_caption=(
            f'分析时间：{selected_history.get("analyzed_at", "").replace("T", " ")} · '
            f'最后更新：{history_updated_time} · '
            f'模式：{selected_history.get("mode", "离线模式")} · '
            f'模型：{selected_history.get("model_name", "规则引擎")}'
        ),
        history_ids={history_file_name: selected_history["id"]},
    )

elif uploaded:
    if model_mode_blocked:
        st.error("模型分析模式尚未配置模型。请先在左侧添加模型地址、模型标识和 API Key。")
        st.stop()

    parsed: dict[str, list[dict]] = {}
    failed: list[str] = []
    saved_history_ids: list[str] = []
    history_ids_by_file: dict[str, str] = {}
    progress = st.progress(0.0, text="解析中…（PDF 解析 → 参数抽取 → 基线对比）")
    for index, uploaded_file in enumerate(uploaded):
        file_bytes = uploaded_file.getvalue()
        results = run_uploaded(uploaded_file.name, file_bytes, mode, *model_params)
        if results:
            history_id = save_history_record(
                uploaded_file.name,
                file_bytes,
                results,
                "模型分析" if mode == "hybrid" else "离线模式",
                model_name,
            )
            saved_history_ids.append(history_id)
            history_ids_by_file[uploaded_file.name] = history_id
            stored_record = find_history_record(history_id)
            parsed[uploaded_file.name] = (
                stored_record.get("results", results) if stored_record else results
            )
        else:
            failed.append(uploaded_file.name)
        progress.progress(
            (index + 1) / len(uploaded),
            text=f"已解析 {index + 1}/{len(uploaded)}：{uploaded_file.name}",
        )
    progress.empty()

    history_refresh_signature = "|".join(saved_history_ids)
    if saved_history_ids and st.session_state.get("history_refresh_signature") != history_refresh_signature:
        st.session_state["history_refresh_signature"] = history_refresh_signature
        st.rerun()

    if failed:
        st.warning(
            f"未识别到标准参数表（需含 OPT/ELE/ENV/MEC/EMC/REG/REL-xx 编号行）：{', '.join(failed)}"
        )
    if not parsed:
        st.stop()

    current_dfs = {name: to_df(results) for name, results in parsed.items()}
    current_pipe = get_pipeline(mode, *model_params)
    render_analysis_workspace(
        current_dfs,
        current_pipe,
        key_prefix="current",
        context_caption=(
            f"已解析 {len(current_dfs)} 份文档 · "
            f"{'模型分析：' + model_name if mode == 'hybrid' else '离线规则分析'}"
        ),
        history_ids=history_ids_by_file,
    )

else:
    st.info("请在上方选择一份或多份 PDF。解析完成后，全量差异矩阵和报告导出按钮会直接显示在这里。")
