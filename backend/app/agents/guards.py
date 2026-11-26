"""Deterministic guards on the trader and risk-manager output.

The model may be wrong about the market — that is what the review loop is for. It
may not produce a decision that breaks arithmetic or A-share mechanics: a target
below the current price, a stop above it, a negative position, "批准" with zero
size, or a risk manager handing out more size than the trader asked for.

These checks are pure functions so a regression suite can pin them down without a
model in the loop; :func:`app.agents.runner.build_report` prints whatever they
find as 「决策体检」 instead of silently shipping a broken call.
"""

from __future__ import annotations

from typing import Any, Optional

POSITION_MAX = 100.0
PRICE_TOLERANCE = 0.01        # 1 分钱以内的四舍五入不算错
SIZE_TOLERANCE = 10.0         # 风控负责人比交易员多给 10 个百分点以内视为调整


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check_proposal(
    proposal: dict[str, Any], last_close: Optional[float] = None
) -> list[str]:
    """Arithmetic and mechanical checks on the trader's draft."""
    issues: list[str] = []
    if not isinstance(proposal, dict) or not proposal:
        return ["交易员草案缺失"]

    action = str(proposal.get("action") or "")
    position = _num(proposal.get("position_pct"))
    if position is None:
        issues.append("仓位缺失或不是数字")
    elif position < 0:
        issues.append(f"仓位为负（{position:g}%）")
    elif position > POSITION_MAX:
        issues.append(f"仓位超过 100%（{position:g}%）")

    target = _num(proposal.get("target_price"))
    stop = _num(proposal.get("stop_price"))
    if last_close:
        if target is not None and target <= last_close * (1 - PRICE_TOLERANCE):
            issues.append(f"目标价 {target:g} 低于现价 {last_close:g}")
        if stop is not None and stop >= last_close * (1 + PRICE_TOLERANCE):
            issues.append(f"止损价 {stop:g} 高于现价 {last_close:g}")
    if target is not None and stop is not None and target <= stop:
        issues.append("目标价不高于止损价（赔率无意义）")

    if action in {"买入", "加仓"} and (position or 0) <= 0:
        issues.append("动作是买入/加仓，仓位却是 0")
    if action == "观望" and (position or 0) >= 50:
        issues.append("观望却给了 50% 以上仓位")
    return issues


def sanitize(
    proposal: dict[str, Any],
    verdict: dict[str, Any],
    last_close: Optional[float] = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """修正自相矛盾的字段，返回 ``(提案, 裁定, 修正说明)``。

    检测不够：一个 120% 的仓位如果只是被"提示"，下游照样会用它建仓。这里做最小
    必要的改写——钳制仓位、把非法价格降级为"数据不足"、让裁定与仓位一致——并且
    把每一处改动写进报告，不静默修改。
    """
    fixes: list[str] = []
    proposal = dict(proposal or {})
    verdict = dict(verdict or {})

    position = _num(proposal.get("position_pct"))
    if position is None:
        proposal["position_pct"] = 0
        fixes.append("仓位缺失 → 0%")
    elif position < 0 or position > POSITION_MAX:
        clamped = max(0.0, min(POSITION_MAX, position))
        proposal["position_pct"] = clamped
        fixes.append(f"仓位 {position:g}% 越界 → {clamped:g}%")
    position = _num(proposal.get("position_pct")) or 0.0

    target = _num(proposal.get("target_price"))
    stop = _num(proposal.get("stop_price"))
    bad_target = bool(last_close) and target is not None and target <= last_close * (1 - PRICE_TOLERANCE)
    bad_stop = bool(last_close) and stop is not None and stop >= last_close * (1 + PRICE_TOLERANCE)
    crossed = target is not None and stop is not None and target <= stop
    if bad_target or bad_stop or crossed:
        if target is not None or stop is not None:
            if target is not None and (bad_target or crossed):
                proposal["target_price"] = "数据不足"
                fixes.append(f"目标价 {target:g} 不合理（现价 {last_close:g}）→ 数据不足")
            if stop is not None and (bad_stop or crossed):
                proposal["stop_price"] = "数据不足"
                fixes.append(f"止损价 {stop:g} 不合理（现价 {last_close:g}）→ 数据不足")
        else:
            proposal["target_price"] = "数据不足"
            proposal["stop_price"] = "数据不足"

    action = str(proposal.get("action") or "")
    if action in {"买入", "加仓"} and position <= 0:
        proposal["action"] = "观望"
        fixes.append(f"动作是{action}、仓位 0% → 观望")
    if action == "观望" and position >= 50:
        proposal["position_pct"] = 0
        fixes.append(f"观望却给 {position:g}% → 0%")
        position = 0.0

    decision = str(verdict.get("decision") or "")
    pct = _num(verdict.get("final_position_pct"))
    if pct is None:
        verdict["final_position_pct"] = 0
        fixes.append("最终仓位缺失 → 0%")
        pct = 0.0
    elif pct < 0 or pct > POSITION_MAX:
        pct = max(0.0, min(POSITION_MAX, pct))
        verdict["final_position_pct"] = pct
        fixes.append(f"最终仓位越界 → {pct:g}%")
    if decision == "否决" and pct > 0:
        verdict["final_position_pct"] = 0
        fixes.append(f"裁定否决却给 {pct:g}% → 0%")
        pct = 0.0
    if pct > position + SIZE_TOLERANCE:
        original = pct
        pct = position
        verdict["final_position_pct"] = pct
        fixes.append(f"最终仓位 {original:g}% 高于交易员 {position:g}% → 压回 {position:g}%")
    if decision == "批准" and pct <= 0:
        verdict["decision"] = "调整后批准"
        fixes.append("裁定批准却 0% 仓位 → 调整后批准")
    return proposal, verdict, fixes


def check_verdict(
    verdict: dict[str, Any], proposal: Optional[dict[str, Any]] = None
) -> list[str]:
    """The risk manager must not break its own arithmetic."""
    issues: list[str] = []
    if not isinstance(verdict, dict) or not verdict:
        return ["风控裁定缺失"]

    decision = str(verdict.get("decision") or "")
    pct = _num(verdict.get("final_position_pct"))
    if pct is None:
        issues.append("最终仓位缺失或不是数字")
    elif pct < 0:
        issues.append(f"最终仓位为负（{pct:g}%）")
    elif pct > POSITION_MAX:
        issues.append(f"最终仓位超过 100%（{pct:g}%）")

    if decision == "否决" and (pct or 0) > 0:
        issues.append(f"裁定否决，却给了 {pct:g}% 仓位")
    if decision == "批准" and pct is not None and pct <= 0:
        issues.append("裁定批准，仓位却是 0（应为「调整后批准」或「否决」）")

    traded = _num((proposal or {}).get("position_pct"))
    if pct is not None and traded is not None and pct > traded + SIZE_TOLERANCE:
        issues.append(f"最终仓位 {pct:g}% 高于交易员草案 {traded:g}%（风控不应加仓）")
    return issues
