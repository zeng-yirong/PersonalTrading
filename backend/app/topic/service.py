"""Thematic research: panel snapshot → one structured memo → markdown + record.

The model is a writer, not a data source. Everything numeric comes from
``context.build``; this module only decides what to show it, validates that the
reply covers the schema, and renders it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional

from app.agents.llm import ChatClient, LLMError
from app.data import store as market_store
from app.market import overview as market_overview
from app.market import sentiment as sentiment_series
from app.topic import context, daily, prompts, store

MAX_TOPIC_CHARS = 80
MIN_TOPIC_CHARS = 2


class TopicError(ValueError):
    """Invalid topic or unusable model reply — surfaced as HTTP 400."""


def _client(model: str = "") -> ChatClient:
    return ChatClient(model=model)


async def research(
    topic: str, model: str = "", source_run_id: Optional[int] = None
) -> dict[str, Any]:
    text = (topic or "").strip()
    if len(text) < MIN_TOPIC_CHARS:
        raise TopicError("主题太短，至少写 2 个字")
    if len(text) > MAX_TOPIC_CHARS:
        raise TopicError(f"主题太长，控制在 {MAX_TOPIC_CHARS} 字以内")
    if source_run_id is not None:
        from app.agents import store as agent_store

        if agent_store.get_run(int(source_run_id)) is None:
            raise TopicError(f"推演 #{source_run_id} 不存在")

    client = _client(model)
    available, reason = client.available()
    if not available:
        raise LLMError(reason)

    snapshot = context.build(text)
    payload = json.dumps(_slim(snapshot), ensure_ascii=False, separators=(",", ":"))
    user = prompts.build_user(text, payload, snapshot.get("data_gaps") or [])
    reply = await client.complete_json(
        prompts.SYSTEM, user, max_tokens=4096, temperature=0.2
    )
    problems = _problems(reply)
    if problems:
        repair = prompts.REPAIR.format(
            missing="、".join(problems), required="、".join(prompts.REQUIRED)
        )
        reply = await client.complete_json(
            prompts.SYSTEM,
            f"{user}\n\n{repair}",
            max_tokens=4096,
            temperature=0.1,
        )
        problems = _problems(reply)
        if problems:
            raise TopicError(
                "模型输出不符合报告结构（缺少或为空："
                + "、".join(problems)
                + "），这次没有保存。可以重试，或把主题问得更具体。"
            )
    report = _normalise(reply)

    report["data_gaps"] = _merge_gaps(report.get("data_gaps") or [], snapshot)
    usage = client.usage.as_dict()
    markdown = render(report, snapshot)

    record = {
        "topic": text,
        "title": report["title"],
        "scope": report["scope"],
        "verdict": report["verdict"],
        "confidence": report["confidence"],
        "model": client.model,
        "as_of": snapshot["as_of"],
        "report": report,
        "markdown": markdown,
        "context_summary": context.summary(snapshot),
        "data_gaps": snapshot.get("data_gaps") or [],
        "usage": usage,
        "source_run_id": source_run_id,
    }
    record["prompt_tokens"] = usage["prompt_tokens"]
    record["completion_tokens"] = usage["completion_tokens"]
    record["id"] = store.save(record)
    record["created_at"] = _created_at(record["id"])
    return record



# 一键「今日复盘」用的是固定主题文本，这样缓存可以直接按 topic 命中。
DAILY_TOPIC = "今日市场情绪与所处阶段"


def _latest_trade_date():
    with market_store.session(read_only=True) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    return row[0] if row and row[0] else None


def daily_cached() -> dict[str, Any]:
    """今天已生成过就直接返回，不调用模型、不花钱。"""
    latest = _latest_trade_date()
    item = store.latest_for_topic(DAILY_TOPIC)
    as_of = latest.isoformat() if latest else None
    if item is None:
        return {"report": None, "cached": False, "as_of": as_of, "stale": False}
    return {
        "report": item,
        "cached": True,
        "as_of": as_of,
        "stale": item.get("as_of") != as_of,
    }


async def daily_brief(force: bool = False, model: str = "") -> dict[str, Any]:
    """Generate (or reuse) today's market brief: sentiment, stage, sectors.

    专用 prompt + 规则兜底：模型结构化失败时不再报错，而是返回规则版摘要。
    """
    status = daily_cached()
    if status["report"] and not force and not status["stale"]:
        return status

    sentiment = sentiment_series.series()
    client = _client(model)
    available, reason = client.available()
    if not available:
        raise LLMError(reason)

    overview = market_overview.overview()
    previous = _previous_brief(sentiment.get("as_of"))
    today_metrics = sentiment_series.metrics_on(sentiment.get("as_of"))
    report = await daily.generate(
        client, sentiment, overview, previous, today=today_metrics
    )
    usage = client.usage.as_dict()
    markdown = daily.render(report, sentiment)
    verdict = daily._stage_verdict(report.get("stage") or "中性")
    record = {
        "topic": DAILY_TOPIC,
        "title": report.get("title") or "今日市场复盘",
        "scope": "大盘",
        "verdict": verdict,
        "confidence": int(round(float(sentiment.get("score") or 0))),
        "model": report.get("model") or client.model,
        "as_of": sentiment.get("as_of"),
        "report": report,
        "markdown": markdown,
        "context_summary": {"sentiment": sentiment.get("components")},
        "data_gaps": report.get("data_gaps") or [],
        "usage": usage,
        "source_run_id": None,
    }
    record["prompt_tokens"] = usage["prompt_tokens"]
    record["completion_tokens"] = usage["completion_tokens"]
    record["id"] = store.save(record)
    record["created_at"] = _created_at(record["id"])
    return {
        "report": record,
        "cached": False,
        "as_of": sentiment.get("as_of"),
        "stale": False,
        "sentiment": sentiment,
    }


def _previous_brief(as_of: Any) -> Any:
    """上一份复盘（用于「昨日判断对照」）。"""
    for item in store.list_for_topic(DAILY_TOPIC, 4):
        if str(item.get("as_of")) == str(as_of):
            continue
        detail = store.get(item["id"]) or {}
        payload = detail.get("report") or {}
        return {
            "as_of": item.get("as_of"),
            "stage": payload.get("stage"),
            "summary": payload.get("summary"),
            "watching": payload.get("watching"),
            "action": payload.get("action"),
            "stance": payload.get("stance"),
        }
    return None


def daily_review(limit: int = 8) -> dict[str, Any]:
    """Judge each past brief's watching list against the next trading day."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in store.list_for_topic(DAILY_TOPIC, max(limit * 3, 12)):
        day = str(item.get("as_of"))
        if day in seen:
            continue
        seen.add(day)
        rows.append(item)
        if len(rows) >= limit:
            break

    days = market_store.get_trade_days()
    index = {day.isoformat(): position for position, day in enumerate(days)}
    with market_store.session(read_only=True) as conn:
        latest_row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    latest = latest_row[0] if latest_row and latest_row[0] else None
    reviews: list[dict[str, Any]] = []
    hits = total = 0
    fired = triggers = 0
    legacy = 0
    for item in rows:
        detail = store.get(item["id"]) or {}
        payload = detail.get("report") or {}
        signals = payload.get("watching") or []
        position = index.get(str(item.get("as_of")))
        next_day = days[position + 1] if position is not None and position + 1 < len(days) else None
        if next_day is None or (latest is not None and next_day > latest):
            reviews.append(
                {
                    "as_of": str(item.get("as_of")),
                    "evaluated_as_of": None,
                    "signals": [],
                    "status": "待验证",
                }
            )
            continue
        judged = daily.judge(signals, sentiment_series.metrics_on(next_day))
        # 命中率只统计 prediction；trigger 是"满足才买"的条件，问的是触发率。
        predictions = [
            entry for entry in judged
            if entry["kind"] == "prediction" and entry["status"] in {"兑现", "落空"}
        ]
        trigger_rows = [
            entry for entry in judged
            if entry["kind"] == "trigger" and entry["status"] in {"已触发", "未触发"}
        ]
        legacy += sum(1 for entry in judged if entry["kind"] == "legacy")
        good = sum(1 for entry in predictions if entry["status"] == "兑现")
        hits += good
        total += len(predictions)
        fired += sum(1 for entry in trigger_rows if entry["status"] == "已触发")
        triggers += len(trigger_rows)
        reviews.append(
            {
                "as_of": str(item.get("as_of")),
                "evaluated_as_of": next_day.isoformat(),
                "signals": judged,
                "hits": good,
                "total": len(predictions),
                "trigger_fired": sum(1 for entry in trigger_rows if entry["status"] == "已触发"),
                "trigger_total": len(trigger_rows),
                "status": "已判定",
            }
        )
    return {
        "reviews": reviews,
        "accuracy": {
            "hits": hits,
            "total": total,
            "rate": round(hits / total * 100, 1) if total else None,
            "prediction": {
                "hits": hits,
                "total": total,
                "rate": round(hits / total * 100, 1) if total else None,
            },
            "trigger": {
                "fired": fired,
                "total": triggers,
                "rate": round(fired / triggers * 100, 1) if triggers else None,
            },
            "legacy": {"total": legacy},
        },
    }


def daily_history(limit: int = 8) -> list[dict[str, Any]]:
    """One row per trading day (同一交易日多次重新生成时只留最新一份)。"""
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in store.list_for_topic(DAILY_TOPIC, max(limit * 3, 12)):
        day = str(row.get("as_of"))
        if day in seen:
            continue
        seen.add(day)
        output.append(row)
        if len(output) >= limit:
            break
    return output


def preview(topic: str) -> dict[str, Any]:
    """The data snapshot alone — no model call, useful for checking a topic."""
    text = (topic or "").strip()
    if len(text) < MIN_TOPIC_CHARS:
        raise TopicError("主题太短，至少写 2 个字")
    snapshot = context.build(text)
    return {
        "as_of": snapshot["as_of"],
        "matched": snapshot.get("matched"),
        "data_gaps": snapshot.get("data_gaps"),
        "summary": context.summary(snapshot),
        "market": snapshot.get("market"),
        "industry_detail": snapshot.get("industry_detail"),
        "industries": snapshot.get("industries"),
        "sw_l1": snapshot.get("sw_l1"),
        "data_dates": snapshot.get("data_dates"),
    }


def history(limit: int = 20) -> list[dict[str, Any]]:
    return store.list_reports(limit)


def detail(report_id: int) -> Optional[dict[str, Any]]:
    return store.get(report_id)


# ------------------------------------------------------------------- shaping
def _slim(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop blocks the model does not need (dates live in data_dates)."""
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"data_dates"}
    }


def _problems(payload: dict[str, Any]) -> list[str]:
    """Fields that are empty or unusable in the *raw* reply (before normalising)."""
    issues: list[str] = []
    if len(str(payload.get("title") or "").strip()) < 2:
        issues.append("title")
    if len(str(payload.get("summary") or "").strip()) < 10:
        issues.append("summary")
    if str(payload.get("verdict") or "") not in prompts.VERDICTS:
        issues.append("verdict")
    try:
        int(float(payload.get("confidence")))
    except (TypeError, ValueError):
        issues.append("confidence")
    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) < 2:
        issues.append("sections")
    for key in ("key_points", "drivers", "risks", "watching", "data_gaps"):
        if not isinstance(payload.get(key), list):
            issues.append(key)
    return issues


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce the reply into the schema's types; never invent content."""
    if not isinstance(payload, dict):
        raise TopicError("模型输出不是 JSON 对象")

    def text(key: str, fallback: str = "") -> str:
        value = payload.get(key)
        return str(value).strip() if value not in (None, "") else fallback

    def items(key: str) -> list[str]:
        value = payload.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        output = []
        for item in value:
            if isinstance(item, dict):
                label = item.get("claim") or item.get("text") or ""
                evidence = item.get("evidence") or item.get("data") or ""
                joined = f"{label}（{evidence}）" if evidence else label
                if joined.strip():
                    output.append(joined.strip())
            elif str(item).strip():
                output.append(str(item).strip())
        return output

    verdict = text("verdict", "无法评估")
    if verdict not in prompts.VERDICTS:
        verdict = "无法评估"
    scope = text("scope", "其他")
    if scope not in prompts.SCOPES:
        scope = "其他"
    try:
        confidence = int(float(payload.get("confidence") or 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    sections = []
    raw_sections = payload.get("sections")
    if isinstance(raw_sections, list):
        for item in raw_sections:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading") or "").strip()
            body = str(item.get("body") or "").strip()
            if heading or body:
                sections.append({"heading": heading or "补充", "body": body})

    return {
        "title": text("title", "主题研究"),
        "scope": scope,
        "verdict": verdict,
        "confidence": confidence,
        "summary": text("summary", "模型没有给出摘要。"),
        "key_points": items("key_points"),
        "drivers": items("drivers"),
        "risks": items("risks"),
        "watching": items("watching"),
        "data_gaps": items("data_gaps"),
        "sections": sections,
    }


def _merge_gaps(reported: list[str], snapshot: dict[str, Any]) -> list[str]:
    """Panel gaps are facts; keep them even if the model forgot one."""
    merged = list(reported)
    for gap in snapshot.get("data_gaps") or []:
        if gap not in merged:
            merged.append(gap)
    return merged


def _created_at(report_id: int) -> str:
    record = store.get(report_id)
    return record["created_at"] if record else ""


# ----------------------------------------------------------------- rendering
def render(report: dict[str, Any], snapshot: dict[str, Any]) -> str:
    lines: list[str] = [f"# {report['title']}", ""]
    lines.append(
        f"> 截至 {snapshot.get('as_of')}｜{report['scope']}｜"
        f"裁定：**{report['verdict']}**（信心 {report['confidence']}/100）"
    )
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append(report["summary"])
    lines.append("")

    def bullet_block(title: str, values: list[str]) -> None:
        if not values:
            return
        lines.append(f"## {title}")
        lines.append("")
        for value in values:
            lines.append(f"- {value}")
        lines.append("")

    bullet_block("关键判断", report["key_points"])
    bullet_block("驱动因素", report["drivers"])
    bullet_block("风险", report["risks"])
    bullet_block("观察信号", report["watching"])
    bullet_block("数据缺口", report["data_gaps"])

    if report["sections"]:
        lines.append("## 展开")
        lines.append("")
        for section in report["sections"]:
            lines.append(f"### {section['heading']}")
            lines.append("")
            lines.append(section["body"])
            lines.append("")

    dates = snapshot.get("data_dates") or {}
    matched = snapshot.get("matched") or {}
    lines.append("---")
    lines.append("")
    lines.append(
        "数据口径："
        f"个股 {dates.get('daily')}、申万行业 {dates.get('sw_daily')}、"
        f"两融 {dates.get('margin_detail')}、涨跌停 {dates.get('limit_list_d')}；"
        f"匹配行业 {('、'.join(matched.get('industries') or []) or '无')}。"
    )
    return "\n".join(lines).strip() + "\n"


def run_sync(topic: str, model: str = "") -> dict[str, Any]:
    """Blocking entry used by the CLI."""
    return asyncio.run(research(topic, model=model))
