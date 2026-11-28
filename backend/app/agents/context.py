"""Build an analyst-ready context for one symbol from the local DuckDB panel.

Every number handed to an agent comes from a query here; nothing is invented and
nothing is fetched from the network at analysis time. That keeps a run
reproducible: re-running tomorrow against the same panel gives the same context.

The output has two parts:

* a nested ``dict`` of typed values (persisted with the decision log), and
* small ``to_prompt_*`` renderers that flatten one slice into compact Chinese
  text, so each analyst only pays for the tokens it needs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.config import settings
from app.data import store
from app.services.indicators import compute_all
from app.services.symbols import normalize_code

from app.agents import context_events, context_market, memory

BENCHMARKS = (("000300.SH", "沪深300"), ("000001.SH", "上证指数"))


def _f(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:+.{digits}f}%"


def _unit(value: Any, unit: str, digits: int = 2) -> str:
    """Format a number with its unit, without producing ``数据缺失%``."""
    if value is None:
        return "数据缺失"
    return f"{value:,.{digits}f}{unit}"


def _iso(value: Any) -> Optional[str]:
    """Dates come back as DATE from most tables and VARCHAR from a few."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)[:10]


def _ratio(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def _rows(sql: str, params: list[Any]) -> list[tuple]:
    with store.session(read_only=True) as conn:
        return conn.execute(sql, params).fetchall()


def _one(sql: str, params: list[Any]) -> Optional[tuple]:
    rows = _rows(sql, params)
    return rows[0] if rows else None


class ContextError(RuntimeError):
    pass


def build_context(symbol: str, *, bars: int = 250) -> dict[str, Any]:
    code = normalize_code(symbol)
    if not code:
        raise ContextError("股票代码无效")
    if not store.is_ready():
        raise ContextError("本地市场面板尚未建立，请先运行 scripts/sync_market.py")

    profile_row = _one(
        "SELECT symbol, name, industry, board, asset_type, list_date FROM symbols WHERE symbol = ?",
        [code],
    )
    if not profile_row:
        raise ContextError(f"面板里没有 {code} 的标的信息")
    ts_code = _ts_code(code)
    end = store.latest_trade_date()

    profile = {
        "symbol": code,
        "ts_code": ts_code,
        "name": profile_row[1],
        "industry": profile_row[2],
        "board": profile_row[3],
        "asset_type": profile_row[4],
        "list_date": _iso(profile_row[5]),
    }

    price = _build_price(code, bars)
    technical = _build_technical(price.pop("_bars"))
    valuation = _build_valuation(code, price["last_close"])
    fundamental = _build_fundamental(ts_code)
    flow = _build_flow(ts_code, code)
    sentiment = _build_sentiment(ts_code, code)
    market = _build_market(end)

    try:
        memory.sync()          # 把新完成的结论补进检索索引
    except Exception:  # noqa: BLE001 - 记忆失败不该挡住推演
        pass
    as_of_text = _iso(end)
    memory_items = memory.for_symbol(
        code,
        as_of=as_of_text,
        limit=3,
        window_days=settings.memory_window_days,
    )
    if settings.memory_peer_enabled and profile.get("industry"):
        try:
            memory_items += memory.for_industry(
                profile["industry"],
                as_of=as_of_text,
                exclude_symbol=code,
                limit=settings.memory_peer_limit,
            )
        except Exception:  # noqa: BLE001 - 面板/行业缺失时不影响主记忆
            pass

    return {
        "symbol": code,
        "profile": profile,
        "price": price,
        "technical": technical,
        "valuation": valuation,
        "fundamental": fundamental,
        "flow": flow,
        "sentiment": sentiment,
        "market": market,
        "regime": context_market.build_market_regime(),
        "sector": context_market.build_sector(profile["industry"], code),
        "events": context_events.build_events(ts_code),
        "attention": context_events.build_attention(ts_code, code),
        "memory": memory_items,
        "memory_ids": memory.memory_ids(memory_items),
        "as_of": _iso(end),
    }


def _ts_code(code: str) -> str:
    from app.services.symbols import exchange

    return f"{code}.{exchange(code)}"


# ------------------------------------------------------------------ price
def _build_price(code: str, bars: int) -> dict[str, Any]:
    rows = _rows(
        "SELECT trade_date, open, high, low, close, volume, amount, adj_factor "
        "FROM daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
        [code, max(bars, 250)],
    )
    if not rows:
        raise ContextError(f"面板里没有 {code} 的日线数据")
    rows.reverse()

    closes = [row[4] for row in rows if row[4] is not None]
    latest = rows[-1]
    close = closes[-1]

    def ret(days: int) -> Optional[float]:
        if len(closes) <= days:
            return None
        return (closes[-1] / closes[-1 - days] - 1.0) * 100.0

    window = rows[-250:]
    highs = [row[2] for row in window if row[2] is not None]
    lows = [row[3] for row in window if row[3] is not None]
    amounts = [row[6] for row in rows[-20:] if row[6] is not None]

    return {
        "trade_date": _iso(latest[0]),
        "last_close": close,
        "prev_close": rows[-2][4] if len(rows) > 1 else None,
        "pct_chg": (close / rows[-2][4] - 1.0) * 100.0 if len(rows) > 1 and rows[-2][4] else None,
        "return_5d": ret(5),
        "return_20d": ret(20),
        "return_60d": ret(60),
        "high_52w": max(highs) if highs else None,
        "low_52w": min(lows) if lows else None,
        "drawdown_from_high": (close / max(highs) - 1.0) * 100.0 if highs and max(highs) else None,
        "avg_amount_20d": sum(amounts) / len(amounts) if amounts else None,
        "bars": len(rows),
        "_bars": [{"trade_date": r[0], "close": r[4], "high": r[2], "low": r[3]} for r in rows],
    }


def _build_technical(bars: list[dict[str, Any]]) -> dict[str, Any]:
    candles = [
        {
            "trade_date": bar["trade_date"],
            "close": bar["close"],
            "high": bar["high"],
            "low": bar["low"],
        }
        for bar in bars
        if bar["close"] is not None
    ]
    indicators = compute_all(candles)
    latest = indicators.get("latest") or {}
    close = latest.get("close")

    ma20 = latest.get("ma20")
    ma60 = latest.get("ma60")
    trend = "数据不足"
    if close and ma20 and ma60:
        if close > ma20 > ma60:
            trend = "多头排列"
        elif close < ma20 < ma60:
            trend = "空头排列"
        else:
            trend = "震荡"

    hist = latest.get("macd_hist")
    macd_state = "数据不足"
    if hist is not None:
        macd_state = "红柱" if hist > 0 else "绿柱"

    return {
        "ma5": latest.get("ma5"),
        "ma10": latest.get("ma10"),
        "ma20": ma20,
        "ma60": ma60,
        "dif": latest.get("dif"),
        "dea": latest.get("dea"),
        "macd_hist": hist,
        "macd_state": macd_state,
        "rsi14": latest.get("rsi14"),
        "boll_upper": latest.get("boll_upper"),
        "boll_mid": latest.get("boll_mid"),
        "boll_lower": latest.get("boll_lower"),
        "trend": trend,
        "close_vs_ma20_pct": (close / ma20 - 1.0) * 100.0 if close and ma20 else None,
    }


# -------------------------------------------------------------- valuation
def _build_valuation(code: str, close: Optional[float]) -> dict[str, Any]:
    row = _one(
        "SELECT trade_date, pe, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv, "
        "turnover_rate, volume_ratio FROM daily_basic WHERE symbol = ? "
        "ORDER BY trade_date DESC LIMIT 1",
        [code],
    )
    if not row:
        return {"pe_ttm": None, "pb": None, "ps_ttm": None, "dv_ttm": None,
                "total_mv": None, "circ_mv": None, "turnover_rate": None,
                "volume_ratio": None, "pe_ttm_percentile_3y": None, "as_of": None}

    pe_ttm = row[2]
    percentile = None
    if pe_ttm is not None:
        since = row[0] - timedelta(days=365 * 3)
        hit = _one(
            "SELECT COUNT(*) FILTER (WHERE pe_ttm <= ?), COUNT(*) FROM daily_basic "
            "WHERE symbol = ? AND trade_date >= ? AND pe_ttm IS NOT NULL",
            [pe_ttm, code, since],
        )
        if hit and hit[1]:
            percentile = hit[0] / hit[1] * 100.0

    return {
        "as_of": _iso(row[0]),
        "pe_ttm": pe_ttm,
        "pb": row[3],
        "ps_ttm": row[4],
        "dv_ttm": row[5],
        "total_mv": row[6],
        "circ_mv": row[7],
        "turnover_rate": row[8],
        "volume_ratio": row[9],
        "pe_ttm_percentile_3y": percentile,
    }


# ------------------------------------------------------------ fundamental
def _build_fundamental(ts_code: str) -> dict[str, Any]:
    indicator = _one(
        "SELECT end_date, roe, roe_waa, eps, bps, grossprofit_margin, netprofit_margin, "
        "debt_to_assets, or_yoy, netprofit_yoy, q_roe, ocf_to_debt "
        "FROM fina_indicator_vip WHERE ts_code = ? ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    )
    income = _one(
        "SELECT end_date, total_revenue, revenue, n_income_attr_p, operate_profit, rd_exp "
        "FROM income_vip WHERE ts_code = ? AND report_type = '1' "
        "ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    )
    balance = _one(
        "SELECT end_date, total_assets, total_liab, total_hldr_eqy_inc_min_int "
        "FROM balancesheet_vip WHERE ts_code = ? AND report_type = '1' "
        "ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    )
    cash = _one(
        "SELECT end_date, n_cashflow_act FROM cashflow_vip WHERE ts_code = ? "
        "AND report_type = '1' ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    )
    forecast = _one(
        "SELECT ann_date, end_date, type, p_change_min, p_change_max, summary "
        "FROM forecast_vip WHERE ts_code = ? ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    )
    express = _one(
        "SELECT ann_date, end_date, revenue, n_income, yoy_net_profit "
        "FROM express_vip WHERE ts_code = ? ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    )

    return {
        "report_period": _iso(indicator[0]) if indicator else None,
        "roe": indicator[1] if indicator else None,
        "roe_waa": indicator[2] if indicator else None,
        "eps": indicator[3] if indicator else None,
        "bps": indicator[4] if indicator else None,
        "gross_margin": indicator[5] if indicator else None,
        "net_margin": indicator[6] if indicator else None,
        "debt_ratio": indicator[7] if indicator else None,
        "revenue_yoy": indicator[8] if indicator else None,
        "profit_yoy": indicator[9] if indicator else None,
        "quarter_roe": indicator[10] if indicator else None,
        "total_revenue": income[1] if income else None,
        "net_profit": income[3] if income else None,
        "operate_profit": income[4] if income else None,
        "rd_exp": income[5] if income else None,
        "total_assets": balance[1] if balance else None,
        "total_liab": balance[2] if balance else None,
        "equity": balance[3] if balance else None,
        "operating_cashflow": cash[1] if cash else None,
        "forecast": {
            "ann_date": _iso(forecast[0]) if forecast else None,
            "type": forecast[2] if forecast else None,
            "change_min": forecast[3] if forecast else None,
            "change_max": forecast[4] if forecast else None,
            "summary": (forecast[5] or "")[:120] if forecast else None,
        } if forecast else None,
        "express": {
            "ann_date": _iso(express[0]) if express else None,
            "revenue": express[1] if express else None,
            "net_income": express[2] if express else None,
        } if express else None,
    }


# ------------------------------------------------------------------- flow
def _build_flow(ts_code: str, code: str) -> dict[str, Any]:
    flow = _one(
        "SELECT trade_date, net_mf_amount, buy_elg_amount, sell_elg_amount, "
        "buy_lg_amount, sell_lg_amount FROM moneyflow WHERE ts_code = ? "
        "ORDER BY trade_date DESC LIMIT 1",
        [ts_code],
    )
    window = _rows(
        "SELECT trade_date, net_mf_amount FROM moneyflow WHERE ts_code = ? "
        "ORDER BY trade_date DESC LIMIT 20",
        [ts_code],
    )
    net_20d = sum(row[1] for row in window if row[1] is not None) if window else None

    margin = _one(
        "SELECT trade_date, rzye, rqye, rzrqye FROM margin_detail WHERE ts_code = ? "
        "ORDER BY trade_date DESC LIMIT 1",
        [ts_code],
    )
    margin_history = _rows(
        "SELECT trade_date, rzye FROM margin_detail WHERE ts_code = ? "
        "ORDER BY trade_date DESC LIMIT 20",
        [ts_code],
    )
    margin_change = None
    if len(margin_history) >= 2 and margin_history[-1][1]:
        margin_change = (margin_history[0][1] / margin_history[-1][1] - 1.0) * 100.0

    chips = _one(
        "SELECT trade_date, cost_50pct, cost_85pct, cost_95pct, winner_rate "
        "FROM cyq_perf WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1",
        [ts_code],
    )

    return {
        "as_of": _iso(flow[0]) if flow else None,
        "net_mf_amount_1d": flow[1] if flow else None,
        "elg_net": (flow[2] - flow[3]) if flow and flow[2] is not None and flow[3] is not None else None,
        "lg_net": (flow[4] - flow[5]) if flow and flow[4] is not None and flow[5] is not None else None,
        "net_mf_amount_20d": net_20d,
        "margin_balance": margin[1] if margin else None,
        "margin_change_20d_pct": margin_change,
        "chip_cost_50pct": chips[1] if chips else None,
        "chip_cost_85pct": chips[2] if chips else None,
        "chip_winner_rate": chips[4] if chips else None,
    }


# -------------------------------------------------------------- sentiment
def _build_sentiment(ts_code: str, code: str) -> dict[str, Any]:
    since_expr = "date_trunc('day', (SELECT MAX(trade_date) FROM daily)) - INTERVAL 90 DAY"

    limit_row = _one(
        f"SELECT COUNT(*) FILTER (WHERE \"limit\" = 'U'), COUNT(*) FILTER (WHERE \"limit\" = 'D'), "
        f"MAX(trade_date) FROM limit_list_d WHERE ts_code = ? AND trade_date >= {since_expr}",
        [ts_code],
    )
    lhb_count = _one(
        f"SELECT COUNT(*), SUM(net_amount) FROM top_list WHERE ts_code = ? AND trade_date >= {since_expr}",
        [ts_code],
    )
    inst_count = _one(
        f"SELECT COUNT(*), SUM(net_buy) FROM top_inst WHERE ts_code = ? AND trade_date >= {since_expr}",
        [ts_code],
    )
    north = _one(
        f"SELECT COUNT(*), SUM(net_amount), MAX(trade_date) FROM hsgt_top10 "
        f"WHERE ts_code = ? AND trade_date >= {since_expr}",
        [ts_code],
    )
    latest_limit = _one(
        f"SELECT trade_date, \"limit\", up_stat, limit_times FROM limit_list_d "
        f"WHERE ts_code = ? AND trade_date >= {since_expr} ORDER BY trade_date DESC LIMIT 1",
        [ts_code],
    )

    return {
        "limit_up_90d": limit_row[0] if limit_row else None,
        "limit_down_90d": limit_row[1] if limit_row else None,
        "lhb_count_90d": lhb_count[0] if lhb_count else None,
        "lhb_net_amount_90d": lhb_count[1] if lhb_count else None,
        "inst_seat_count_90d": inst_count[0] if inst_count else None,
        "inst_net_buy_90d": inst_count[1] if inst_count else None,
        "northbound_days_90d": north[0] if north else None,
        "northbound_net_90d": north[1] if north else None,
        "latest_limit": {
            "trade_date": _iso(latest_limit[0]) if latest_limit else None,
            "direction": latest_limit[1] if latest_limit else None,
            "up_stat": latest_limit[2] if latest_limit else None,
            "limit_times": latest_limit[3] if latest_limit else None,
        } if latest_limit else None,
    }


# ----------------------------------------------------------------- market
def _build_market(end: Optional[date]) -> dict[str, Any]:
    output: dict[str, Any] = {"benchmarks": [], "as_of": _iso(end)}
    for ts_code, label in BENCHMARKS:
        rows = _rows(
            "SELECT trade_date, close FROM index_daily WHERE ts_code = ? "
            "ORDER BY trade_date DESC LIMIT 21",
            [ts_code],
        )
        if len(rows) < 2:
            continue
        rows.reverse()
        closes = [row[1] for row in rows if row[1] is not None]
        output["benchmarks"].append(
            {
                "name": label,
                "ts_code": ts_code,
                "close": closes[-1],
                "return_20d": (closes[-1] / closes[0] - 1.0) * 100.0 if closes[0] else None,
            }
        )
    return output


# ----------------------------------------------------------------- prompts
def render_profile(ctx: dict[str, Any]) -> str:
    profile = ctx["profile"]
    price = ctx["price"]
    return (
        f"标的：{profile['name']}（{profile['symbol']}），行业：{profile['industry'] or '未知'}，"
        f"板块：{profile['board'] or '未知'}，上市：{profile['list_date'] or '未知'}\n"
        f"最新交易日：{ctx['as_of']}，收盘 {_f(price['last_close'])}，"
        f"当日 {_pct(price['pct_chg'])}，5/20/60 日 "
        f"{_pct(price['return_5d'])} / {_pct(price['return_20d'])} / {_pct(price['return_60d'])}"
    )


def render_technical(ctx: dict[str, Any]) -> str:
    price, tech = ctx["price"], ctx["technical"]
    return (
        f"【价格结构】收盘 {_f(price['last_close'])}，距 52 周高点 {_pct(price['drawdown_from_high'])}，"
        f"52 周区间 {_f(price['low_52w'])} ~ {_f(price['high_52w'])}，20 日均额 {_f(price['avg_amount_20d'], 0)} 元\n"
        f"【均线】MA5 {_f(tech['ma5'])} / MA10 {_f(tech['ma10'])} / MA20 {_f(tech['ma20'])} / "
        f"MA60 {_f(tech['ma60'])}，排列：{tech['trend']}，偏离 MA20 {_pct(tech['close_vs_ma20_pct'])}\n"
        f"【动量】MACD DIF {_f(tech['dif'], 4)} / DEA {_f(tech['dea'], 4)} / 柱 {_f(tech['macd_hist'], 4)}"
        f"（{tech['macd_state']}），RSI14 {_f(tech['rsi14'], 1)}\n"
        f"【通道】BOLL 上 {_f(tech['boll_upper'])} / 中 {_f(tech['boll_mid'])} / 下 {_f(tech['boll_lower'])}"
    )


def render_fundamental(ctx: dict[str, Any]) -> str:
    fund, value = ctx["fundamental"], ctx["valuation"]
    forecast = fund.get("forecast")
    forecast_text = (
        f"；业绩预告 {forecast['type']}（{_pct(forecast['change_min'])} ~ {_pct(forecast['change_max'])}）"
        if forecast and forecast.get("type")
        else ""
    )
    return (
        f"【报告期】{fund['report_period'] or '缺失'}\n"
        f"【盈利】ROE {_unit(fund['roe'], '%')}，摊薄 ROE {_unit(fund['roe_waa'], '%')}，"
        f"EPS {_f(fund['eps'])}，毛利率 {_unit(fund['gross_margin'], '%')}，"
        f"净利率 {_unit(fund['net_margin'], '%')}\n"
        f"【成长】营收同比 {_pct(fund['revenue_yoy'])}，净利同比 {_pct(fund['profit_yoy'])}{forecast_text}\n"
        f"【规模】营业收入 {_f(fund['total_revenue'], 0)}，归母净利 {_f(fund['net_profit'], 0)}，"
        f"总资产 {_f(fund['total_assets'], 0)}，资产负债率 {_unit(fund['debt_ratio'], '%')}\n"
        f"【现金流】经营现金流 {_f(fund['operating_cashflow'], 0)}，BPS {_f(fund['bps'])}\n"
        f"【估值】PE(TTM) {_f(value['pe_ttm'])}（近三年分位 {_unit(value['pe_ttm_percentile_3y'], '%', 0)}），"
        f"PB {_f(value['pb'])}，PS(TTM) {_f(value['ps_ttm'])}，股息率 {_unit(value['dv_ttm'], '%')}，"
        f"总市值 {_f(value['total_mv'], 0)}，换手率 {_unit(value['turnover_rate'], '%')}"
    )


def render_flow(ctx: dict[str, Any]) -> str:
    flow = ctx["flow"]
    return (
        f"【主力资金】当日净流入 {_unit(flow['net_mf_amount_1d'], ' 万元', 0)}，20 日累计 "
        f"{_unit(flow['net_mf_amount_20d'], ' 万元', 0)}；超大单净额 {_unit(flow['elg_net'], ' 万元', 0)}，"
        f"大单净额 {_unit(flow['lg_net'], ' 万元', 0)}\n"
        f"【两融】融资余额 {_unit(flow['margin_balance'], ' 元', 0)}，近 20 日变化 "
        f"{_pct(flow['margin_change_20d_pct'])}\n"
        f"【筹码】获利比例 {_unit(flow['chip_winner_rate'], '%')}，50% 成本 {_f(flow['chip_cost_50pct'])}，"
        f"85% 成本 {_f(flow['chip_cost_85pct'])}"
    )


def _count_text(count: Any) -> str:
    return "无记录" if not count else f"{int(count)} 次"


def render_sentiment(ctx: dict[str, Any]) -> str:
    sent = ctx["sentiment"]
    latest = sent.get("latest_limit") or {}
    limit_text = (
        f"最近一次上榜 {latest.get('trade_date')}（{latest.get('direction')}）"
        if latest.get("trade_date")
        else "近 90 日未上榜"
    )
    lhb_count = sent["lhb_count_90d"] or 0
    inst_count = sent["inst_seat_count_90d"] or 0
    north_count = sent["northbound_days_90d"] or 0
    return (
        f"【涨跌停】近 90 日涨停 {_count_text(sent['limit_up_90d'])}、"
        f"跌停 {_count_text(sent['limit_down_90d'])}，{limit_text}\n"
        f"【龙虎榜】近 90 日 {_count_text(lhb_count)}，"
        f"净买入 {_unit(sent['lhb_net_amount_90d'], ' 元', 0) if lhb_count else '无记录'}；"
        f"机构席位 {_count_text(inst_count)}，"
        f"净买入 {_unit(sent['inst_net_buy_90d'], ' 元', 0) if inst_count else '无记录'}\n"
        f"【北向】近 90 日上榜 {_count_text(north_count)}，"
        f"净买入 {_unit(sent['northbound_net_90d'], ' 元', 0) if north_count else '无记录'}"
    )


def render_market(ctx: dict[str, Any], stock_return_20d: Optional[float]) -> str:
    lines = []
    for item in ctx["market"]["benchmarks"]:
        lines.append(f"{item['name']}近 20 日 {_pct(item['return_20d'])}（收 {_f(item['close'])}）")
    if stock_return_20d is not None and ctx["market"]["benchmarks"]:
        benchmark = ctx["market"]["benchmarks"][0]["return_20d"]
        if benchmark is not None:
            lines.append(f"相对沪深300 超额 {_pct(stock_return_20d - benchmark)}")
    return "【市场环境】" + "；".join(lines) if lines else "【市场环境】指数数据缺失"


def render_regime(ctx: dict[str, Any]) -> str:
    return context_market.render_market_regime(ctx["regime"])


def render_sector(ctx: dict[str, Any]) -> str:
    return context_market.render_sector(ctx["sector"])


def render_events(ctx: dict[str, Any]) -> str:
    return context_events.render_events(ctx["events"])


def render_attention(ctx: dict[str, Any]) -> str:
    return context_events.render_attention(ctx["attention"])


def render_attention_full(ctx: dict[str, Any]) -> str:
    """Attention = crowd heat + limit/LHB/北向, i.e. the whole sentiment slice."""
    return f"{render_sentiment(ctx)}\n{render_attention(ctx)}"
