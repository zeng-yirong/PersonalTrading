"""AI research notes powered by a DeepSeek-compatible chat API."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.db import save_note
from app.services.fundamentals import get_fundamentals
from app.services.indicators import compute_all
from app.services.market import get_kline, get_quote
from app.services.symbols import normalize_code


class ResearchError(RuntimeError):
    pass


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据不足"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _build_data_block(
    quote: dict[str, Any],
    candles: list[dict[str, Any]],
    indicators: dict[str, Any],
    fundamentals: dict[str, Any],
) -> str:
    latest = indicators.get("latest") or {}
    recent = candles[-20:]
    recent_text = "；".join(
        f"{item['trade_date']} 收 {_fmt(item['close'])}"
        for item in recent[:5] + recent[-5:]
    ) if recent else "数据不足"

    return f"""
【标的】
名称：{quote.get('name')}（{quote.get('symbol')}）
类型：{'ETF 基金' if quote.get('asset_type') == 'etf' else 'A 股'}
最新价：{_fmt(quote.get('price'))} 元
涨跌幅：{_fmt(quote.get('pct_chg'))}%
今日开盘/最高/最低：{_fmt(quote.get('open'))} / {_fmt(quote.get('high'))} / {_fmt(quote.get('low'))}
成交量：{_fmt(quote.get('volume'))} 手
成交额：{_fmt(quote.get('amount'))} 元
行情日期：{quote.get('trade_date') or '数据不足'}

【技术指标】
MA5 / MA10 / MA20 / MA60：{_fmt(latest.get('ma5'))} / {_fmt(latest.get('ma10'))} / {_fmt(latest.get('ma20'))} / {_fmt(latest.get('ma60'))}
MACD DIF / DEA / 柱：{_fmt(latest.get('dif'), 4)} / {_fmt(latest.get('dea'), 4)} / {_fmt(latest.get('macd_hist'), 4)}
RSI14：{_fmt(latest.get('rsi14'))}
BOLL 上/中/下：{_fmt(latest.get('boll_upper'))} / {_fmt(latest.get('boll_mid'))} / {_fmt(latest.get('boll_lower'))}

【近期收盘片段】
{recent_text}

【基本面快照】
报告期：{fundamentals.get('report_date') or '数据不足'}
营业收入：{_fmt(fundamentals.get('revenue'))} 元
归母净利润：{_fmt(fundamentals.get('net_profit'))} 元
ROE：{_fmt(fundamentals.get('roe'))}%
毛利率：{_fmt(fundamentals.get('gross_margin'))}%
资产负债率：{_fmt(fundamentals.get('debt_ratio'))}%
EPS / BPS：{_fmt(fundamentals.get('eps'))} / {_fmt(fundamentals.get('bps'))}
PE / PB：{_fmt(fundamentals.get('pe'))} / {_fmt(fundamentals.get('pb'))}
总市值：{_fmt(fundamentals.get('total_mv'))} 元
""".strip()


SYSTEM_PROMPT = """你是一名严谨、克制的 A 股研究助手。
你的任务是基于用户提供的数据写一份中文研究笔记。
必须遵守：
1. 只使用给定数据。数据缺失时明确写“数据不足”，禁止编造。
2. 可以给目标价、期望收益、止损止盈与买卖时点，但必须标注为“推算”并写明依据（估值锚、技术位、赔率）；不得写成保证或“必涨必跌”。
3. 区分“数据事实”和“你的推断”，推断要说明依据。
4. 结构固定为：数据概览 / 技术面观察 / 基本面观察 / 目标价与操作建议（推算） / 风险与不确定性 / 后续观察点。
5. 语言简洁，总长度 500-900 字。结尾注明“目标价与操作建议为模型推算，不保证准确，据此操作风险自担”。
"""


async def generate_research(symbol: str) -> dict[str, Any]:
    code = normalize_code(symbol)
    if not code:
        raise ResearchError("股票代码无效")
    if not settings.deepseek_api_key:
        raise ResearchError(
            "未配置 DeepSeek API Key。请在项目根目录 .env 中设置 "
            "PERSONALTRADING_DEEPSEEK_API_KEY。"
        )

    quote = await get_quote(code)
    candles = await get_kline(code, "day", 120)
    indicators = compute_all(candles)
    fundamentals = await get_fundamentals(code)
    data_block = _build_data_block(quote, candles, indicators, fundamentals)

    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"请基于以下数据写研究笔记：\n\n{data_block}",
            },
        ],
        "temperature": 0.3,
        "max_tokens": 3000,
    }
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                detail = response.text[:300]
                raise ResearchError(f"模型接口返回 {response.status_code}: {detail}")
            body = response.json()
    except ResearchError:
        raise
    except Exception as exc:
        raise ResearchError(f"模型调用失败：{exc}") from exc

    choices = body.get("choices") or []
    message = (choices[0].get("message") if choices else {}) or {}
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning_content") or "").strip()
    if not content:
        raise ResearchError("模型没有返回内容，请稍后重试或检查模型配置")

    note = save_note(code, settings.deepseek_model, content)
    return {
        "symbol": code,
        "name": quote.get("name"),
        "model": settings.deepseek_model,
        "note": note,
        "snapshot": {
            "quote": quote,
            "indicators": indicators.get("latest") or {},
            "fundamentals": fundamentals,
        },
    }