"""记忆有没有用？——先用能拿到的数据做一次诚实的对照。

    python scripts/memory_eval.py                 # 观察性对照
    python scripts/memory_eval.py --paired        # 只做配对门槛检查
    python scripts/memory_eval.py --paired --live # 真跑 on/off 配对（花钱）

配对回放只重跑**决策层**（交易员 → 三方风控 → 风控负责人），把分析、辩论与
研究主管的结论冻结在当时的 payload 上；记忆只注入决策层，所以这足以回答
"记忆有没有改变判断、有没有帮上忙"。

这不是 P2-5 的完整回放引擎：它不重跑分析师，也不能回放没有落过上下文的 run。
样本门槛：≥30 条上下文 + ≥30 条已判定 + ≥30 条可配对（有 d20 收益）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import memory  # noqa: E402
from app.agents import replay  # noqa: E402
from app.db import connection, init_db  # noqa: E402

MIN_CONTEXTS = 30
MIN_JUDGED = 30


def _cohort(memory_hits: int) -> str:
    return "有历史结论" if memory_hits > 0 else "无历史结论"


def collect() -> dict:
    memory.sync()          # 先把还没索引的结论补进来，否则统计的是空索引
    with connection() as conn:
        rows = conn.execute(
            "SELECT r.id, r.symbol, r.context_json, r.verdict, "
            "v.outcome, v.d20_return "
            "FROM agent_runs r LEFT JOIN agent_reviews v ON v.run_id = r.id "
            "WHERE r.status IN ('ok', 'partial') ORDER BY r.id"
        ).fetchall()

    groups: dict[str, dict] = {}
    for row in rows:
        context = json.loads(row["context_json"]) if row["context_json"] else {}
        hits = len(context.get("memory") or [])
        key = _cohort(hits)
        bucket = groups.setdefault(
            key, {"runs": 0, "judged": 0, "correct": 0, "returns": [], "memory_hits": 0}
        )
        bucket["runs"] += 1
        bucket["memory_hits"] += hits
        outcome = row["outcome"] or ""
        if "正确" in outcome or "错误" in outcome:
            bucket["judged"] += 1
            if "正确" in outcome:
                bucket["correct"] += 1
        if row["d20_return"] is not None:
            bucket["returns"].append(float(row["d20_return"]))
    return {"groups": groups, "total": len(rows), "index": memory.stats()}


def preflight() -> dict:
    with connection() as conn:
        contexts = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE status IN ('ok', 'partial') "
            "AND context_json IS NOT NULL AND as_of IS NOT NULL"
        ).fetchone()[0]
        judged = conn.execute(
            "SELECT COUNT(*) FROM agent_reviews WHERE outcome IS NOT NULL "
            "AND d20_return IS NOT NULL"
        ).fetchone()[0]
        pairable = conn.execute(
            "SELECT COUNT(*) FROM agent_runs r JOIN agent_reviews v ON v.run_id = r.id "
            "WHERE r.status IN ('ok', 'partial') AND r.context_json IS NOT NULL "
            "AND r.as_of IS NOT NULL AND v.d20_return IS NOT NULL"
        ).fetchone()[0]
    return {
        "contexts": int(contexts or 0),
        "judged": int(judged or 0),
        "pairable": int(pairable or 0),
        "min_contexts": MIN_CONTEXTS,
        "min_judged": MIN_JUDGED,
    }


def paired_report(payload: dict) -> None:
    summary = payload["summary"]
    print(f"配对样本：{summary['n']} 条｜调用 {payload['calls'].get('calls', 0)} 次\n")
    print(f"{'指标':<10}{'均值':>10}{'95% 下界':>12}{'95% 上界':>12}{'n':>6}")
    for key, label in (("brier", "ΔBrier"), ("excess", "Δ超额")):
        block = summary[key]
        if block["mean"] is None:
            print(f"{label:<10}{'—':>10}{'—':>12}{'—':>12}{0:>6}")
            continue
        print(
            f"{label:<10}{block['mean']:>10.4f}{block['low']:>12.4f}"
            f"{block['high']:>12.4f}{block['n']:>6}"
        )
    print(
        "\nΔBrier = on - off（负值表示记忆更好）；"
        "Δ超额 = (仓位_on - 仓位_off) × T+20 超额（正值表示记忆更好）。"
    )
    print(f"方向命中：有记忆 {summary['on_correct']} / 无记忆 {summary['off_correct']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="记忆检索的观察性 / 配对评测")
    parser.add_argument("--json", help="把完整结果写到文件")
    parser.add_argument("--paired", action="store_true", help="检查配对门槛")
    parser.add_argument("--live", action="store_true", help="真的调用模型跑配对（花钱）")
    parser.add_argument("--limit", type=int, default=MIN_CONTEXTS)
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    init_db()

    if args.paired:
        gate = preflight()
        print(
            f"上下文 {gate['contexts']}/{gate['min_contexts']}｜"
            f"已判定 {gate['judged']}/{gate['min_judged']}｜"
            f"可配对 {gate['pairable']}"
        )
        enough = (
            gate["contexts"] >= MIN_CONTEXTS
            and gate["judged"] >= MIN_JUDGED
            and gate["pairable"] >= MIN_CONTEXTS
        )
        if not enough:
            print("样本不足：不跑配对，也不下结论。")
            return 0
        if not args.live:
            print(
                f"门槛已过。加 --live 会跑 {gate['pairable']} 条 × on/off × 5 次调用"
                f"（约 {gate['pairable'] * 10} 次模型调用）。"
            )
            return 0
        payload = asyncio.run(replay.run_paired(limit=args.limit, model=args.model))
        paired_report(payload)
        if args.json:
            Path(args.json).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"完整结果已写入 {args.json}")
        return 0

    payload = collect()
    groups = payload["groups"]
    print(f"索引：{payload['index']['indexed']} 条（推演 {payload['index']['runs']}）｜"
          f"共 {payload['total']} 次推演\n")

    if not groups:
        print("还没有可对照的推演。")
        return 0

    print(f"{'分组':<12}{'推演':>6}{'已判定':>8}{'正确':>6}{'命中率':>8}{'平均T+20':>10}{'累计注入':>10}")
    for key in ("有历史结论", "无历史结论"):
        bucket = groups.get(key)
        if not bucket:
            continue
        rate = f"{bucket['correct'] / bucket['judged'] * 100:.0f}%" if bucket["judged"] else "—"
        avg = (
            f"{sum(bucket['returns']) / len(bucket['returns']):+.2f}%"
            if bucket["returns"]
            else "—"
        )
        print(
            f"{key:<12}{bucket['runs']:>6}{bucket['judged']:>8}{bucket['correct']:>6}"
            f"{rate:>8}{avg:>10}{bucket['memory_hits']:>10}"
        )

    print(
        "\n注意：这是观察性对照，不是因果结论——两组不是随机分配的，"
        "而且判定要等 T+20 走完。配对实验见 --paired。"
    )
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完整结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
