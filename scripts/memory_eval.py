"""记忆有没有用？——先用能拿到的数据做一次诚实的对照。

    python scripts/memory_eval.py
    python scripts/memory_eval.py --json out.json

这不是配对回放（那需要 P2-5 的回放引擎，且要 30+ 条样本）。在没有回放引擎之前，
这里只做**观察性对照**：把用到了历史结论的推演与没用到分成两组，比较复盘判定。

必须说清两件事：
1. 分组不是随机的——有记忆的推演发生在记忆上线之后，市场环境可能不同；
2. 结论要等 T+20 走完，现在多数是「待验证」。

所以脚本末尾会明确打印「这不是因果结论」。等样本够了、回放引擎上线，再换成配对设计。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import memory  # noqa: E402
from app.db import connection, init_db  # noqa: E402


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="记忆检索的观察性对照")
    parser.add_argument("--json", help="把完整结果写到文件")
    args = parser.parse_args(argv)

    init_db()
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
        "而且判定要等 T+20 走完。样本 ≥30 条、回放引擎（P2-5）上线后再做配对实验。"
    )
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完整结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
