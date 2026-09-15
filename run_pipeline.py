"""批量解析全部规格书 → 差异对比 → 输出 JSON / Excel / Markdown 报告。

用法：
    python run_pipeline.py                    # 全量 10 份 PDF（自动选择通道）
    python run_pipeline.py --mode offline     # 离线规则通道（无需 API Key）
    python run_pipeline.py --pdf data/pdf/SPEC-001_xxx.pdf   # 单份
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from speclens.evaluator import evaluate
from speclens.pipeline import DATA_DIR, CUSTOMER_CSV, SpecPipeline
from speclens.reporter import save_excel, save_markdown

OUT_DIR = Path(__file__).resolve().parent / "output"


def main() -> None:
    ap = argparse.ArgumentParser(description="SpecLens 规格书解析与差异分析")
    ap.add_argument("--mode", choices=["hybrid", "llm", "offline"], default="hybrid",
                    help="hybrid=LLM+规则交叉校验（默认）/ llm=纯LLM / offline=纯规则")
    ap.add_argument("--pdf", type=str, default="", help="只处理单份 PDF（默认处理 data/pdf 全部）")
    args = ap.parse_args()

    t0 = time.time()
    pipe = SpecPipeline(mode=args.mode)
    channel = "LLM+规则交叉校验" if pipe.extractor.llm else "离线规则通道（未配置 LLM_API_KEY）"
    print(f"[SpecLens] 抽取通道：{channel}")

    results = pipe.run_pdf(args.pdf) if args.pdf else pipe.run_dir(DATA_DIR / "pdf")
    print(f"[SpecLens] 解析 {results and len({r.spec_id for r in results})} 份规格书 / "
          f"{len(results)} 条参数，用时 {time.time() - t0:.1f}s")

    OUT_DIR.mkdir(exist_ok=True)
    SpecPipeline.save_json(results, OUT_DIR / "results.json")
    excel = save_excel(results, OUT_DIR / "差异分析报告.xlsx")
    md = save_markdown(results, OUT_DIR / "差异分析报告.md")
    print(f"[SpecLens] 报告已生成：\n  {excel}\n  {md}")

    report = evaluate(results, CUSTOMER_CSV)
    print("\n===== 效果自测（对照官方标注 510 行）=====")
    for line in report.as_lines():
        print("  " + line)


if __name__ == "__main__":
    main()
