"""策略周期分类：短线 / 中线 / 长线，各自的参数与晋级门槛。

为什么必须分开：同一个信号在不同调仓周期下测的是**不同的策略**。2026-11-28 实测，
同一年份、同一条件：

* 量价齐升：20 日调仓 −65.2%（胜率 42%），5 日调仓 −47.9%；
* 涨停回踩：20 日调仓 −51.6%，3 日调仓 −79.0%（换手翻倍，费用吃掉一切）。

所以"用一套 20 日参数给所有策略排名"既不公平也失真：短周期信号的半衰期只有几天，
把它持有 20 天，测的是"追高后一个月"的另一种效应。门槛也必须分组——短线看胜率与
盈亏比和费用拖累，长线看年化超额与回撤。
"""

from __future__ import annotations

from typing import Any, Optional

HORIZONS = ("short", "mid", "long")
LABELS = {
    "short": "短线（≤5 日）",
    "mid": "中线（5–20 日）",
    "long": "长线（20–60 日）",
}

# 每类的默认执行参数（策略自己的 params 优先）
PARAMS = {
    "short": {"rebalance": 3, "top_n": 5},
    "mid": {"rebalance": 10, "top_n": 8},
    "long": {"rebalance": 20, "top_n": 10},
}

# 晋级门槛：短周期看"交易够多、胜率、盈亏比、费用别太狠"；长周期看"超额、回撤、期数"
GATES: dict[str, list[dict[str, Any]]] = {
    "short": [
        {"key": "trades", "op": ">=", "value": 200, "label": "交易笔数 ≥200"},
        {"key": "hit_rate_pct", "op": ">=", "value": 45, "label": "胜率 ≥45%"},
        {"key": "win_loss_ratio", "op": ">=", "value": 1.3, "label": "盈亏比 ≥1.3"},
        {"key": "fee_drag_pct", "op": "<=", "value": 25, "label": "年化费用拖累 ≤25%"},
        {"key": "max_drawdown_pct", "op": ">=", "value": -35, "label": "最大回撤 ≥-35%"},
    ],
    "mid": [
        {"key": "trades", "op": ">=", "value": 60, "label": "交易笔数 ≥60"},
        {"key": "excess_cagr_pct", "op": ">", "value": 0, "label": "超额年化 >0"},
        {"key": "max_drawdown_pct", "op": ">=", "value": -30, "label": "最大回撤 ≥-30%"},
        {"key": "hit_rate_pct", "op": ">=", "value": 45, "label": "胜率 ≥45%"},
    ],
    "long": [
        # 1 年 / 20 日调仓最多只有 12 期，要求 12 等于"一期都不许跳"；按 2 年窗口
        # （24 期）校准后取一半并允许跳 2 期。2 年窗口下仍应 ≥20。
        {"key": "periods", "op": ">=", "value": 10, "label": "期数 ≥10（1 年窗口）"},
        {"key": "excess_cagr_pct", "op": ">", "value": 0, "label": "超额年化 >0"},
        {"key": "max_drawdown_pct", "op": ">=", "value": -25, "label": "最大回撤 ≥-25%"},
        {"key": "hit_rate_pct", "op": ">=", "value": 50, "label": "胜率 ≥50%"},
    ],
}

# 排名用的主指标（组内）
RANK_FIELD = {
    "short": "excess_cagr_pct",
    "mid": "excess_cagr_pct",
    "long": "excess_cagr_pct",
}


def normalise(value: str) -> str:
    text = (value or "").strip().lower()
    if text in HORIZONS:
        return text
    if text in {"短线", "短期"}:
        return "short"
    if text in {"中线", "中期"}:
        return "mid"
    if text in {"长线", "长期"}:
        return "long"
    return "long"          # 没标注的按长线处理：调仓最慢、门槛最松


def params_for(horizon: str, own: Optional[dict[str, Any]] = None) -> dict[str, int]:
    base = dict(PARAMS.get(normalise(horizon), PARAMS["long"]))
    for key in ("rebalance", "top_n"):
        value = (own or {}).get(key)
        if value:
            base[key] = int(value)
    return base


def _check(metrics: dict[str, Any], gate: dict[str, Any]) -> bool:
    value = metrics.get(gate["key"])
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number >= float(gate["value"]) if gate["op"] in {">=", ">"} else number <= float(gate["value"])


def gate_check(horizon: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """返回 ``{"passed": bool, "checks": [...], "failures": [...]}``。"""
    gates = GATES.get(normalise(horizon), GATES["long"])
    checks = [
        {
            "label": gate["label"],
            "key": gate["key"],
            "value": metrics.get(gate["key"]),
            "ok": _check(metrics, gate),
        }
        for gate in gates
    ]
    failures = [item["label"] for item in checks if not item["ok"]]
    return {"passed": not failures, "checks": checks, "failures": failures}
