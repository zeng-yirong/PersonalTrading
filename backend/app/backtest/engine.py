"""Portfolio simulation for a screener-driven strategy.

The rules are the paper account's rules, applied to history:

* a signal is computed on the close of the rebalance day, and the order is filled
  at the **next open** — using the signal day's close to trade would be free
  look-ahead;
* a limit-up open cannot be bought and a limit-down open cannot be sold; a blocked
  sell is retried on the next day instead of being assumed away;
* positions are tracked by **adjusted** open (``open * adj_factor``), so a split or
  a cash dividend is a return rather than a fake gap in the equity curve;
* the same fees as ``app.fees`` are charged, and lot size is 100 shares.

Positions are equal-weighted with the cash available after the previous period is
sold. Nothing is leveraged and uninvested cash earns nothing.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from app.backtest.data import close_adj
from app.fees import LOT, fees, shares_for

TRADING_DAYS = 252
LIMIT_TOLERANCE = 0.999   # at/above 99.9% of the up limit: no fill
DOWN_TOLERANCE = 1.001    # at/below 100.1% of the down limit: no fill


@dataclass
class Position:
    symbol: str
    ts_code: str
    name: str
    shares: float
    fill_price: float
    entry_adj: float      # fill price x adj_factor at entry
    cost: float           # shares x fill price + buy fee
    basis: float          # shares x fill price (used to value the position)
    entry_date: date
    period: int
    sell_due: bool = False
    last_adj: Optional[float] = None


def schedule(days: list[date], rebalance: int, min_period: int = 3) -> list[date]:
    """Signal dates: every ``rebalance`` trading days, minus an uninvestable tail.

    A final period shorter than half a rebalance is dropped: three days of
    mark-to-market at the end of a 20-day strategy is noise, not a period.
    """
    floor_days = max(min_period, rebalance // 2)
    signals: list[date] = []
    for index in range(0, len(days), rebalance):
        if len(days) - 1 - index < floor_days:
            break
        signals.append(days[index])
    return signals


def simulate(
    spec,
    days: list[date],
    picks_by_signal: dict[date, list[dict[str, Any]]],
    bars: dict[str, dict[date, dict[str, Any]]],
    limits: dict[tuple[str, date], tuple[Optional[float], Optional[float]]],
    bench: dict[date, float],
    warnings: list[str],
) -> dict[str, Any]:
    if not days:
        raise ValueError("回测区间内没有交易日")

    index_of = {day: index for index, day in enumerate(days)}
    plan: dict[date, tuple[date, list[dict[str, Any]]]] = {}
    for signal in sorted(picks_by_signal):
        index = index_of.get(signal)
        if index is None or index + 1 >= len(days):
            continue
        plan[days[index + 1]] = (signal, picks_by_signal[signal])
    if not plan:
        raise ValueError("区间内没有可执行的调仓日（信号日之后缺少交易日）")

    slip = spec.slippage_bps / 10_000.0
    cash = float(spec.initial_cash)
    positions: list[Position] = []
    periods: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    skipped_buys = 0
    blocked_sells = 0

    def period_of(index: int) -> dict[str, Any]:
        return periods[index]

    for day in days:
        # 1. every rebalance day liquidates whatever is held
        if day in plan:
            for position in positions:
                position.sell_due = True

        # 2. sells first, so the cash is available for the buys below
        for position in list(positions):
            if not position.sell_due:
                continue
            bar = bars.get(position.symbol, {}).get(day)
            if bar is None or bar.get("open") is None:
                continue  # suspended: retry next day
            limit = limits.get((position.ts_code, day))
            down = limit[1] if limit else None
            if down and bar["open"] <= down * DOWN_TOLERANCE:
                blocked_sells += 1
                continue  # limit-down: nobody is buying
            exit_adj = float(bar["open"]) * float(bar.get("adj") or 1.0)
            proceeds = position.basis * (exit_adj / position.entry_adj)
            fee = fees("sell", proceeds)
            cash += proceeds - fee
            positions.remove(position)
            record = period_of(position.period)
            record["holdings"].append(
                {
                    "symbol": position.symbol,
                    "name": position.name,
                    "weight": _weight(position.cost, record["equity"]),
                    "entry_price": round(position.fill_price, 3),
                    "exit_price": round(float(bar["open"]), 3),
                    "return": round((proceeds - fee) / position.cost - 1, 6),
                    "exit_date": day.isoformat(),
                }
            )
            record["fees"] += fee
            record["invested"] += position.cost
            if record["exit_date"] is None or day > record["exit_date"]:
                record["exit_date"] = day
            trades.append(
                {
                    "date": day.isoformat(),
                    "symbol": position.symbol,
                    "name": position.name,
                    "side": "sell",
                    "shares": position.shares,
                    "price": round(float(bar["open"]), 3),
                    "fee": fee,
                    "period": position.period + 1,
                }
            )

        # 3. buys at the open of the day after the signal
        if day in plan:
            signal, picks = plan[day]
            tradable = []
            for pick in picks:
                bar = bars.get(pick["symbol"], {}).get(day)
                if bar is None or bar.get("open") is None:
                    continue  # suspended
                limit = limits.get((pick["ts_code"], day))
                up = limit[0] if limit else None
                if up and bar["open"] >= up * LIMIT_TOLERANCE:
                    skipped_buys += 1
                    continue  # limit-up open: cannot get filled
                tradable.append((pick, bar))

            equity = cash
            per_name = cash / len(tradable) if tradable else 0.0
            record = {
                "index": len(periods) + 1,
                "signal_date": signal.isoformat(),
                "entry_date": day.isoformat(),
                "exit_date": None,
                "picks": len(picks),
                "equity": equity,
                "invested": 0.0,
                "fees": 0.0,
                "holdings": [],
                "note": "",
            }
            for pick, bar in tradable:
                fill = float(bar["open"]) * (1 + slip)
                # The fee is charged on top of the notional, so a budget equal to
                # the whole cash pile cannot buy a single lot. Step down until the
                # order (notional + fee) fits.
                shares = shares_for(per_name, fill)
                while shares > 0 and shares * fill + fees("buy", shares * fill) > cash + 1e-6:
                    shares -= LOT
                if shares <= 0:
                    continue
                gross = shares * fill
                fee = fees("buy", gross)
                cash -= gross + fee
                position = Position(
                    symbol=pick["symbol"],
                    ts_code=pick["ts_code"],
                    name=pick.get("name") or pick["symbol"],
                    shares=shares,
                    fill_price=fill,
                    entry_adj=fill * float(bar.get("adj") or 1.0),
                    cost=gross + fee,
                    basis=gross,
                    entry_date=day,
                    period=len(periods),
                )
                positions.append(position)
                record["invested"] += gross + fee
                record["fees"] += fee
                trades.append(
                    {
                        "date": day.isoformat(),
                        "symbol": position.symbol,
                        "name": position.name,
                        "side": "buy",
                        "shares": shares,
                        "price": round(fill, 3),
                        "fee": fee,
                        "period": record["index"],
                    }
                )
            if not tradable:
                record["note"] = "没有可成交的标的（停牌或涨停），保持空仓"
            elif len(tradable) < len(picks):
                record["note"] = f"{len(picks) - len(tradable)} 只因停牌/涨停未能买入"
            periods.append(record)

        # 4. mark to market at the close
        value = 0.0
        invested = 0.0
        for position in positions:
            bar = bars.get(position.symbol, {}).get(day)
            adj = close_adj(bar)
            if adj is not None:
                position.last_adj = adj
            adj = position.last_adj
            if adj is None:
                adj = position.entry_adj
            value += position.basis * (adj / position.entry_adj)
            invested += position.basis * (adj / position.entry_adj)
        nav_rows.append(
            {
                "trade_date": day,
                "cash": round(cash, 2),
                "market_value": round(value, 2),
                "equity": round(cash + value, 2),
                "nav": round((cash + value) / spec.initial_cash, 6),
                "invested_ratio": round(invested / (cash + value), 6) if cash + value else 0.0,
            }
        )

    # Anything still open is valued at the last close, with no sell fee charged.
    last_day = days[-1]
    for position in positions:
        bar = bars.get(position.symbol, {}).get(last_day)
        adj = close_adj(bar) or position.last_adj or position.entry_adj
        proceeds = position.basis * (adj / position.entry_adj)
        record = period_of(position.period)
        record["holdings"].append(
            {
                "symbol": position.symbol,
                "name": position.name,
                "weight": _weight(position.cost, record["equity"]),
                "entry_price": round(position.fill_price, 3),
                "exit_price": round(proceeds / position.shares, 3) if position.shares else None,
                "return": round(proceeds / position.cost - 1, 6),
                "exit_date": last_day.isoformat(),
            }
        )
        record["invested"] += position.cost
        if "期末未平仓" not in record["note"]:
            record["note"] = (record["note"] + "；" if record["note"] else "") + (
                "期末未平仓，按收盘估值"
            )
        trades.append(
            {
                "date": last_day.isoformat(),
                "symbol": position.symbol,
                "name": position.name,
                "side": "mark",
                "shares": position.shares,
                "price": round(proceeds / position.shares, 3) if position.shares else None,
                "fee": 0.0,
                "period": position.period + 1,
            }
        )

    for record in periods:
        exit_day = record["exit_date"] or last_day
        record["exit_date"] = exit_day.isoformat()
        record["cash_weight"] = round(
            max(0.0, 1 - record["invested"] / record["equity"]), 6
        ) if record["equity"] else 1.0
        record["return"] = round(
            sum(item["weight"] * item["return"] for item in record["holdings"]), 6
        )
        record["holdings"].sort(key=lambda item: item["weight"], reverse=True)
        record["benchmark_return"] = _benchmark_return(bench, record["entry_date"], exit_day)
        record["excess"] = round(record["return"] - record["benchmark_return"], 6)

    metrics = _metrics(
        nav_rows, periods, bench, trades, spec, days, skipped_buys, blocked_sells
    )
    if skipped_buys:
        warnings.append(f"{skipped_buys} 次买入因开盘涨停被跳过（无法成交）")
    if blocked_sells:
        warnings.append(f"{blocked_sells} 次卖出因跌停未能成交，顺延到下一个交易日")

    return {
        "nav": [
            {
                "trade_date": row["trade_date"].isoformat(),
                "nav": row["nav"],
                "benchmark": _benchmark_nav(bench, nav_rows, row["trade_date"]),
                "invested_ratio": row["invested_ratio"],
            }
            for row in nav_rows
        ],
        "nav_detail": [
            {
                "trade_date": row["trade_date"].isoformat(),
                "cash": row["cash"],
                "market_value": row["market_value"],
                "equity": row["equity"],
            }
            for row in nav_rows
        ],
        "periods": _serialise_periods(periods),
        "trades": trades,
        "metrics": metrics,
    }


# --------------------------------------------------------------------- helpers
def _weight(cost: float, equity: float) -> float:
    return round(cost / equity, 6) if equity else 0.0


def _benchmark_return(bench: dict[date, float], entry: str | date, exit_day: str | date) -> float:
    start = bench.get(_as_date(entry))
    end = bench.get(_as_date(exit_day))
    if not start or not end:
        return 0.0
    return round(end / start - 1, 6)


def _as_date(value: str | date) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _benchmark_nav(
    bench: dict[date, float], nav_rows: list[dict[str, Any]], day: date
) -> Optional[float]:
    if not bench or not nav_rows:
        return None
    base = bench.get(nav_rows[0]["trade_date"])
    value = bench.get(day)
    if not base or not value:
        return None
    return round(value / base, 6)


def _serialise_periods(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for record in periods:
        output.append(
            {
                "index": record["index"],
                "signal_date": record["signal_date"],
                "entry_date": record["entry_date"],
                "exit_date": record["exit_date"],
                "picks": record["picks"],
                "positions": len(record["holdings"]),
                "return_pct": round(record["return"] * 100, 4),
                "benchmark_pct": round(record["benchmark_return"] * 100, 4),
                "excess_pct": round(record["excess"] * 100, 4),
                "cash_weight": record["cash_weight"],
                "fees": round(record["fees"], 2),
                "note": record["note"],
                "holdings": [
                    {
                        "symbol": item["symbol"],
                        "name": item["name"],
                        "weight": item["weight"],
                        "entry_price": item["entry_price"],
                        "exit_price": item["exit_price"],
                        "return_pct": round(item["return"] * 100, 4),
                        "exit_date": item["exit_date"],
                    }
                    for item in record["holdings"]
                ],
            }
        )
    return output


def _metrics(
    nav_rows: list[dict[str, Any]],
    periods: list[dict[str, Any]],
    bench: dict[date, float],
    trades: list[dict[str, Any]],
    spec,
    days: list[date],
    skipped_buys: int = 0,
    blocked_sells: int = 0,
) -> dict[str, Any]:
    navs = [row["nav"] for row in nav_rows]
    total = navs[-1] / navs[0] - 1 if navs and navs[0] else 0.0
    daily = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs)) if navs[i - 1]]
    volatility = statistics.pstdev(daily) * math.sqrt(TRADING_DAYS) if len(daily) > 2 else 0.0
    sharpe = (
        statistics.fmean(daily) / statistics.pstdev(daily) * math.sqrt(TRADING_DAYS)
        if len(daily) > 2 and statistics.pstdev(daily) > 0
        else 0.0
    )
    peak, max_drawdown = 0.0, 0.0
    for value in navs:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1 if peak else 0.0)
    span = max(1, len(navs) - 1)
    cagr = (1 + total) ** (TRADING_DAYS / span) - 1 if total > -1 else -1.0

    bench_total = 0.0
    bench_cagr = 0.0
    if bench:
        first = bench.get(nav_rows[0]["trade_date"])
        last = bench.get(nav_rows[-1]["trade_date"])
        if first and last:
            bench_total = last / first - 1
            bench_cagr = (1 + bench_total) ** (TRADING_DAYS / span) - 1 if bench_total > -1 else -1.0

    returns = [item["return"] for item in periods]
    excess = [item["excess"] for item in periods]
    hits = sum(1 for value in excess if value > 0)
    fees_total = sum(item["fee"] for item in trades)
    exposure = statistics.fmean(row["invested_ratio"] for row in nav_rows) if nav_rows else 0.0

    best = max(periods, key=lambda item: item["excess"]) if periods else None
    worst = min(periods, key=lambda item: item["excess"]) if periods else None
    empty = sum(1 for item in periods if not item["holdings"])

    # 短周期策略要看的四个数：持有天数、年化换手、费用拖累、盈亏比。
    day_index = {day.isoformat(): index for index, day in enumerate(days)}
    holding: list[int] = []
    for item in periods:
        entry, exit_ = item.get("entry_date"), item.get("exit_date")
        if entry in day_index and exit_ in day_index:
            holding.append(max(1, day_index[exit_] - day_index[entry]))
    avg_holding = statistics.fmean(holding) if holding else 0.0

    notional = sum(
        float(item["price"]) * float(item["shares"])
        for item in trades
        if item["side"] != "mark"
    )
    # 分母必须是"账户净资产"（元），不是 nav 比值（≈1.0）。
    avg_equity = (
        statistics.fmean(float(row.get("equity") or 0) for row in nav_rows)
        if nav_rows
        else float(spec.initial_cash)
    )
    years = max(1 / TRADING_DAYS, span / TRADING_DAYS)
    turnover = (notional / 2 / avg_equity / years) if avg_equity else 0.0
    fee_drag = (fees_total / avg_equity / years) if avg_equity else 0.0

    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    win_loss = (
        statistics.fmean(wins) / abs(statistics.fmean(losses))
        if wins and losses and statistics.fmean(losses) != 0
        else 0.0
    )

    return {
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "trading_days": len(navs),
        "total_return_pct": round(total * 100, 4),
        "cagr_pct": round(cagr * 100, 4),
        "benchmark_return_pct": round(bench_total * 100, 4),
        "benchmark_cagr_pct": round(bench_cagr * 100, 4),
        "excess_cagr_pct": round((cagr - bench_cagr) * 100, 4),
        "volatility_pct": round(volatility * 100, 4),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "hit_rate_pct": round(hits / len(periods) * 100, 2) if periods else 0.0,
        "avg_period_pct": round(statistics.fmean(returns) * 100, 4) if returns else 0.0,
        "avg_excess_pct": round(statistics.fmean(excess) * 100, 4) if excess else 0.0,
        "periods": len(periods),
        "empty_periods": empty,
        "trades": sum(1 for item in trades if item["side"] != "mark"),
        "fees": round(fees_total, 2),
        "exposure_pct": round(exposure * 100, 4),
        "avg_positions": round(
            statistics.fmean(len(item["holdings"]) for item in periods), 2
        ) if periods else 0.0,
        "best_period": _period_brief(best),
        "worst_period": _period_brief(worst),
        "avg_holding_days": round(avg_holding, 2),
        "turnover_pct": round(turnover * 100, 2),
        "fee_drag_pct": round(fee_drag * 100, 2),
        "win_loss_ratio": round(win_loss, 3),
        "skipped_buys": int(skipped_buys),
        "blocked_sells": int(blocked_sells),
    }


def _period_brief(record: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if record is None:
        return None
    return {
        "index": record["index"],
        "entry_date": record["entry_date"],
        "return_pct": round(record["return"] * 100, 4),
        "excess_pct": round(record["excess"] * 100, 4),
    }
