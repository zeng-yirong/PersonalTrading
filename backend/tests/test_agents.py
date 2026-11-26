"""Tests for the multi-agent research layer (no network, no real LLM)."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from app.agents import prompts
from app.agents.graph import build_graph
from app.agents.llm import ChatClient, _extract_json
from app.agents.runner import build_report


# ------------------------------------------------------------------ parsing
def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = '```json\n{"a": 2, "b": [1, 2]}\n```'
    assert _extract_json(text) == {"a": 2, "b": [1, 2]}


def test_extract_json_after_prose():
    text = '我们需要构建论证。最终答案如下：\n{"thesis": "x", "conviction": 50}'
    assert _extract_json(text)["thesis"] == "x"


def test_extract_json_ignores_truncated():
    assert _extract_json('{"thesis": "x", "evidence": ["a"') is None


def test_extract_json_rejects_arrays():
    assert _extract_json("[1, 2, 3]") is None


# ------------------------------------------------------------------- canvas
class FakeClient:
    """Returns a schema-appropriate payload based on which prompt it was given."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.model = "fake-model"

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        self.calls.append(system[:40])
        if system == prompts.SYSTEM_FUNDAMENTAL:
            return {"stance": "中性", "score": 5, "confidence": 60, "summary": "基本面无明显方向",
                    "key_points": ["ROE 10%"], "risks": ["估值偏高"], "data_gaps": []}
        if system == prompts.SYSTEM_TECHNICAL:
            return {"stance": "看多", "score": 40, "confidence": 55, "summary": "均线多头",
                    "key_points": ["MA20 10.0"], "risks": ["偏离均线"], "data_gaps": []}
        if system == prompts.SYSTEM_FLOW:
            return {"stance": "看多", "score": 30, "confidence": 50, "summary": "主力净流入",
                    "key_points": ["20日净流入 1 亿"], "risks": ["持续性未知"], "data_gaps": []}
        if system == prompts.SYSTEM_SENTIMENT:
            return {"stance": "中性", "score": 0, "confidence": 50, "summary": "无异常",
                    "key_points": ["无龙虎榜"], "risks": ["关注度低"], "data_gaps": []}
        if system == prompts.SYSTEM_EVENT:
            return {"stance": "中性", "score": 10, "confidence": 50, "summary": "事件无冲击",
                    "key_points": ["无解禁"], "risks": ["分红偏低"], "data_gaps": []}
        if system == prompts.SYSTEM_REGIME:
            return {"stance": "看空", "score": -20, "confidence": 55, "summary": "大盘走弱",
                    "key_points": ["沪深300 -3.7%"], "risks": ["退潮"], "data_gaps": []}
        if system == prompts.SYSTEM_SECTOR:
            return {"stance": "看多", "score": 25, "confidence": 50, "summary": "板块中游",
                    "key_points": ["同业 30 只"], "risks": ["轮动过快"], "data_gaps": []}
        if system == prompts.SYSTEM_BULL:
            return {"thesis": "多头逻辑", "evidence": ["e1", "e2", "e3"],
                    "rebuttals": [{"target": "t", "rebuttal": "r"}],
                    "invalidation": "跌破 MA20", "conviction": 60}
        if system == prompts.SYSTEM_BEAR:
            return {"thesis": "空头逻辑", "evidence": ["e1", "e2", "e3"],
                    "rebuttals": [{"target": "t", "rebuttal": "r"}],
                    "invalidation": "站上上轨", "conviction": 50}
        if system == prompts.SYSTEM_RESEARCH_MANAGER:
            return {"verdict": "偏多", "leaning_score": 20, "winner": "多头",
                    "reasoning": "多头占优", "key_disagreements": ["d1"], "confidence": 55}
        if system == prompts.SYSTEM_TRADER or system.startswith(prompts.SYSTEM_TRADER):
            return {"action": "持有", "position_pct": 40, "conviction": 55,
                    "target_price": 1650, "stop_price": "1425 元",
                    "rationale": "r", "entry_zone": "10.0-10.5", "stop_condition": "9.5",
                    "horizon": "4 周", "what_would_change_my_mind": "w"}
        if system.startswith("你是风控负责人"):
            return {"decision": "批准", "final_position_pct": 30, "rationale": "ok",                    "adjustments": [], "monitor": ["m1"], "confidence": 60}
        if system.startswith("你是风控委员"):
            return {"persona": "x", "concerns": ["c1"], "position_advice_pct": 30,
                    "verdict": "同意", "reasoning": "r"}
        raise AssertionError(f"unexpected prompt: {system[:60]}")


def _fake_context() -> dict[str, Any]:
    return {
        "symbol": "600519",
        "as_of": "2026-11-20",
        "profile": {
            "symbol": "600519", "ts_code": "600519.SH", "name": "贵州茅台",
            "industry": "白酒", "board": "沪市主板", "asset_type": "stock",
            "list_date": "2001-08-27",
        },
        "price": {
            "trade_date": "2026-11-20", "last_close": 1500.0, "prev_close": 1490.0,
            "pct_chg": 0.67, "return_5d": 2.0, "return_20d": 5.0, "return_60d": 8.0,
            "high_52w": 1600.0, "low_52w": 1200.0, "drawdown_from_high": -6.25,
            "avg_amount_20d": 1e9, "bars": 250,
        },
        "technical": {
            "ma5": 1495.0, "ma10": 1480.0, "ma20": 1450.0, "ma60": 1400.0,
            "dif": 10.0, "dea": 8.0, "macd_hist": 4.0, "macd_state": "红柱",
            "rsi14": 60.0, "boll_upper": 1550.0, "boll_mid": 1450.0, "boll_lower": 1350.0,
            "trend": "多头排列", "close_vs_ma20_pct": 3.4,
        },
        "valuation": {
            "as_of": "2026-11-20", "pe_ttm": 22.0, "pb": 6.8, "ps_ttm": 10.0,
            "dv_ttm": 3.5, "total_mv": 1.8e12, "circ_mv": 1.8e12,
            "turnover_rate": 0.5, "volume_ratio": 1.0, "pe_ttm_percentile_3y": 60.0,
        },
        "fundamental": {
            "report_period": "2026-09-30", "roe": 25.0, "roe_waa": 24.0, "eps": 50.0,
            "bps": 210.0, "gross_margin": 89.0, "net_margin": 50.0, "debt_ratio": 12.0,
            "revenue_yoy": 2.0, "profit_yoy": -0.3, "quarter_roe": 6.0,
            "total_revenue": 1.3e11, "net_profit": 6.4e10, "operate_profit": 8e10,
            "rd_exp": None, "total_assets": 3e11, "total_liab": 3.6e10, "equity": 2.6e11,
            "operating_cashflow": 8.4e10,
            "forecast": {"ann_date": "2026-10-01", "type": "略增", "change_min": 5.0,
                         "change_max": 5.0, "summary": "略增"},
            "express": None,
        },
        "flow": {
            "as_of": "2026-11-20", "net_mf_amount_1d": 10000.0, "elg_net": -1000.0,
            "lg_net": 8000.0, "net_mf_amount_20d": 120000.0,
            "margin_balance": 1.4e10, "margin_change_20d_pct": -11.0,
            "chip_cost_50pct": 1340.0, "chip_cost_85pct": 1478.0, "chip_winner_rate": 86.0,
        },
        "sentiment": {
            "limit_up_90d": 0, "limit_down_90d": 0, "lhb_count_90d": 0,
            "lhb_net_amount_90d": None, "inst_seat_count_90d": 0,
            "inst_net_buy_90d": None, "northbound_days_90d": 40,
            "northbound_net_90d": None, "latest_limit": None,
        },
        "market": {
            "as_of": "2026-11-20",
            "benchmarks": [{"name": "沪深300", "ts_code": "000300.SH", "close": 3950.0,
                            "return_20d": -3.7}],
        },
        "regime": {
            "as_of": "2026-11-20",
            "indices": [{"name": "上证指数", "ts_code": "000001.SH", "close": 3500.0,
                         "return_5d": -1.0, "return_20d": -4.2, "return_60d": -6.0}],
            "breadth": {"limit_up": 40, "limit_down": 12, "broken": 8},
            "turnover": {"amount": 1.1e12, "avg_amount_20d": 1.0e12, "ratio_vs_20d": 1.1},
            "margin": {"balance": 1.6e12, "change_20d_pct": -2.0},
        },
        "sector": {
            "rotation_top": [{"ts_code": "801010.SI", "name": "农林牧渔", "return_20d": 8.0}],
            "rotation_bottom": [{"ts_code": "801030.SI", "name": "基础化工", "return_20d": -6.0}],
            "rotation_count": 31,
            "peer": {"industry": "白酒", "count": 20, "median_pe_ttm": 20.0, "median_pb": 5.0,
                     "median_mv": 5e10, "own_pe_ttm": 22.0, "own_pb": 6.8, "up_ratio": 0.6},
        },
        "events": {
            "as_of": "2026-11-20",
            "forecasts": [{"ann_date": "2026-10-01", "end_date": "2026-09-30", "type": "略增",
                           "change_min": 5.0, "change_max": 5.0}],
            "express": [],
            "dividends": [{"ann_date": "2026-06-01", "end_date": "2025-12-31",
                           "progress": "实施", "cash_div": 20.0, "stock_div": 0.0,
                           "ex_date": "2026-06-20"}],
            "upcoming_float": {"count": 0, "shares": None, "next_date": None},
            "insider": {"count": 2, "sell_count": 2, "buy_count": 0},
            "block_trades": {"count": 1, "amount": 5e7},
            "surveys": {"count": 3, "latest": "2026-10-10"},
        },
        "attention": {
            "as_of": "2026-11-20",
            "hot": {
                "同花顺": {"rank": 12, "hot_value": 1.2e6, "concept": "白酒", "data_type": "A股",
                        "days_in_20d": 5},
                "东方财富": {"rank": None, "hot_value": None, "concept": None, "data_type": None,
                         "days_in_20d": 0},
            },
            "market_concepts": [{"name": "机器人概念", "limit_up": 8, "streak": "2天2板",
                                 "pct_chg": -5.2}],
            "holder_number": {"latest_period": "2026-09-30", "count": 120000,
                              "change_pct": -3.5},
        },
    }


def test_graph_runs_every_role_and_merges_parallel_analysts():
    client = FakeClient()
    graph = build_graph(client)

    state = {
        "symbol": "600519",
        "name": "贵州茅台",
        "rounds": 1,
        "context": _fake_context(),
        "analyses": {},
        "debate": [],
        "errors": [],
        "model": "fake-model",
    }
    result = asyncio.run(graph.ainvoke(state))

    # All seven analysts survived the parallel merge.
    assert set(result["analyses"]) == {
        "market", "fundamental", "flow", "attention", "event", "regime", "sector"
    }
    assert result["errors"] == []
    # 1 round = one bull + one bear.
    assert [item["role"] for item in result["debate"]] == ["bull", "bear"]
    assert result["consensus"]["verdict"] == "偏多"
    assert result["proposal"]["action"] == "持有"
    assert len(result["risk_reviews"]) == 3
    assert result["verdict"]["decision"] == "批准"
    # 7 analysts + 2 debaters + manager + trader + 3 risk + risk manager
    assert len(client.calls) == 15


def test_graph_runs_two_debate_rounds():
    client = FakeClient()
    graph = build_graph(client)
    state = {
        "symbol": "600519", "name": "贵州茅台", "rounds": 2,
        "context": _fake_context(), "analyses": {}, "debate": [], "errors": [],
        "model": "fake-model",
    }
    result = asyncio.run(graph.ainvoke(state))
    assert [item["role"] for item in result["debate"]] == ["bull", "bear", "bull", "bear"]


# ------------------------------------------------------------------- report
def test_report_contains_every_section():
    client = FakeClient()
    state = {
        "symbol": "600519", "name": "贵州茅台", "rounds": 1,
        "context": _fake_context(), "analyses": {}, "debate": [], "errors": [],
        "model": "fake-model",
    }
    result = asyncio.run(build_graph(client).ainvoke(state))
    report = build_report(result, _fake_context(), {"calls": 12, "total_tokens": 1000})

    for heading in ("## 结论", "## 分析师观点", "## 多空辩论", "## 交易员草案",
                    "## 目标与赔率（推算）", "## 风控审查", "## 数据快照"):
        assert heading in report, heading
    # 收盘 1500，目标 1650 / 止损 1425 → +10.00% / -5.00%，赔率 2:1
    assert "目标价：**1,650.00 元**（较收盘 +10.00%）" in report
    assert "止损价：1,425.00 元（较收盘 -5.00%）" in report
    assert "2.00 : 1" in report
    assert "推算" in report
    assert "贵州茅台（600519）" in report


def test_build_plan_parses_strings_and_computes_ratios():
    from app.agents.runner import build_plan

    plan = build_plan({"target_price": "1650 元", "stop_price": "1425"}, 1500.0)
    assert plan["target_price"] == 1650.0
    assert plan["expected_return_pct"] == pytest.approx(10.0)
    assert plan["downside_pct"] == pytest.approx(-5.0)
    assert plan["reward_risk"] == pytest.approx(2.0)

    empty = build_plan({"target_price": "数据不足", "stop_price": None}, 1500.0)
    assert empty["target_price"] is None and empty["expected_return_pct"] is None


def test_report_survives_missing_payloads():
    """A failed role must degrade the report, not crash it."""
    state = {
        "symbol": "600519", "name": "贵州茅台", "rounds": 1, "model": "m",
        "analyses": {}, "debate": [], "consensus": {}, "proposal": {},
        "risk_reviews": [], "verdict": {}, "errors": ["fundamental: boom"],
    }
    report = build_report(state, _fake_context(), {"calls": 0, "total_tokens": 0})
    assert "## 运行异常" in report
    assert "fundamental: boom" in report


# ------------------------------------------------------------------ llm bits
def test_chat_client_reports_missing_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.deepseek_api_key", "")
    client = ChatClient(api_key="", model="m")
    ok, reason = client.available()
    assert ok is False
    assert "API_KEY" in reason


def test_usage_accumulates():
    from app.agents.llm import Usage

    usage = Usage()
    usage.add(10, 5)
    usage.add(3, 1)
    assert usage.as_dict() == {
        "prompt_tokens": 13,
        "completion_tokens": 6,
        "total_tokens": 19,
        "calls": 2,
    }
