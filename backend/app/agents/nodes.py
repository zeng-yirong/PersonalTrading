"""Role nodes for the research graph.

Each node is one LLM call, returns only the slice of state it owns, and never
raises: a failed agent degrades the run (the report says which analyst is
missing) instead of destroying it.
"""

from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any, TypedDict

from app.agents import context as ctxmod
from app.agents import experience as experience_mod
from app.agents import memory as memory_mod
from app.agents import prompts
from app.agents.llm import ChatClient, LLMError
from app import skills


def _merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer: the four analysts write into ``analyses`` concurrently."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


async def _checked_json(
    client: ChatClient,
    system: str,
    user: str,
    required: tuple[str, ...],
    *,
    max_tokens: int,
) -> dict[str, Any]:
    """Parse the reply, and re-ask once if required fields are missing.

    Models sometimes answer with only the field they consider most relevant
    (e.g. a single rebuttal instead of a full bear thesis). One corrective round
    is far cheaper than a silently degraded run.
    """
    payload = await client.complete_json(system, user, max_tokens=max_tokens)
    missing = [field for field in required if field not in payload]
    if missing:
        corrective = (
            f"你上一次的输出缺少这些字段：{', '.join(missing)}。\n"
            f"请只输出一个 JSON 对象，必须且只能包含：{', '.join(required)}。"
            "不要解释、不要输出思考过程、不要使用英文推理。"
        )
        payload = await client.complete_json(
            system, f"{user}\n\n{corrective}", max_tokens=max_tokens
        )
    return payload


class AgentState(TypedDict, total=False):
    run_id: str
    symbol: str
    name: str
    rounds: int
    model: str
    context: dict[str, Any]
    # Parallel analysts each return only their own slice.
    analyses: Annotated[dict[str, Any], _merge_dict]
    debate: list[dict[str, Any]]
    consensus: dict[str, Any]
    proposal: dict[str, Any]
    risk_reviews: list[dict[str, Any]]
    verdict: dict[str, Any]
    errors: Annotated[list[str], operator.add]


def _context_text(ctx: dict[str, Any], extra: str) -> str:
    head = ctxmod.render_profile(ctx)
    market = ctxmod.render_market(ctx, ctx["price"].get("return_20d"))
    return f"{head}\n\n{extra}\n\n{market}\n\n以上全部为截止 {ctx['as_of']} 的本地数据。"


def _analyses_text(state: AgentState) -> str:
    lines = []
    for role, payload in (state.get("analyses") or {}).items():
        label = prompts.ROLE_LABELS.get(role, role)
        points = "；".join(str(item) for item in (payload.get("key_points") or [])[:5])
        lines.append(
            f"[{label}] 立场={payload.get('stance')} 评分={payload.get('score')} "
            f"置信={payload.get('confidence')}：{payload.get('summary')}。要点：{points}"
        )
    return "\n".join(lines) if lines else "（无分析师结论）"


def _debate_text(state: AgentState, limit: int = 6) -> str:
    entries = state.get("debate") or []
    lines = []
    for entry in entries[-limit:]:
        label = "多头" if entry["role"] == "bull" else "空头"
        payload = entry.get("payload") or {}
        rebuttals = "；".join(
            f"回应「{item.get('target', '')}」：{item.get('rebuttal', '')}"
            for item in (payload.get("rebuttals") or [])[:3]
        )
        lines.append(
            f"[{label} 第{entry.get('round')}轮] {payload.get('thesis')} "
            f"（信心 {payload.get('conviction')}）。证据：{'；'.join(str(x) for x in (payload.get('evidence') or [])[:3])}。"
            f"{rebuttals}"
        )
    return "\n".join(lines) if lines else "（暂无辩论）"


# ------------------------------------------------------------------ analysts
def _make_analyst(client: ChatClient, role: str, system: str, render: Any):
    async def node(state: AgentState) -> dict[str, Any]:
        ctx = state["context"]
        try:
            user = _context_text(ctx, render(ctx))
            payload = await client.complete_json(system, user, max_tokens=2000)
        except LLMError as exc:
            return {"errors": [f"{role}: {exc}"]}
        analyses = dict(state.get("analyses") or {})
        payload["role"] = role
        payload["label"] = prompts.ROLE_LABELS.get(role, role)
        analyses[role] = payload
        return {"analyses": analyses}

    return node


# ------------------------------------------------------------------- debate
def _make_debater(client: ChatClient, role: str, system: str):
    async def node(state: AgentState) -> dict[str, Any]:
        ctx = state["context"]
        debate = list(state.get("debate") or [])
        round_no = sum(1 for item in debate if item["role"] == role) + 1
        prior = (
            "以下是对手方此前的关键论点，请正面回应：\n" + _debate_text(state)
            if debate
            else "这是第一轮，还没有对手论点。"
        )
        user = (
            f"{ctxmod.render_profile(ctx)}\n\n"
            f"【四位分析师的结构化结论】\n{_analyses_text(state)}\n\n"
            f"【关键数据】\n{ctxmod.render_technical(ctx)}\n{ctxmod.render_fundamental(ctx)}\n"
            f"{ctxmod.render_flow(ctx)}\n{ctxmod.render_sentiment(ctx)}\n\n"
            f"{ctxmod.render_market(ctx, ctx['price'].get('return_20d'))}\n\n"
            f"{prior}"
        )
        try:
            # Debaters write long evidence + rebuttal lists; a small budget
            # truncates the JSON mid-string and the whole reply is unusable.
            payload = await _checked_json(
                client,
                system,
                user,
                ("thesis", "evidence", "rebuttals", "invalidation", "conviction"),
                max_tokens=3400,
            )
        except LLMError as exc:
            return {"errors": [f"{role}: {exc}"]}
        debate.append({"role": role, "round": round_no, "payload": payload})
        return {"debate": debate}

    return node


def debate_router(state: AgentState) -> str:
    max_rounds = int(state.get("rounds") or 1)
    bull_rounds = sum(1 for item in (state.get("debate") or []) if item["role"] == "bull")
    bear_rounds = sum(1 for item in (state.get("debate") or []) if item["role"] == "bear")
    if bull_rounds < max_rounds or bear_rounds < max_rounds:
        # Alternate: whoever is behind speaks next.
        return "bull" if bull_rounds <= bear_rounds else "bear"
    return "research_manager"


# ----------------------------------------------------------------- decision
def _make_manager(client: ChatClient):
    async def node(state: AgentState) -> dict[str, Any]:
        ctx = state["context"]
        user = (
            f"{ctxmod.render_profile(ctx)}\n\n"
            f"【分析师结论】\n{_analyses_text(state)}\n\n"
            f"【多空辩论全文】\n{_debate_text(state, limit=99)}"
        )
        try:
            payload = await client.complete_json(prompts.SYSTEM_RESEARCH_MANAGER, user)
        except LLMError as exc:
            return {"errors": [f"research_manager: {exc}"]}
        return {"consensus": payload}

    return node


def _make_trader(client: ChatClient):
    async def node(state: AgentState) -> dict[str, Any]:
        ctx = state["context"]
        consensus = state.get("consensus") or {}
        user = (
            f"{ctxmod.render_profile(ctx)}\n\n"
            f"【技术面】\n{ctxmod.render_technical(ctx)}\n\n"
            f"【估值】\n{ctxmod.render_fundamental(ctx)}\n\n"
            f"【研究主管综合判断】\n"
            f"结论={consensus.get('verdict')} 倾向分={consensus.get('leaning_score')} "
            f"理由={consensus.get('reasoning')}\n"
            f"分歧点：{'；'.join(str(x) for x in (consensus.get('key_disagreements') or []))}\n\n"
            f"【辩论摘要】\n{_debate_text(state, limit=4)}"
        )
        recalled = memory_mod.render(ctx.get("memory") or [])
        if recalled:
            user = f"{user}\n\n{recalled}"
        lessons = experience_mod.render(ctx.get("experience") or [])
        if lessons:
            user = f"{user}\n\n{lessons}"
        try:
            payload = await client.complete_json(
                prompts.SYSTEM_TRADER + skills.prompt_suffix("trader"), user, max_tokens=1800
            )
        except LLMError as exc:
            return {"errors": [f"trader: {exc}"]}
        return {"proposal": payload}

    return node


def _make_risk(client: ChatClient):
    personas = ("激进", "中性", "保守")

    async def node(state: AgentState) -> dict[str, Any]:
        ctx = state["context"]
        proposal = state.get("proposal") or {}
        consensus = state.get("consensus") or {}
        user = (
            f"{ctxmod.render_profile(ctx)}\n\n"
            f"【交易员草案】动作={proposal.get('action')} "
            f"仓位={proposal.get('position_pct')}% 信心={proposal.get('conviction')}\n"
            f"理由={proposal.get('rationale')}\n"
            f"入场={proposal.get('entry_zone')} 失效条件={proposal.get('stop_condition')}\n"
            f"周期={proposal.get('horizon')} 改变信号={proposal.get('what_would_change_my_mind')}\n\n"
            f"【研究主管】{consensus.get('verdict')} / 倾向分 {consensus.get('leaning_score')}\n\n"
            f"【风险相关数据】\n{ctxmod.render_technical(ctx)}\n{ctxmod.render_flow(ctx)}\n"
            f"{ctxmod.render_sentiment(ctx)}"
        )

        async def review(persona: str) -> dict[str, Any]:
            system = prompts.SYSTEM_RISK.replace("{persona}", persona)
            try:
                payload = await _checked_json(
                    client,
                    system,
                    user,
                    ("persona", "concerns", "position_advice_pct", "verdict", "reasoning"),
                    max_tokens=1400,
                )
            except LLMError as exc:
                return {"persona": persona, "verdict": "无法评估", "error": str(exc)}
            payload.setdefault("persona", persona)
            return payload

        reviews = await asyncio.gather(*(review(p) for p in personas))
        return {"risk_reviews": [dict(item) for item in reviews]}

    return node


def _make_risk_manager(client: ChatClient):
    async def node(state: AgentState) -> dict[str, Any]:
        ctx = state["context"]
        proposal = state.get("proposal") or {}
        reviews = state.get("risk_reviews") or []
        review_text = "\n".join(
            f"[{item.get('persona')}] 裁定={item.get('verdict')} 建议仓位上限={item.get('position_advice_pct')}% "
            f"理由={item.get('reasoning')} 关切={'；'.join(str(x) for x in (item.get('concerns') or []))}"
            for item in reviews
        )
        user = (
            f"{ctxmod.render_profile(ctx)}\n\n"
            f"【交易员草案】{proposal.get('action')} / 仓位 {proposal.get('position_pct')}% / "
            f"信心 {proposal.get('conviction')}\n\n"
            f"【三位风控委员意见】\n{review_text or '（无）'}"
        )
        recalled = memory_mod.render(ctx.get("memory") or [])
        if recalled:
            user = f"{user}\n\n{recalled}"
        lessons = experience_mod.render(ctx.get("experience") or [])
        if lessons:
            user = f"{user}\n\n{lessons}"
        try:
            payload = await client.complete_json(
                prompts.SYSTEM_RISK_MANAGER + skills.prompt_suffix("risk_manager"), user
            )
        except LLMError as exc:
            return {
                "errors": [f"risk_manager: {exc}"],
                "verdict": {"decision": "无法评估", "error": str(exc)},
            }
        return {"verdict": payload}

    return node


ANALYSTS = (
    ("market", prompts.SYSTEM_TECHNICAL, ctxmod.render_technical),
    ("fundamental", prompts.SYSTEM_FUNDAMENTAL, ctxmod.render_fundamental),
    ("flow", prompts.SYSTEM_FLOW, ctxmod.render_flow),
    ("attention", prompts.SYSTEM_SENTIMENT, ctxmod.render_attention_full),
    ("event", prompts.SYSTEM_EVENT, ctxmod.render_events),
    ("regime", prompts.SYSTEM_REGIME, ctxmod.render_regime),
    ("sector", prompts.SYSTEM_SECTOR, ctxmod.render_sector),
)
