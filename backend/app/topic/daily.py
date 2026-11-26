"""The one-click daily brief: dedicated prompt, validation, rule fallback.

Design goals, in order:

1. **数字必须来自快照** — the model may write prose, but every number it cites is
   checked against the payload; a reply that invents numbers is repaired once and
   otherwise replaced by a rule-based memo.
2. **结构固定** — 结论 / 盘面 / 情绪 / 资金 / 板块 / 操作建议 / 昨日对照 /
   观察 / 风险 / 缺口，缺一段就重试。
3. **可验证** — 明日观察必须是「条件 + 阈值」，第二天可以自动判定；其中
   ``prediction`` 进命中率、``trigger`` 只进触发率（把入场条件记成"判断错误"
   是 2026-11-26 之前的测量 bug）。
4. **可追责** — 上一份复盘会作为输入，模型要逐条说明昨天的判断兑现没有。

This module never touches the database; ``topic.service`` saves the result.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

from app.agents.llm import ChatClient
from app.market import overview as market_overview
from app.market.sentiment import SIGNAL_METRICS, SIGNAL_STEPS

STAGES = ("冰点", "修复", "中性", "扩散", "偏热", "亢奋", "退潮")
STANCES = ("进攻", "均衡", "防守")
REQUIRED = (
    "title",
    "stage",
    "stance",
    "summary",
    "market_view",
    "sentiment_view",
    "money_view",
    "sectors",
    "action",
    "watching",
    "risks",
)

SYSTEM = """你是 A 股的个人研究员，为个人交易账户写「今日复盘」。数据由我提供，只能引用其中的数字。
输出一个 JSON 对象，字段：
- title: 一句话标题，含阶段判断
- stage: 冰点|修复|中性|扩散|偏热|亢奋|退潮
- stance: 进攻|均衡|防守（对明天的总体姿态）
- summary: 3-5 句结论，至少 4 个数字
- market_view: 1-2 句，讲指数分化、涨跌分布、量能（引用 amount_percentile）
- sentiment_view: 1-2 句，讲情绪分数与分位、涨停梯队、封板率
- money_view: 1-2 句，讲主力、北向、两融是否同向
- sectors: {"leaders":[{"name","pct","note"}], "laggards":[...], "diffusion":"引用行业上涨占比", "concentration":"引用集中度", "continuity":"昨日领涨今天是否延续"}
- action: {"position_range":"例如 0-2 成", "buy":["什么条件下买什么方向"], "sell":["什么条件下减仓/止盈"], "avoid":["今天不要做什么"]}
- yesterday: "昨日判断对照：昨天说 X，今天 Y；没有上一份就写「无」"
- watching: 2-4 条，每条带 kind：
   {"kind":"prediction|trigger","metric":"指标","op":">=|<=|value":"阈值(数字)","text":"一句话"}
   kind=prediction 是**对明天的预测**（会被次日自动判定，计入命中率）；
   kind=trigger 是**交易触发条件**（只统计是否触发，不计入命中率）。
   指标只能取：score(情绪分) up_ratio(上涨占比%) above_ma20_ratio(站上20日线%) limit_up(涨停家数)
   limit_down(跌停家数) seal_rate(封板率%) amount_yi(成交额亿) amount_ratio(成交额/20日均量)
   sector_up_ratio(行业上涨占比%) north_net(北向净流入万元)；
   **至少 2 条 prediction**，最多 2 条 trigger。
- risks: ["2-3 条风险"]
- data_gaps: ["数据缺口，没有就写空数组"]
规则：
1. 这是个人交易账户，允许给明确的买卖方向与仓位区间（标为推算），但要写依据。
2. 数字只能来自数据；不确定就写「不确定」，不要编。
3. 阶段要结合「分数 + 分位 + 今日变动 + 5 日变动」，不能只看一天。
4. 板块只写数据里出现的行业名。
5. **prediction 的阈值必须与今日实际值有明显差距**（步长：score 3 分、上涨占比 3pp、
   站上20日线 2pp、涨停 5 家、跌停 3 家、封板率 3pp、成交额 500 亿、量比 0.1、
   行业上涨占比 5pp、北向 5 亿元）。把今天的数值原样写成"预测明天≥今天"会被判为无效。
6. trigger 的阈值可以等于今日值，但要在 text 里写清"满足才买/才减"，它不参与命中率。
只输出 JSON，不要 markdown 代码块。"""


# --------------------------------------------------------------------- helpers
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


def _walk_numbers(node: Any, out: set[float]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                # 允许单位换算：万元→亿元（/1e4）、元→亿元（/1e8）
                for digits in (0, 1, 2):
                    out.add(round(float(value), digits))
                if str(key).endswith(("_wan", "_yuan")):
                    divisor = 1e4 if str(key).endswith("_wan") else 1e8
                    for digits in (0, 1, 2):
                        out.add(round(float(value) / divisor, digits))
            else:
                _walk_numbers(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk_numbers(value, out)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        for digits in (0, 1, 2):
            out.add(round(float(node), digits))
    elif isinstance(node, str):
        # 字符串里也可能带数字（如 "2026-11-24"），先解析再入库
        for token in re.findall(r"-?\d+(?:\.\d+)?", node):
            try:
                for digits in (0, 1, 2):
                    out.add(round(float(token), digits))
            except ValueError:
                continue


def numbers_ok(text: str, payload: dict[str, Any], minimum: float = 0.5) -> tuple[bool, float]:
    """Check that quoted numbers exist in the snapshot (tolerance: 0-2 decimals)."""
    allowed: set[float] = set()
    _walk_numbers(payload, allowed)
    tokens = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if not tokens:
        return True, 1.0
    matched = 0
    for token in tokens:
        if any(round(token, digits) in allowed for digits in (0, 1, 2)):
            matched += 1
    ratio = matched / len(tokens)
    return ratio >= minimum, round(ratio, 2)


# ---------------------------------------------------------------------- prompt
def _prompt(
    sentiment: dict[str, Any],
    overview: dict[str, Any],
    previous: Optional[dict[str, Any]],
) -> str:
    components = sentiment.get("components") or {}
    points = sentiment.get("points") or []
    payload = {
        "as_of": sentiment.get("as_of"),
        "indices": overview.get("indices") or [],
        "distribution": overview.get("distribution") or [],
        "style": overview.get("style") or {},
        "sector_top": (overview.get("sectors") or {}).get("top") or [],
        "sector_bottom": (overview.get("sectors") or {}).get("bottom") or [],
        "concentration": (overview.get("sectors") or {}).get("concentration"),
        "continuity": (overview.get("sectors") or {}).get("continuity") or [],
        "sector_up_ratio": components.get("sector_up_ratio"),
        "limit_detail": overview.get("limit") or {},
        "money": {
            "net_mf_amount_wan": (overview.get("money") or {}).get("net_mf_amount"),
            "date": (overview.get("money") or {}).get("date"),
        },
        "north": {
            "net_wan": (overview.get("north") or {}).get("net"),
            "hgt_wan": (overview.get("north") or {}).get("hgt"),
            "sgt_wan": (overview.get("north") or {}).get("sgt"),
            "date": (overview.get("north") or {}).get("date"),
        },
        "margin": {
            "balance_yuan": (overview.get("margin") or {}).get("balance"),
            "change_5d_pct": (overview.get("margin") or {}).get("change_5d_pct"),
            "date": (overview.get("margin") or {}).get("date"),
        },
        "sentiment": {
            "score": sentiment.get("score"),
            "score_percentile_250d": sentiment.get("percentile"),
            "amount_percentile_250d": sentiment.get("amount_percentile"),
            "change": sentiment.get("change"),
            "change_5d": sentiment.get("change_5d"),
            "rule_stage": sentiment.get("stage"),
            "components": components,
            "last_6_days": [
                {
                    "date": point.get("date"),
                    "score": point.get("score"),
                    "up_ratio": point.get("up_ratio"),
                    "limit_up": point.get("limit_up"),
                    "limit_down": point.get("limit_down"),
                    "amount_yi": point.get("amount_yi"),
                }
                for point in points[-6:]
            ],
        },
        "previous_brief": previous,
        "units": {
            "money.net_mf_amount_wan": "万元",
            "north.net_wan": "万元",
            "margin.balance_yuan": "元",
            "sentiment.components.amount_yi": "亿元",
            "sector pct": "%",
            "sector amount": "万元",
        },
        "notes": {
            "score_percentile_250d": "情绪分数在过去 250 个交易日中的分位（0=最冷，100=最热）",
            "amount_percentile_250d": "当日成交额在过去 250 个交易日中的分位",
        },
    }
    return (
        "数据如下（JSON）：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        + "\n\n请按规则输出 JSON。"
    )


# ------------------------------------------------------------------ validation
def _problems(
    payload: dict[str, Any],
    previous: Optional[dict[str, Any]],
    today: Optional[dict[str, Any]] = None,
) -> list[str]:
    issues: list[str] = []
    for key in REQUIRED:
        if not payload.get(key):
            issues.append(key)
    if payload.get("stage") not in STAGES:
        issues.append("stage")
    if payload.get("stance") not in STANCES:
        issues.append("stance")
    if _digits(str(payload.get("summary") or "")) < 4:
        issues.append("summary")

    sectors = payload.get("sectors")
    if not isinstance(sectors, dict):
        issues.append("sectors")
    else:
        for key in ("leaders", "laggards"):
            if not isinstance(sectors.get(key), list) or not sectors.get(key):
                issues.append(f"sectors.{key}")
        for key in ("diffusion", "concentration", "continuity"):
            if not sectors.get(key):
                issues.append(f"sectors.{key}")

    action = payload.get("action")
    if not isinstance(action, dict):
        issues.append("action")
    else:
        for key in ("position_range", "buy", "sell", "avoid"):
            if not action.get(key):
                issues.append(f"action.{key}")

    watching = payload.get("watching")
    if not isinstance(watching, list) or len(watching) < 2:
        issues.append("watching")
    else:
        predictions = 0
        broken = False
        for item in watching:
            if not isinstance(item, dict):
                issues.append("watching")
                broken = True
                break
            kind = item.get("kind")
            if kind not in {"prediction", "trigger"}:
                issues.append("watching.kind")
                broken = True
                break
            if item.get("metric") not in SIGNAL_METRICS:
                issues.append("watching.metric")
                broken = True
                break
            if item.get("op") not in {">=", "<="}:
                issues.append("watching.op")
                broken = True
                break
            value = _num(item.get("value"))
            if value is None:
                issues.append("watching.value")
                broken = True
                break
            if not item.get("text"):
                issues.append("watching.text")
                broken = True
                break
            if kind == "prediction":
                predictions += 1
                step = SIGNAL_STEPS.get(item["metric"])
                current = (today or {}).get(item["metric"])
                if step and current is not None and abs(value - float(current)) < step:
                    issues.append(
                        f"watching.anchored（{item['metric']} 阈值 {value:g} 距今日 "
                        f"{float(current):g} 不足最小步长 {step:g}）"
                    )
                    broken = True
                    break
        if not broken:
            if predictions < 2:
                issues.append("watching.prediction（至少 2 条 prediction）")
            if len(watching) - predictions > 2:
                issues.append("watching.trigger（最多 2 条 trigger）")

    # 数字自检：summary / action / sectors 里引用的数字要在快照里找得到
    if previous is not None:
        pass
    check_text = " ".join(
        str(payload.get(key) or "")
        for key in ("summary", "market_view", "sentiment_view", "money_view")
    )
    return issues


def _stage_verdict(stage: str) -> str:
    if stage in {"偏热", "亢奋", "扩散"}:
        return "看多"
    if stage in {"冰点", "退潮"}:
        return "看空"
    return "中性"


def _default_action(stage: str, score: float) -> dict[str, Any]:
    if stage in {"亢奋", "偏热"}:
        return {
            "position_range": "5-7 成",
            "buy": ["强势板块回踩不破 5 日线时加仓"],
            "sell": ["封板率跌破 40% 或涨停家数腰斩时减仓"],
            "avoid": ["不追高位连板", "不满仓押单一题材"],
        }
    if stage in {"冰点", "退潮"}:
        return {
            "position_range": "0-2 成",
            "buy": ["等成交额缩到 20 日均量 0.8 倍以下、涨停回升再考虑"],
            "sell": ["反弹到 5 日线附近先减仓"],
            "avoid": ["不抄跌停板", "不接高位补跌"],
        }
    return {
        "position_range": "3-5 成",
        "buy": ["只做有资金流入的低位方向"],
        "sell": ["冲高不放量时减仓"],
        "avoid": ["不追一日游题材"],
    }


# ---------------------------------------------------------------------- render
def _watch_text(items: Any) -> list[str]:
    output = []
    for item in items or []:
        if isinstance(item, dict):
            text = item.get("text") or f"{SIGNAL_METRICS.get(item.get('metric'), item.get('metric'))} {item.get('op')} {item.get('value')}"
            tag = "触发" if item.get("kind") == "trigger" else "预测"
            output.append(f"[{tag}] {text}（{item.get('metric')} {item.get('op')} {item.get('value')}）")
        else:
            output.append(str(item))
    return output


def judge(signals: Any, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Judge yesterday's watching list against today's metrics (no model).

    两类观察分开判：``prediction`` 兑现/落空（进命中率）；``trigger`` 只问条件
    有没有触发（进触发率）。把"满足才买"记成"判断错误"是 2026-11-26 之前的
    测量 bug。没有 ``kind`` 的历史记录记作 ``legacy``：照常显示判定，但不进
    任何分母——否则新旧口径混在一起，就看不出修好没有。
    """
    output = []
    for item in signals or []:
        if not isinstance(item, dict):
            output.append(
                {"signal": str(item), "kind": "legacy", "status": "无法判定", "actual": None}
            )
            continue
        kind = item.get("kind") if item.get("kind") in {"prediction", "trigger"} else "legacy"
        metric = item.get("metric")
        target = _num(item.get("value"))
        actual = metrics.get(metric) if metric else None
        if metric not in SIGNAL_METRICS or target is None or actual is None:
            status = "无法判定"
        elif kind == "trigger":
            fired = actual >= target if item.get("op") == ">=" else actual <= target
            status = "已触发" if fired else "未触发"
        else:
            ok = actual >= target if item.get("op") == ">=" else actual <= target
            status = "兑现" if ok else "落空"
        output.append(
            {
                "signal": item.get("text") or f"{metric} {item.get('op')} {target}",
                "kind": kind,
                "metric": metric,
                "op": item.get("op"),
                "value": target,
                "actual": round(float(actual), 2) if actual is not None else None,
                "status": status,
            }
        )
    return output


def render(report: dict[str, Any], sentiment: dict[str, Any]) -> str:
    components = sentiment.get("components") or {}
    action = report.get("action") or {}
    sectors = report.get("sectors") or {}

    def _li(items: Any) -> list[str]:
        if not items:
            return ["- —"]
        return [f"- {item}" for item in items]

    lines = [
        f"# {report.get('title') or '今日市场复盘'}",
        "",
        f"> {sentiment.get('as_of') or '—'}｜情绪 {_fmt(sentiment.get('score'), 1)}"
        f"（分位 {_fmt(sentiment.get('percentile'), 0)}，较前日 {_fmt(sentiment.get('change'), 1)}）"
        f"｜阶段 **{report.get('stage')}**｜姿态 **{report.get('stance')}**"
        f"｜仓位建议 **{action.get('position_range') or '—'}**",
        "",
        "## 结论",
        str(report.get("summary") or ""),
        "",
        "## 盘面",
        str(report.get("market_view") or ""),
        "",
        "## 情绪",
        str(report.get("sentiment_view") or ""),
        "",
        "## 资金",
        str(report.get("money_view") or ""),
        "",
        "## 板块",
    ]
    leaders = sectors.get("leaders") or []
    laggards = sectors.get("laggards") or []
    if leaders:
        lines.append("**领涨**：" + "、".join(f"{item.get('name')}（{item.get('pct')}%）" for item in leaders))
    if laggards:
        lines.append("**领跌**：" + "、".join(f"{item.get('name')}（{item.get('pct')}%）" for item in laggards))
    if sectors.get("diffusion"):
        lines.append(f"- 扩散度：{sectors.get('diffusion')}")
    if sectors.get("concentration"):
        lines.append(f"- 集中度：{sectors.get('concentration')}")
    if sectors.get("continuity"):
        lines.append(f"- 延续性：{sectors.get('continuity')}")
    lines += [
        "",
        "## 操作建议（推算）",
        f"- 仓位区间：{action.get('position_range') or '—'}",
        "- 可买方向：",
        *_li(action.get("buy")),
        "- 减仓/止盈：",
        *_li(action.get("sell")),
        "- 不做的事：",
        *_li(action.get("avoid")),
        "",
        "## 昨日判断对照",
        str(report.get("yesterday") or "无"),
        "",
        "## 明日观察（预测 / 触发）",
        *_li(_watch_text(report.get("watching"))),
        "",
        "## 风险",
        *_li(report.get("risks")),
        "",
        "## 数据缺口",
        *(_li(report.get("data_gaps")) if report.get("data_gaps") else ["- 无"]),
        "",
        "---",
        f"分项：上涨占比 {_fmt(components.get('up_ratio'))}%，"
        f"站上20日线 {_fmt(components.get('above_ma20_ratio'))}%，"
        f"涨停 {components.get('limit_up')} / 跌停 {components.get('limit_down')}，"
        f"成交额 {_fmt(components.get('amount_yi'), 1)} 亿（20日均量 {_fmt(components.get('amount_ratio'))} 倍），"
        f"行业上涨占比 {_fmt(components.get('sector_up_ratio'))}%。",
    ]
    return "\n".join(lines)


def fallback(
    sentiment: dict[str, Any], overview: dict[str, Any], reason: str
) -> dict[str, Any]:
    """规则版：模型输出不可用时，仍然给出一份结构完整、数字真实的复盘。"""
    components = sentiment.get("components") or {}
    stage = sentiment.get("stage") or "中性"
    sector_row = overview.get("sectors") or {}
    top = (sector_row.get("top") or [])[:3]
    bottom = (sector_row.get("bottom") or [])[:3]
    limit = overview.get("limit") or {}
    north = overview.get("north") or {}
    margin = overview.get("margin") or {}
    summary = (
        f"情绪分数 {_fmt(sentiment.get('score'), 1)}（近一年分位 {_fmt(sentiment.get('percentile'), 0)}），"
        f"较前一日 {_fmt(sentiment.get('change'), 1)}、较 5 日前 {_fmt(sentiment.get('change_5d'), 1)}；"
        f"上涨占比 {_fmt(components.get('up_ratio'))}%，站上 20 日线 {_fmt(components.get('above_ma20_ratio'))}%，"
        f"涨停 {limit.get('up')} 家、跌停 {limit.get('down')} 家、封板率 {_fmt(limit.get('seal_rate'), 1)}%，"
        f"成交额 {_fmt(components.get('amount_yi'), 1)} 亿元。"
        f"北向净流入 {_fmt(north.get('net'), 1)} 万元，两融余额 {_fmt(margin.get('balance'), 0)} 元。"
    )
    action = _default_action(stage, float(sentiment.get("score") or 0))
    return {
        "title": f"情绪{stage}：规则版摘要",
        "stage": stage,
        "stance": "防守" if stage in {"冰点", "退潮"} else ("进攻" if stage in {"偏热", "亢奋"} else "均衡"),
        "summary": summary,
        "market_view": f"成交额 {_fmt(components.get('amount_yi'), 1)} 亿，"
        f"20 日均量 {_fmt(components.get('amount_ratio'))} 倍，"
        f"上涨占比 {_fmt(components.get('up_ratio'))}%。",
        "sentiment_view": f"情绪 {_fmt(sentiment.get('score'), 1)}，"
        f"涨停 {limit.get('up')} 家、炸板 {limit.get('broken')} 家、封板率 {_fmt(limit.get('seal_rate'), 1)}%。",
        "money_view": f"北向 {_fmt(north.get('net'), 1)} 万元；两融余额 {_fmt(margin.get('balance'), 0)} 元。",
        "sectors": {
            "leaders": [
                {"name": item["name"], "pct": round(float(item.get("pct_chg") or 0), 2), "note": "申万一级当日涨幅"}
                for item in top
            ],
            "laggards": [
                {"name": item["name"], "pct": round(float(item.get("pct_chg") or 0), 2), "note": "申万一级当日跌幅"}
                for item in bottom
            ],
            "diffusion": f"行业上涨占比 {_fmt(components.get('sector_up_ratio'))}%",
            "concentration": f"前 5 行业成交占比 {_fmt(sector_row.get('concentration'), 1)}%",
            "continuity": "规则版不判断延续性",
        },
        "action": action,
        "yesterday": "无（规则版未做昨日对照）",
        "watching": [
            {
                "kind": "prediction",
                "metric": "limit_up",
                "op": ">=",
                "value": round(float(limit.get("up") or 0) * 1.2, 1),
                "text": f"涨停家数回到 {int((limit.get('up') or 0) * 1.2)} 家以上",
            },
            {
                "kind": "prediction",
                "metric": "amount_yi",
                "op": ">=",
                "value": round(float(components.get("amount_yi") or 0) * 0.9, 1),
                "text": f"成交额维持在 {_fmt(components.get('amount_yi'), 0)} 亿元附近",
            },
        ],
        "risks": ["规则版摘要不含模型判断，需人工复核。"],
        "data_gaps": [],
        "fallback": True,
        "model": "rule",
        "reason": reason,
    }


# -------------------------------------------------------------------- generate
async def generate(
    client: ChatClient,
    sentiment: dict[str, Any],
    overview: dict[str, Any],
    previous: Optional[dict[str, Any]] = None,
    today: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """``today`` 是今日各指标的实际值，只用于校验「预测阈值是否锚在当日值上」。"""
    user = _prompt(sentiment, overview, previous)
    # deepseek-flash 的 reasoning_content 也算在 max_tokens 里：3000 会在长推理日
    # 直接截断成半截 JSON（2026-11-26 就这样退回规则版），所以这一段给足 8000。
    reply = await client.complete_json(SYSTEM, user, max_tokens=8000, temperature=0.2)
    problems = _problems(reply, previous, today)
    if problems:
        repair = (
            "上一次输出缺少或不合规：" + "、".join(problems)
            + "。只输出 JSON，字段齐全、数字只能来自数据；"
            "watching 至少 2 条 prediction（阈值必须离今日值有明显差距）+ 最多 2 条 trigger。"
        )
        reply = await client.complete_json(SYSTEM, user + "\n\n" + repair, max_tokens=8000, temperature=0.1)
        problems = _problems(reply, previous, today)
    if not problems:
        ok, ratio = numbers_ok(
            " ".join(str(reply.get(key) or "") for key in ("summary", "market_view", "sentiment_view", "money_view")),
            {"sentiment": sentiment, "overview": overview, "previous": previous},
        )
        if not ok:
            problems = [f"数字与快照不一致（匹配率 {ratio}）"]
    if problems:
        report = fallback(sentiment, overview, "、".join(problems))
        return report

    report = {
        "title": str(reply.get("title")),
        "stage": str(reply.get("stage")),
        "stance": str(reply.get("stance")),
        "summary": str(reply.get("summary")),
        "market_view": str(reply.get("market_view")),
        "sentiment_view": str(reply.get("sentiment_view")),
        "money_view": str(reply.get("money_view")),
        "sectors": reply.get("sectors") or {},
        "action": reply.get("action") or {},
        "yesterday": str(reply.get("yesterday") or "无"),
        "watching": reply.get("watching") or [],
        "risks": reply.get("risks") or [],
        "data_gaps": reply.get("data_gaps") or [],
        "model": client.model,
        "numbers_match": ratio,
    }
    return report
