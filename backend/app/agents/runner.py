"""Run the research team and stream what happens.

``stream_research`` is the only entry point: it builds the context from the local
panel, runs the graph, yields one event per state change, persists every node
payload, and finishes with a markdown report. The HTTP layer turns that generator
into SSE; the CLI turns it into progress lines.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from app.agents import context as ctxmod
from app.agents import guards
from app.agents import store as agent_store
from app.agents.graph import NODE_LABELS, build_graph
from app.agents.llm import ChatClient
from app.config import settings
from app.services.symbols import normalize_code
from app import skills

DISCLAIMER = (
    "目标价、期望收益与买卖动作均为模型基于本地面板的推算，不保证准确；"
    "全部内容仅供个人研究使用，据此操作风险自担。"
)


def _price(value: Any) -> Optional[float]:
    """Parse a price the model wrote as a number, '1480 元' or '数据不足'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group())
    return None


def build_plan(proposal: dict[str, Any], last_close: Optional[float]) -> dict[str, Any]:
    """Turn the trader's target/stop into expected return and reward/risk.

    The percentages are computed here, not by the model, so the report cannot
    disagree with itself about what a target price implies.
    """
    target = _price(proposal.get("target_price"))
    stop = _price(proposal.get("stop_price"))
    close = float(last_close) if last_close else None
    plan: dict[str, Any] = {
        "target_price": target,
        "stop_price": stop,
        "expected_return_pct": None,
        "downside_pct": None,
        "reward_risk": None,
    }
    if target and close:
        plan["expected_return_pct"] = round((target / close - 1) * 100, 2)
    if stop and close:
        plan["downside_pct"] = round((stop / close - 1) * 100, 2)
    if target and stop and close and close > stop:
        plan["reward_risk"] = round((target - close) / (close - stop), 2)
    return plan


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def build_report(state: dict[str, Any], ctx: dict[str, Any], usage: dict[str, int]) -> str:
    profile = ctx["profile"]
    price = ctx["price"]
    consensus = state.get("consensus") or {}
    proposal = state.get("proposal") or {}
    verdict = state.get("verdict") or {}
    analyses = state.get("analyses") or {}
    debate = state.get("debate") or []
    risks = state.get("risk_reviews") or []
    errors = state.get("errors") or []
    plan = build_plan(proposal, price.get("last_close"))

    lines = [
        f"# {profile['name']}（{profile['symbol']}）多智能体研究",
        "",
        f"> 截止 {ctx['as_of']} ｜ 收盘 {_fmt(price['last_close'])} ｜ "
        f"模型 `{state.get('model', '')}` ｜ 辩论 {state.get('rounds', 0)} 轮 ｜ "
        f"调用 {usage.get('calls', 0)} 次 / {usage.get('total_tokens', 0)} tokens"
        + (f" ｜ 技能 `{state.get('skill_version')}`" if state.get("skill_version") else ""),
        "",
        "## 结论",
        "",
    ]
    if consensus:
        lines.append(
            f"**{consensus.get('verdict', '—')}**（倾向分 {consensus.get('leaning_score', '—')}，"
            f"置信 {consensus.get('confidence', '—')}）—— {consensus.get('reasoning', '')}"
        )
    else:
        lines.append("研究主管未能给出结论。")
    if verdict:
        lines.extend(
            [
                "",
                f"**风控裁定：{verdict.get('decision', '—')}**，最终仓位 "
                f"{verdict.get('final_position_pct', '—')}%；{verdict.get('rationale', '')}",
            ]
        )

    lines.extend(["", "## 分析师观点", "", "| 角色 | 立场 | 评分 | 置信 | 核心判断 |", "|---|---|---:|---:|---|"])
    for role, payload in analyses.items():
        lines.append(
            f"| {payload.get('label', role)} | {payload.get('stance', '—')} | "
            f"{payload.get('score', '—')} | {payload.get('confidence', '—')} | "
            f"{str(payload.get('summary', '')).replace('|', '/')} |"
        )
    if not analyses:
        lines.append("| （无） | — | — | — | — |")

    if debate:
        lines.extend(["", "## 多空辩论", ""])
        for entry in debate:
            payload = entry.get("payload") or {}
            label = "多头" if entry["role"] == "bull" else "空头"
            lines.append(f"### {label} · 第 {entry.get('round')} 轮（信心 {payload.get('conviction', '—')}）")
            lines.append(f"- **主张**：{payload.get('thesis', '—')}")
            for item in payload.get("evidence") or []:
                lines.append(f"- 证据：{item}")
            for item in payload.get("rebuttals") or []:
                lines.append(f"- 回应「{item.get('target', '')}」：{item.get('rebuttal', '')}")
            lines.append(f"- **证伪条件**：{payload.get('invalidation', '—')}")
            lines.append("")

    if consensus.get("key_disagreements"):
        lines.extend(["### 真正的分歧点", ""])
        for item in consensus["key_disagreements"]:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## 交易员草案", ""])
    if proposal:
        lines.extend(
            [
                f"- 动作：**{proposal.get('action', '—')}**，仓位 {proposal.get('position_pct', '—')}%，"
                f"信心 {proposal.get('conviction', '—')}",
                f"- 理由：{proposal.get('rationale', '—')}",
                f"- 入场区间：{proposal.get('entry_zone', '—')}",
                f"- 失效条件：{proposal.get('stop_condition', '—')}",
                f"- 周期：{proposal.get('horizon', '—')}",
                f"- 改变信号：{proposal.get('what_would_change_my_mind', '—')}",
            ]
        )
    else:
        lines.append("交易员未给出草案。")

    lines.extend(["", "## 目标与赔率（推算）", ""])
    if plan.get("target_price") or plan.get("stop_price"):
        if plan.get("target_price"):
            expected = plan.get("expected_return_pct")
            suffix = f"（较收盘 {expected:+.2f}%）" if expected is not None else ""
            lines.append(f"- 目标价：**{_fmt(plan['target_price'])} 元**{suffix}")
        else:
            lines.append("- 目标价：模型未给出（数据不足）")
        if plan.get("stop_price"):
            downside = plan.get("downside_pct")
            suffix = f"（较收盘 {downside:+.2f}%）" if downside is not None else ""
            lines.append(f"- 止损价：{_fmt(plan['stop_price'])} 元{suffix}")
        else:
            lines.append("- 止损价：模型未给出（数据不足）")
        if plan.get("reward_risk") is not None:
            lines.append(f"- 赔率：{plan['reward_risk']:.2f} : 1（潜在空间 / 潜在亏损）")
            if plan["reward_risk"] < 1.5:
                lines.append("- 赔率不足 1.5:1，按交易员规则本应观望，请以风控裁定为准。")
        lines.append("")
    else:
        lines.append("交易员没有给出目标价与止损价（可能判定数据不足）。")

    lines.extend(["", "## 风控审查", "", "| 视角 | 裁定 | 建议仓位上限 | 主要关切 |", "|---|---|---:|---|"])
    for item in risks:
        concerns = "；".join(str(x) for x in (item.get("concerns") or [])[:3]).replace("|", "/")
        lines.append(
            f"| {item.get('persona', '—')} | {item.get('verdict', '—')} | "
            f"{item.get('position_advice_pct', '—')}% | {concerns or '—'} |"
        )
    if not risks:
        lines.append("| （无） | — | — | — |")

    if verdict.get("adjustments") or verdict.get("monitor"):
        lines.append("")
        for item in verdict.get("adjustments") or []:
            lines.append(f"- 调整：{item}")
        for item in verdict.get("monitor") or []:
            lines.append(f"- 监控：{item}")

    lines.extend(
        [
            "",
            "## 数据快照",
            "",
            f"- 价格：收盘 {_fmt(price['last_close'])}，5/20/60 日 "
            f"{_fmt(price['return_5d'])}% / {_fmt(price['return_20d'])}% / {_fmt(price['return_60d'])}%，"
            f"距 52 周高点 {_fmt(price['drawdown_from_high'])}%",
            f"- 估值：PE(TTM) {_fmt(ctx['valuation']['pe_ttm'])}（三年分位 "
            f"{_fmt(ctx['valuation']['pe_ttm_percentile_3y'])}%），PB {_fmt(ctx['valuation']['pb'])}",
            f"- 基本面：ROE {_fmt(ctx['fundamental']['roe'])}%，营收同比 "
            f"{_fmt(ctx['fundamental']['revenue_yoy'])}%，净利同比 {_fmt(ctx['fundamental']['profit_yoy'])}%",
            f"- 资金：20 日主力净流入 {_fmt(ctx['flow']['net_mf_amount_20d'], 0)} 万元，"
            f"融资余额变化 {_fmt(ctx['flow']['margin_change_20d_pct'])}%",
        ]
    )
    regime = (ctx.get("regime") or {}).get("indices") or []
    if regime:
        benchmark = next((item for item in regime if item["name"] == "沪深300"), regime[0])
        lines.append(
            f"- 大盘：{benchmark['name']} 近 20 日 {_fmt(benchmark['return_20d'])}%"
            + (
                f"，涨停 {(ctx['regime'].get('breadth') or {}).get('limit_up', '—')} 家 / "
                f"跌停 {(ctx['regime'].get('breadth') or {}).get('limit_down', '—')} 家"
                if ctx["regime"].get("breadth")
                else ""
            )
        )
    sector = (ctx.get("sector") or {}).get("peer")
    if sector:
        lines.append(
            f"- 同业：{sector['industry']} 共 {sector['count']} 只，中位 PE "
            f"{_fmt(sector['median_pe_ttm'])}，本股 {_fmt(sector['own_pe_ttm'])}"
        )
    hot = (ctx.get("attention") or {}).get("hot") or {}
    rank = (hot.get("同花顺") or {}).get("rank")
    if rank:
        lines.append(f"- 热度：同花顺热榜第 {int(rank)} 名")
    lines.append("")
    guards_found = guards.check_proposal(proposal, price.get("last_close"))
    guards_found += guards.check_verdict(verdict, proposal)
    fixes = state.get("guard_fixes") or []
    if guards_found or fixes:
        lines.extend(["## 决策体检（自动校验）", ""])
        for item in fixes:
            lines.append(f"- 已修正：{item}")
        for item in guards_found:
            lines.append(f"- 仅提示：{item}")
        lines.append("")
    if errors:
        lines.extend(["## 运行异常", ""])
        for item in errors:
            lines.append(f"- {item}")
        lines.append("")
    lines.extend(["---", ""])
    return "\n".join(lines)


async def stream_research(
    symbol: str,
    rounds: Optional[int] = None,
    *,
    persist: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    code = normalize_code(symbol)
    if not code:
        yield {"type": "error", "message": "股票代码无效"}
        return

    client = ChatClient()
    ok, reason = client.available()
    if not ok:
        yield {"type": "error", "message": reason}
        return

    rounds = max(1, min(int(rounds or settings.agent_debate_rounds), 4))
    started = time.perf_counter()

    yield {"type": "stage", "message": "读取本地面板数据…"}
    try:
        ctx = await asyncio.to_thread(ctxmod.build_context, code)
    except ctxmod.ContextError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    name = ctx["profile"]["name"]
    skill_label = skills.label()
    run_id = None
    if persist:
        run_id = await asyncio.to_thread(
            agent_store.create_run, code, name, client.model, rounds, ctx, skill_label
        )
        if run_id and skill_label:
            await asyncio.to_thread(skills.record, "agents", run_id, "trader")

    yield {
        "type": "run_start",
        "run_id": run_id,
        "symbol": code,
        "name": name,
        "as_of": ctx["as_of"],
        "model": client.model,
        "rounds": rounds,
        "skill_version": skill_label,
    }

    queue: asyncio.Queue = asyncio.Queue()
    graph = build_graph(client, queue)
    state = {
        "symbol": code,
        "name": name,
        "rounds": rounds,
        "context": ctx,
        "analyses": {},
        "debate": [],
        "errors": [],
        "model": client.model,
        "skill_version": skill_label,
    }

    task = asyncio.create_task(graph.ainvoke(state))
    final_state: dict[str, Any] = {}
    debate_counts = {"bull": 0, "bear": 0}
    finished = False

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
            if persist and event["type"] == "node_end":
                payload = event.get("payload")
                role = event["node"].split("_", 1)[1] if event["node"].startswith("analyst_") else event["node"]
                round_no = None
                if event["node"] in {"bull", "bear"}:
                    debate_counts[event["node"]] += 1
                    round_no = debate_counts[event["node"]]
                await asyncio.to_thread(
                    agent_store.append_message,
                    run_id,
                    event["node"],
                    event.get("label"),
                    role,
                    round_no,
                    event.get("status", "ok"),
                    payload,
                )
            yield event

        try:
            final_state = await task
        except Exception as exc:  # noqa: BLE001 - graph-level failure
            message = f"{type(exc).__name__}: {exc}"
            if persist and run_id:
                await asyncio.to_thread(
                    agent_store.finish_run,
                    run_id,
                    "failed",
                    error=message,
                    usage=client.usage.as_dict(),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            finished = True
            yield {"type": "error", "message": message}
            return

        usage = client.usage.as_dict()
        final_state["model"] = client.model
        final_state["rounds"] = rounds
        # 硬约束：自相矛盾的数字在落库前就被改掉（仓位钳制、目标价/止损非法的
        # 降级为"数据不足"），而不是只写一行"体检提示"然后原样存下去。
        proposal, verdict, fixes = guards.sanitize(
            final_state.get("proposal") or {},
            final_state.get("verdict") or {},
            ctx["price"].get("last_close"),
        )
        final_state["proposal"] = proposal
        final_state["verdict"] = verdict
        final_state["guard_fixes"] = fixes
        report = build_report(final_state, ctx, usage)
        duration_ms = int((time.perf_counter() - started) * 1000)

        if persist and run_id:
            await asyncio.to_thread(
                agent_store.finish_run,
                run_id,
                "ok" if not final_state.get("errors") else "partial",
                verdict=final_state.get("verdict"),
                report=report,
                usage=usage,
                duration_ms=duration_ms,
            )

        finished = True
        yield {
            "type": "run_end",
            "run_id": run_id,
            "report": report,
            "usage": usage,
            "duration_ms": duration_ms,
            "verdict": final_state.get("verdict"),
            "proposal": final_state.get("proposal"),
            "plan": build_plan(
                final_state.get("proposal") or {}, ctx["price"].get("last_close")
            ),
            "consensus": final_state.get("consensus"),
            "errors": final_state.get("errors") or [],
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
    finally:
        # 客户端断开（GeneratorExit / CancelledError）时，图还在后台跑就会继续烧
        # token，账本也会永远停在 running。这里兜底取消并落一条 cancelled。
        if not finished:
            if not task.done():
                task.cancel()
            if persist and run_id:
                try:
                    await asyncio.to_thread(
                        agent_store.finish_run,
                        run_id,
                        "cancelled",
                        error="客户端断开，已取消",
                        usage=client.usage.as_dict(),
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                except Exception:  # noqa: BLE001 - 断开时尽力记录
                    pass


async def run_research(symbol: str, rounds: Optional[int] = None) -> dict[str, Any]:
    """Non-streaming helper for the CLI and tests."""
    events: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    async for event in stream_research(symbol, rounds):
        events.append({"type": event["type"]})
        if event["type"] in {"run_end", "error"}:
            final = event
    return {"final": final, "events": events}
