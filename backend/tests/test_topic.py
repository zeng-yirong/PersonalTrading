"""Tests for thematic research: snapshot maths, schema validation, rendering.

The model is faked everywhere: what needs testing is that the numbers are right,
that a hollow reply is rejected instead of rendered, and that the memo keeps the
panel's data gaps.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.llm import Usage
from app.config import settings
from app.data import store
from app.db import init_db
from app.topic import context, service
from app.topic.service import TopicError


# ---------------------------------------------------------------- data fixture
@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()

    days = [date(2026, 9, 1) + timedelta(days=index) for index in range(70)]
    store.upsert_calendar([(day, True) for day in days], "test")
    store.upsert_symbols([
        {"symbol": "600519", "ts_code": "600519.SH", "name": "贵州茅台",
         "industry": "白酒", "asset_type": "stock"},
        {"symbol": "000858", "ts_code": "000858.SZ", "name": "五粮液",
         "industry": "白酒", "asset_type": "stock"},
        {"symbol": "600809", "ts_code": "600809.SH", "name": "山西汾酒",
         "industry": "白酒", "asset_type": "stock"},
        {"symbol": "601398", "ts_code": "601398.SH", "name": "工商银行",
         "industry": "银行", "asset_type": "stock"},
        {"symbol": "600036", "ts_code": "600036.SH", "name": "招商银行",
         "industry": "银行", "asset_type": "stock"},
    ])

    prices = {
        "600519": [1500 + index for index in range(len(days))],      # 白酒上涨
        "000858": [150 + index * 0.5 for index in range(len(days))],
        "600809": [200 - index * 0.2 for index in range(len(days))],
        "601398": [6 - index * 0.01 for index in range(len(days))],  # 银行下跌
        "600036": [35 - index * 0.1 for index in range(len(days))],
    }
    store.upsert_daily(
        [
            {"symbol": symbol, "trade_date": day, "open": price, "close": price,
             "high": price * 1.01, "low": price * 0.99, "amount": 1e8,
             "adj_factor": 1.0}
            for symbol, series in prices.items()
            for day, price in zip(days, series)
        ],
        "test",
    )

    def table(name, columns, key, values):
        store.ensure_dataset_table(name, columns, key)
        store.write_dataset(name, list(columns), key, values)

    table(
        "daily_basic",
        {"symbol": "VARCHAR", "trade_date": "DATE", "pe_ttm": "DOUBLE", "pb": "DOUBLE"},
        ("symbol", "trade_date"),
        [{"symbol": symbol, "trade_date": day,
          "pe_ttm": 30.0 if symbol.startswith("600519") else 8.0,
          "pb": 5.0 if symbol.startswith("600519") else 0.8}
         for symbol in prices for day in days],
    )
    table(
        "moneyflow",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "net_mf_amount": "DOUBLE"},
        ("ts_code", "trade_date"),
        [{"ts_code": f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ",
          "trade_date": day,
          "net_mf_amount": 1000.0 if symbol.startswith("600519") else -500.0}
         for symbol in prices for day in days],
    )
    # 最后一天只有 1 只两融标的：这是同步中断留下的残缺日，必须被跳过。
    table(
        "margin_detail",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "rzye": "DOUBLE"},
        ("ts_code", "trade_date"),
        [{"ts_code": ts_code, "trade_date": day, "rzye": 1e9}
         for ts_code in ("600519.SH", "000858.SZ", "600809.SH", "601398.SH", "600036.SH")
         for day in days[:-1]]
        + [{"ts_code": "600519.SH", "trade_date": days[-1], "rzye": 1e9}],
    )
    table(
        "limit_list_d",
        {"trade_date": "DATE", "ts_code": "VARCHAR", "limit": "VARCHAR"},
        ("trade_date", "ts_code"),
        [{"trade_date": days[-1], "ts_code": "600519.SH", "limit": "U"},
         {"trade_date": days[-1], "ts_code": "000858.SZ", "limit": "U"},
         {"trade_date": days[-1], "ts_code": "601398.SH", "limit": "D"}],
    )
    table(
        "hsgt_top10",
        {"trade_date": "DATE", "ts_code": "VARCHAR", "net_amount": "DOUBLE"},
        ("trade_date", "ts_code"),
        [{"trade_date": day, "ts_code": "600519.SH", "net_amount": None}
         for day in days],
    )
    table(
        "limit_cpt_list",
        {"ts_code": "VARCHAR", "name": "VARCHAR", "trade_date": "DATE",
         "up_nums": "DOUBLE", "days": "DOUBLE", "pct_chg": "DOUBLE", "up_stat": "VARCHAR"},
        ("ts_code", "trade_date"),
        [{"ts_code": "885001.TI", "name": "白酒概念", "trade_date": days[-1],
          "up_nums": 5.0, "days": 2.0, "pct_chg": 1.2, "up_stat": "2天2板"}],
    )
    table(
        "ths_hot",
        {"trade_date": "DATE", "data_type": "VARCHAR", "ts_code": "VARCHAR",
         "ts_name": "VARCHAR", "rank": "BIGINT"},
        ("trade_date", "data_type", "ts_code"),
        [{"trade_date": days[-1], "data_type": "概念板块", "ts_code": "885002.TI",
          "ts_name": "白酒概念", "rank": 3}],
    )
    table(
        "sw_daily",
        {"ts_code": "VARCHAR", "name": "VARCHAR", "trade_date": "DATE",
         "pct_change": "DOUBLE", "pe": "DOUBLE", "pb": "DOUBLE", "amount": "DOUBLE"},
        ("ts_code", "trade_date"),
        [{"ts_code": "801120.SI", "name": "食品饮料", "trade_date": days[-2],
          "pct_change": 0.8, "pe": 25.0, "pb": 3.0, "amount": 1e6},
         {"ts_code": "801780.SI", "name": "银行", "trade_date": days[-2],
          "pct_change": -0.4, "pe": 6.0, "pb": 0.6, "amount": 2e6}],
    )
    table(
        "index_daily",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
        ("ts_code", "trade_date"),
        [{"ts_code": code, "trade_date": day, "close": 4000 + index * 3}
         for code in ("000300.SH", "000001.SH")
         for index, day in enumerate(days)],
    )
    return days


# -------------------------------------------------------------------- matching
def test_match_topic_finds_industry_and_concept():
    matched = context.match_topic(
        "白酒行业还能拿吗", ["白酒", "银行", "半导体"], ["白酒概念", "AI应用"]
    )
    assert matched["industries"] == ["白酒"]
    assert matched["concepts"] == []
    # 题材名必须完整出现在问题里才算命中，避免单字误匹配
    assert context.match_topic("白酒概念还有戏吗", ["白酒"], ["白酒概念"])["concepts"] == ["白酒概念"]


def test_match_topic_on_market_question_matches_nothing():
    matched = context.match_topic("市场情绪现在怎么样", ["白酒", "银行"], ["AI应用"])
    assert matched["industries"] == []


# --------------------------------------------------------------------- context
def test_context_numbers(panel):
    payload = context.build("白酒行业还能拿吗")
    assert payload["as_of"] == panel[-1].isoformat()
    breadth = payload["market"]["breadth"]
    assert breadth["stocks"] == 5
    # 两只白酒上涨、一只白酒 + 两只银行下跌（用前收盘推算）
    assert breadth["up"] == 2 and breadth["down"] == 3
    assert breadth["up_ratio"] == pytest.approx(40.0, abs=0.1)

    limits = payload["market"]["limits"]
    assert limits["limit_up"] == 2 and limits["limit_down"] == 1

    turnover = payload["market"]["turnover"]
    assert turnover["amount_yi"] == pytest.approx(5.0, abs=0.1)  # 5 只 × 1 亿

    matched = payload["matched"]
    assert matched["industries"] == ["白酒"]
    detail = payload["industry_detail"][0]
    assert detail["industry"] == "白酒"
    assert detail["count"] == 3
    assert detail["stats"]["ret20"] is not None


def test_context_skips_a_partial_margin_day(panel):
    payload = context.build("白酒")
    margin = payload["market"]["margin"]
    # 最后一天只有 1 只标的，应回退到倒数第二天
    assert margin is not None
    assert margin["date"] == panel[-2].isoformat()
    assert panel[-1].isoformat() in margin["skipped_partial_days"]
    assert any("两融余额" in gap for gap in payload["data_gaps"])


def test_context_reports_missing_north(panel):
    payload = context.build("白酒")
    assert payload["market"]["north"] == {}  # net_amount 全为空
    assert any("北向" in gap for gap in payload["data_gaps"])


def test_context_includes_sw_l1_when_present(panel):
    payload = context.build("白酒")
    names = {row["name"] for row in payload["sw_l1"]}
    assert names == {"食品饮料", "银行"}


# ------------------------------------------------------------------ validation
def test_problems_flags_a_hollow_reply():
    assert service._problems({}) == [
        "title", "summary", "verdict", "confidence", "sections",
        "key_points", "drivers", "risks", "watching", "data_gaps",
    ]


def test_normalise_coerces_and_clamps():
    report = service._normalise(
        {
            "title": "白酒",
            "verdict": "看多",
            "confidence": "88",
            "summary": "一句话结论，带了 3 个数字。",
            "key_points": ["判断一"],
            "drivers": [{"claim": "驱动", "evidence": "数据"}],
            "sections": [{"heading": "现在", "body": "正文"}],
        }
    )
    assert report["confidence"] == 88
    assert report["key_points"] == ["判断一"]
    assert report["drivers"] == ["驱动（数据）"]
    assert report["risks"] == []


def test_normalise_defaults_an_unknown_verdict():
    report = service._normalise({"verdict": "强烈看好", "scope": "板块"})
    assert report["verdict"] == "无法评估"
    assert report["scope"] == "其他"


# --------------------------------------------------------------------- service
GOOD_REPLY = {
    "title": "白酒抗跌但缩圈",
    "scope": "行业",
    "verdict": "中性",
    "confidence": 58,
    "summary": "白酒 ret20 -1.8%，强于上证 -4.3%；但 ret5 -7.8%，短期动能已断。",
    "key_points": [{"claim": "相对抗跌", "evidence": "ret20 差 2.5 个百分点"}],
    "drivers": ["防御资金流入"],
    "risks": ["龙头补跌"],
    "watching": ["up_ratio_5d 是否回升"],
    "data_gaps": ["北向缺失"],
    "sections": [
        {"heading": "现在发生了什么", "body": "白酒 3 只，5 日全部下跌。"},
        {"heading": "数据支持什么", "body": "20 日资金净流入 1000 万。"},
    ],
}


class FakeClient:
    model = "fake-model"

    def __init__(self, reply: dict):
        self.reply = reply
        self.usage = Usage()
        self.calls = 0

    def available(self) -> tuple[bool, str]:
        return True, ""

    async def complete_json(self, system: str, user: str, **_: object) -> dict:
        self.calls += 1
        self.usage.add(100, 50)
        return self.reply


def test_research_renders_and_persists(panel, monkeypatch):
    fake = FakeClient(GOOD_REPLY)
    monkeypatch.setattr(service, "_client", lambda model="": fake)

    report = service.run_sync("白酒行业还能拿吗")
    assert report["id"] >= 1
    assert report["verdict"] == "中性"
    assert f"截至 {panel[-1].isoformat()}" in report["markdown"]
    assert "## 摘要" in report["markdown"] and "## 观察信号" in report["markdown"]
    # 面板缺口必须出现在报告里，即使模型漏写
    assert any("北向" in gap for gap in report["data_gaps"])
    assert fake.calls == 1

    listed = service.history(5)
    assert listed[0]["id"] == report["id"]
    detail = service.detail(report["id"])
    assert detail["report"]["title"] == "白酒抗跌但缩圈"


def test_research_retries_once_on_a_hollow_reply(panel, monkeypatch):
    class Flaky(FakeClient):
        async def complete_json(self, system: str, user: str, **_: object) -> dict:
            self.calls += 1
            self.usage.add(100, 50)
            return {} if self.calls == 1 else GOOD_REPLY

    fake = Flaky({})
    monkeypatch.setattr(service, "_client", lambda model="": fake)
    report = service.run_sync("白酒")
    assert fake.calls == 2
    assert report["verdict"] == "中性"


def test_research_gives_up_after_two_hollow_replies(panel, monkeypatch):
    monkeypatch.setattr(service, "_client", lambda model="": FakeClient({}))
    with pytest.raises(TopicError, match="不符合报告结构"):
        service.run_sync("白酒")


def test_research_rejects_a_short_topic(panel):
    with pytest.raises(TopicError, match="太短"):
        service.run_sync("白")


# ----------------------------------------------------------------- http layer
def test_http_contract(panel, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(service, "_client", lambda model="": FakeClient(GOOD_REPLY))
    client = TestClient(app)

    meta = client.get("/api/topics/meta").json()
    assert meta["as_of"] == panel[-1].isoformat()
    assert meta["examples"] and meta["industries"]

    snapshot = client.get("/api/topics/context", params={"topic": "白酒"}).json()
    assert snapshot["matched"]["industries"] == ["白酒"]
    assert snapshot["summary"]["breadth"]["stocks"] == 5

    created = client.post("/api/topics/research", json={"topic": "白酒还能拿吗"})
    assert created.status_code == 200
    body = created.json()
    assert {"id", "report", "markdown", "usage", "verdict"} <= set(body)

    reports = client.get("/api/topics/reports").json()
    assert reports["count"] >= 1
    assert client.get(f"/api/topics/reports/{body['id']}").status_code == 200
    assert client.get("/api/topics/reports/99999").status_code == 404
    assert client.post("/api/topics/research", json={"topic": "白"}).status_code == 400

DAILY_REPLY = {
    "title": "情绪回落转退潮，防守等修复",
    "stage": "退潮",
    "stance": "防守",
    "summary": "情绪 39.0，较前日 61.6 回落 22.6；上涨占比 35.01%，涨停 55 家、跌停 2 家，成交额 16678 亿元。",
    "market_view": "上证涨 0.27%，成交额 16678 亿元。",
    "sentiment_view": "情绪 39.0，封板率 44.7%。",
    "money_view": "北向净流入 256569.83 万元。",
    "sectors": {
        "leaders": [{"name": "银行", "pct": 1.2, "note": "防御资金"}],
        "laggards": [{"name": "军工", "pct": -2.1, "note": "高位回落"}],
        "diffusion": "行业上涨占比 42.6%",
        "concentration": "前 5 占比 40.7%",
        "continuity": "昨日领涨今日多数回落",
    },
    "action": {
        "position_range": "0-2 成",
        "buy": ["等涨停回到 66 家以上再考虑"],
        "sell": ["反弹到 5 日线先减仓"],
        "avoid": ["不抄跌停板"],
    },
    "yesterday": "无",
    "watching": [
        {"kind": "prediction", "metric": "limit_up", "op": ">=", "value": 66, "text": "次日涨停回到 66 家以上"},
        {"kind": "prediction", "metric": "amount_yi", "op": ">=", "value": 16678, "text": "成交额维持 16678 亿元以上"},
        {"kind": "trigger", "metric": "score", "op": ">=", "value": 45, "text": "情绪回到 45 以上才加仓"},
    ],
    "risks": ["量能不足"],
    "data_gaps": [],
}


def test_daily_brief_caches_within_the_same_trade_date(panel, monkeypatch):
    import asyncio

    fake = FakeClient(DAILY_REPLY)
    monkeypatch.setattr(service, "_client", lambda model="": fake)

    first = asyncio.run(service.daily_brief())
    assert first["cached"] is False
    assert fake.calls == 1
    assert first["report"]["report"]["stage"] in {"冰点", "修复", "中性", "扩散", "偏热", "亢奋", "退潮"}

    cached = asyncio.run(service.daily_brief())
    assert cached["cached"] is True
    assert cached["report"]["id"] == first["report"]["id"]
    assert fake.calls == 1          # 没有再次调用模型

    forced = asyncio.run(service.daily_brief(force=True))
    assert forced["cached"] is False
    assert fake.calls == 2


def test_daily_brief_falls_back_when_model_is_unstructured(panel, monkeypatch):
    import asyncio

    fake = FakeClient({})
    monkeypatch.setattr(service, "_client", lambda model="": fake)
    result = asyncio.run(service.daily_brief())
    report = result["report"]["report"]
    assert report.get("fallback") is True
    assert report["stage"]
    assert "规则版" in result["report"]["title"]
    assert fake.calls == 2          # 原调用 + 一次修复


def test_daily_today_http_contract(panel, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    fake = FakeClient(GOOD_REPLY)
    monkeypatch.setattr(service, "_client", lambda model="": fake)
    client = TestClient(app)
    empty = client.get("/api/topics/today").json()
    assert empty["report"] is None
    created = client.post("/api/topics/today", json={}).json()
    assert created["cached"] is False
    again = client.get("/api/topics/today").json()
    assert again["cached"] is True and again["report"]["id"] == created["report"]["id"]

def test_judge_signal_statuses():
    from app.topic import daily

    metrics = {"limit_up": 55.0, "amount_yi": 16000.0}
    judged = daily.judge(
        [
            {"kind": "prediction", "metric": "limit_up", "op": ">=", "value": 50, "text": "涨停≥50"},
            {"kind": "prediction", "metric": "limit_up", "op": "<=", "value": 50, "text": "涨停≤50"},
            {"kind": "prediction", "metric": "score", "op": ">=", "value": 20, "text": "分数缺失"},
            {"kind": "trigger", "metric": "limit_up", "op": ">=", "value": 50, "text": "满足才买"},
            {"kind": "trigger", "metric": "limit_up", "op": ">=", "value": 90, "text": "没满足"},
            "旧格式观察",
        ],
        metrics,
    )
    assert [item["status"] for item in judged] == [
        "兑现",
        "落空",
        "无法判定",
        "已触发",
        "未触发",
        "无法判定",
    ]
    # 历史记录没有 kind，记作 legacy：照常判定，但不进任何分母。
    assert judged[-1]["kind"] == "legacy"


def test_prediction_anchored_at_today_is_rejected():
    """把今天的值原样写成"明天≥今天"是硬币，不是预测。"""
    from app.topic import daily

    today = {"score": 34.6, "limit_up": 65.0, "amount_yi": 13259.9}
    payload = dict(DAILY_REPLY)
    payload["watching"] = [
        {"kind": "prediction", "metric": "score", "op": ">=", "value": 34.6, "text": "情绪不破今日"},
        {"kind": "prediction", "metric": "limit_up", "op": ">=", "value": 80, "text": "涨停回到 80 家"},
    ]
    problems = daily._problems(payload, None, today)
    assert any("anchored" in item for item in problems)

    payload["watching"][0]["value"] = 40  # 离今日值 5.4 分，超过 3 分步长
    assert not any("anchored" in item for item in daily._problems(payload, None, today))


def test_watching_needs_two_predictions():
    from app.topic import daily

    payload = dict(DAILY_REPLY)
    payload["watching"] = [
        {"kind": "trigger", "metric": "score", "op": ">=", "value": 45, "text": "a"},
        {"kind": "trigger", "metric": "limit_up", "op": ">=", "value": 80, "text": "b"},
    ]
    problems = daily._problems(payload, None, {"score": 30.0, "limit_up": 65.0})
    assert any("prediction" in item for item in problems)


def test_daily_review_separates_predictions_from_triggers(panel):
    """命中率只统计预测型；触发型进触发率，不再被记成"判断错误"。"""
    from app.topic import store as topic_store

    topic_store.save(
        {
            "topic": service.DAILY_TOPIC,
            "title": "t",
            "scope": "大盘",
            "verdict": "中性",
            "confidence": 30,
            "model": "fake",
            "as_of": panel[-2].isoformat(),
            "report": {
                "watching": [
                    {"kind": "prediction", "metric": "limit_up", "op": ">=", "value": 999, "text": "必落空"},
                    {"kind": "trigger", "metric": "limit_up", "op": ">=", "value": 0, "text": "必触发"},
                ]
            },
            "markdown": "",
        }
    )
    result = service.daily_review(3)
    accuracy = result["accuracy"]
    assert accuracy["prediction"] == {"hits": 0, "total": 1, "rate": 0.0}
    assert accuracy["trigger"] == {"fired": 1, "total": 1, "rate": 100.0}
    assert accuracy["total"] == 1          # 兼容旧字段：只有预测进分母


def test_daily_review_empty_history(panel):
    result = service.daily_review(5)
    assert result["reviews"] == []
    assert result["accuracy"]["total"] == 0
    assert result["accuracy"]["rate"] is None
