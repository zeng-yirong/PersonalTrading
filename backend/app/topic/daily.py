"""The one-click daily brief: its own prompt, its own schema, its own fallback.

This deliberately does **not** reuse ``topic.prompts``. The free-form topic
prompt asks for eleven fields at once, and with a long topic text the model
sometimes returns nothing usable. The daily brief instead asks for seven concrete
fields, validates them, repairs once, and if the model still fails it renders a
rule-based memo from the sentiment series — so the button always produces
something, and never a schema error.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.agents.llm import ChatClient
from app.market import overview as market_overview

STAGES = ("冰点", "修复", "中性", "扩散", "偏热", "亢奋", "退潮")
REQUIRED = ("title", "stage", "summary", "sentiment", "sectors", "risks", "watching")

SYSTEM = """你是 A 股的个人研究员，写一份「今日市场复盘」。你只能使用我给出的数据，一个数字都不许编。
输出一个 JSON 对象，字段如下：
- title: 一句话标题，必须含阶段判断（如「情绪冰点修复，结构偏防御」）
- stage: 只能是 冰点|修复|中性|扩散|偏热|亢奋|退潮 之一
- summary: 3-5 句结论，至少出现 3 个具体数字（上涨占比、涨停家数、成交额、站上20日线占比等）
- sentiment: {"view": "一句话判断", "evidence": ["数字1", "数字2", "数字3"]}
- sectors: {"leaders": [{"name": "行业名", "pct": 数字, "note": "一句依据"}],
            "laggards": [{"name": "行业名", "pct": 数字, "note": "一句依据"}],
            "diffusion": "一句话，必须引用行业上涨占比"}
- risks: ["2-3 条风险"]
- watching: ["2-3 个明天的观察信号，要可验证"]
- data_gaps: ["数据缺口，没有就写空数组"]
规则：
1. 阶段判断要结合「情绪分数 + 今日变动 + 5 日变动」，不能只看一天。
1b. 指数涨跌幅（indices）、涨跌分布（distribution）、行业资金（industry_money）、
   北向资金（north，单位万元）、涨停梯队（limit_detail：首板/2板/3板+/炸板/封板率/最高连板）、
   两融余额（margin）都在数据里，禁止把这些写成「数据缺失」。
2. 板块只写我给的数据里出现的行业名，不准自己编行业。
3. 不要写买卖建议，不要给点位。
只输出 JSON，不要 markdown 代码块。"""


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, digits: int = 2) -> str:
    number = _num(value)
    return "—" if number is None else f"{number:.{digits}f}"


def _digits(text: str) -> int:
    return sum(1 for char in text if char.isdigit())


def _prompt(sentiment: dict[str, Any], overview: dict[str, Any]) -> str:
    components = sentiment.get("components") or {}
    points = sentiment.get("points") or []
    recent = [
        {
            "date": point.get("date"),
            "score": point.get("score"),
            "up_ratio": point.get("up_ratio"),
            "limit_up": point.get("limit_up"),
            "limit_down": point.get("limit_down"),
            "amount_yi": point.get("amount_yi"),
        }
        for point in points[-6:]
    ]
    payload = {
        "as_of": sentiment.get("as_of"),
        "indices": overview.get("indices") or [],
        "distribution": overview.get("distribution") or [],
        "industry_money": overview.get("money_by_industry") or {},
        "north": overview.get("north") or {},
        "limit_detail": overview.get("limit") or {},
        "margin": overview.get("margin") or {},
        "score": sentiment.get("score"),
        "prev_score": sentiment.get("prev_score"),
        "change": sentiment.get("change"),
        "change_5d": sentiment.get("change_5d"),
        "rule_stage": sentiment.get("stage"),
        "components": components,
        "last_6_days": recent,
        "sector_top": (overview.get("sectors") or {}).get("top") or [],
        "sector_bottom": (overview.get("sectors") or {}).get("bottom") or [],
    }
    return (
        "数据如下（JSON）：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        + "\n\n请按规则输出 JSON。"
    )


def _problems(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in REQUIRED:
        if not payload.get(key):
            issues.append(key)
    if payload.get("stage") not in STAGES:
        issues.append("stage")
    summary = str(payload.get("summary") or "")
    if _digits(summary) < 3:
        issues.append("summary")
    sentiment = payload.get("sentiment")
    if not isinstance(sentiment, dict) or not sentiment.get("view"):
        issues.append("sentiment")
    sectors = payload.get("sectors")
    if not isinstance(sectors, dict):
        issues.append("sectors")
    else:
        if not isinstance(sectors.get("leaders"), list) or not sectors.get("leaders"):
            issues.append("sectors.leaders")
        if not isinstance(sectors.get("laggards"), list) or not sectors.get("laggards"):
            issues.append("sectors.laggards")
        if not sectors.get("diffusion"):
            issues.append("sectors.diffusion")
    return issues


def _stage_verdict(stage: str) -> str:
    if stage in {"偏热", "亢奋", "扩散"}:
        return "看多"
    if stage in {"冰点", "退潮"}:
        return "看空"
    return "中性"


def render(report: dict[str, Any], sentiment: dict[str, Any]) -> str:
    components = sentiment.get("components") or {}
    lines = [
        f"# {report.get('title') or '今日市场复盘'}",
        "",
        f"> {sentiment.get('as_of') or '—'}｜情绪分数 {_fmt(sentiment.get('score'), 1)}"
        f"（较前一日 {_fmt(sentiment.get('change'), 1)}，较 5 日前 {_fmt(sentiment.get('change_5d'), 1)}）"
        f"｜阶段：**{report.get('stage')}**",
        "",
        "## 摘要",
        str(report.get("summary") or ""),
        "",
        "## 情绪",
        str((report.get("sentiment") or {}).get("view") or ""),
        "",
    ]
    for item in (report.get("sentiment") or {}).get("evidence") or []:
        lines.append(f"- {item}")
    lines += ["", "## 板块结构"]
    sectors = report.get("sectors") or {}
    leaders = sectors.get("leaders") or []
    laggards = sectors.get("laggards") or []
    if leaders:
        lines.append("**领涨**：" + "、".join(f"{item.get('name')}（{item.get('pct')}%）" for item in leaders))
    if laggards:
        lines.append("**领跌**：" + "、".join(f"{item.get('name')}（{item.get('pct')}%）" for item in laggards))
    if sectors.get("diffusion"):
        lines += ["", str(sectors.get("diffusion"))]
    lines += ["", "## 风险"]
    lines += [f"- {item}" for item in report.get("risks") or []]
    lines += ["", "## 明日观察"]
    lines += [f"- {item}" for item in report.get("watching") or []]
    gaps = report.get("data_gaps") or []
    lines += ["", "## 数据缺口"]
    lines += [f"- {item}" for item in gaps] if gaps else ["- 无"]
    lines += [
        "",
        "---",
        f"情绪分项：上涨占比 {_fmt(components.get('up_ratio'))}%，"
        f"站上20日线 {_fmt(components.get('above_ma20_ratio'))}%，"
        f"涨停 {components.get('limit_up')} / 跌停 {components.get('limit_down')}，"
        f"成交额 {_fmt(components.get('amount_yi'), 1)} 亿（20日均量 {_fmt(components.get('amount_ratio'))} 倍），"
        f"行业上涨占比 {_fmt(components.get('sector_up_ratio'))}%。",
    ]
    return "\n".join(lines)


def fallback(sentiment: dict[str, Any], sectors: dict[str, Any], reason: str) -> dict[str, Any]:
    """规则版备忘：模型不给结构化输出时也不能让按钮失败。"""
    components = sentiment.get("components") or {}
    stage = sentiment.get("stage") or "中性"
    top = (sectors.get("top") or [])[:3]
    bottom = (sectors.get("bottom") or [])[:3]
    summary = (
        f"{sentiment.get('as_of')} 情绪分数 {_fmt(sentiment.get('score'), 1)}，"
        f"较前一日 {_fmt(sentiment.get('change'), 1)}、较 5 日前 {_fmt(sentiment.get('change_5d'), 1)}；"
        f"上涨家数占比 {_fmt(components.get('up_ratio'))}%，"
        f"站上 20 日均线 {_fmt(components.get('above_ma20_ratio'))}%，"
        f"涨停 {components.get('limit_up')} 家、跌停 {components.get('limit_down')} 家，"
        f"成交额 {_fmt(components.get('amount_yi'), 1)} 亿元。"
        f"行业上涨占比 {_fmt(components.get('sector_up_ratio'))}%，"
        f"领涨 {top[0]['name'] if top else '—'}、领跌 {bottom[0]['name'] if bottom else '—'}。"
    )
    return {
        "title": f"情绪{stage}：规则版摘要",
        "stage": stage,
        "summary": summary,
        "sentiment": {
            "view": f"按规则判定为「{stage}」，模型输出不可用（{reason}），以下为数据摘要。",
            "evidence": [
                f"上涨占比 {_fmt(components.get('up_ratio'))}%",
                f"站上20日线 {_fmt(components.get('above_ma20_ratio'))}%",
                f"涨停/跌停 {components.get('limit_up')}/{components.get('limit_down')}",
                f"成交额 {_fmt(components.get('amount_yi'), 1)} 亿，20日均量 {_fmt(components.get('amount_ratio'))} 倍",
            ],
        },
        "sectors": {
            "leaders": [
                {"name": item["name"], "pct": round(float(item.get("pct_chg") or 0), 2), "note": "数据来自申万行业当日涨跌"}
                for item in top
            ],
            "laggards": [
                {"name": item["name"], "pct": round(float(item.get("pct_chg") or 0), 2), "note": "数据来自申万行业当日涨跌"}
                for item in bottom
            ],
            "diffusion": f"行业上涨占比 {_fmt(components.get('sector_up_ratio'))}%（{components.get('industries')} 个行业）。",
        },
        "risks": ["规则版摘要不含模型判断，需人工复核。"],
        "watching": ["次日涨停/跌停家数是否延续", "成交额能否维持在 20 日均量之上"],
        "data_gaps": [],
        "fallback": True,
    }


async def generate(
    client: ChatClient, sentiment: dict[str, Any], model: str = ""
) -> dict[str, Any]:
    """Ask the model once (plus one repair); fall back to rules if it fails."""
    overview = market_overview.overview()
    sectors = overview.get("sectors") or {}
    user = _prompt(sentiment, overview)
    reply = await client.complete_json(SYSTEM, user, max_tokens=2600, temperature=0.2)
    problems = _problems(reply)
    if problems:
        repair = (
            "上一次输出缺少或不合规：" + "、".join(problems)
            + "。只输出 JSON，必须包含全部字段且 stage 在允许取值内。"
        )
        reply = await client.complete_json(SYSTEM, user + "\n\n" + repair, max_tokens=2600, temperature=0.1)
        problems = _problems(reply)
    if problems:
        report = fallback(sentiment, sectors, "、".join(problems))
        report["model"] = "rule"
        return report

    report = {
        "title": str(reply.get("title")),
        "stage": str(reply.get("stage")),
        "summary": str(reply.get("summary")),
        "sentiment": reply.get("sentiment") or {},
        "sectors": reply.get("sectors") or {},
        "risks": reply.get("risks") or [],
        "watching": reply.get("watching") or [],
        "data_gaps": reply.get("data_gaps") or [],
    }
    report["model"] = model or client.model
    return report
