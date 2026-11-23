"""Paper trading: a virtual account settled against the local panel.

Rules that matter for a review to mean anything:

* **Fills happen at the panel's close**, never at a realtime quote — otherwise a
  backtest of the assistant's own advice would compare against prices nobody
  could have traded.
* **Fees are charged** (commission with a 5 yuan floor, stamp duty on sells,
  transfer fee), so a strategy that churns cannot look profitable by accident.
* **NAV is reconstructed from trades**, not stored incrementally, so a re-run of
  the same trade list always yields the same curve.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from app.data import store
from app.db import connection, now_iso
from app.fees import (
    COMMISSION_MIN,
    COMMISSION_RATE,
    LOT,
    STAMP_RATE,
    TRANSFER_RATE,
    fees as _fees,
    shares_for as _shares_from_amount,
)
from app.services.benchmark import BENCHMARK
from app.services import benchmark as benchmark_service

DEFAULT_CASH = 1_000_000.0


class PaperError(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers
def _latest_close(symbol: str) -> tuple[date, float]:
    bars = store.get_daily(symbol, limit=1)
    if not bars:
        raise PaperError(f"面板里没有 {symbol} 的行情，无法成交")
    bar = bars[-1]
    return bar["trade_date"], float(bar["close"])


def _close_on(symbol: str, day: date) -> Optional[float]:
    bars = store.get_daily(symbol, start=day, end=day, limit=1)
    return float(bars[-1]["close"]) if bars else None


# ------------------------------------------------------------------ account
def ensure_account(initial_cash: Optional[float] = None) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
        if row is None:
            cash = float(initial_cash or DEFAULT_CASH)
            conn.execute(
                "INSERT INTO paper_account(id, name, initial_cash, cash, created_at) "
                "VALUES(1, ?, ?, ?, ?)",
                ("模拟账户", cash, cash, now_iso()),
            )
            row = conn.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
    return _account_row(row)


def _account_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "initial_cash": row["initial_cash"],
        "cash": row["cash"],
        "created_at": row["created_at"],
    }


def reset(initial_cash: float = DEFAULT_CASH) -> dict[str, Any]:
    if initial_cash <= 0:
        raise PaperError("初始资金必须大于 0")
    with connection() as conn:
        conn.execute("DELETE FROM paper_trades")
        conn.execute("DELETE FROM paper_positions")
        conn.execute("DELETE FROM paper_nav")
        conn.execute("DELETE FROM paper_account")
        conn.execute(
            "INSERT INTO paper_account(id, name, initial_cash, cash, created_at) "
            "VALUES(1, ?, ?, ?, ?)",
            ("模拟账户", initial_cash, initial_cash, now_iso()),
        )
    return ensure_account()


# ------------------------------------------------------------------ trading
def trade(
    symbol: str,
    side: str,
    shares: Optional[float] = None,
    amount: Optional[float] = None,
    run_id: Optional[int] = None,
    note: str = "",
) -> dict[str, Any]:
    side = side.lower()
    if side not in {"buy", "sell"}:
        raise PaperError("side 只能是 buy 或 sell")

    account = ensure_account()
    day, price = _latest_close(symbol)
    if shares is None:
        if amount is None:
            raise PaperError("需要指定 shares 或 amount")
        shares = _shares_from_amount(float(amount), price)
    shares = float(shares)
    if shares <= 0:
        raise PaperError(f"按当前价 {price:.2f} 元，这个金额买不到一手（100 股）")

    gross = round(shares * price, 2)
    fee = _fees(side, gross)
    name = (store.get_symbol(symbol) or {}).get("name") or symbol

    with connection() as conn:
        if side == "buy":
            if gross + fee > account["cash"] + 1e-6:
                raise PaperError(
                    f"现金不足：需要 {gross + fee:,.2f} 元，可用 {account['cash']:,.2f} 元"
                )
            position = conn.execute(
                "SELECT * FROM paper_positions WHERE symbol = ? AND status = 'open'",
                (symbol,),
            ).fetchone()
            if position:
                total_shares = position["shares"] + shares
                avg_cost = (position["avg_cost"] * position["shares"] + gross + fee) / total_shares
                conn.execute(
                    "UPDATE paper_positions SET shares = ?, avg_cost = ? WHERE id = ?",
                    (total_shares, round(avg_cost, 4), position["id"]),
                )
                position_id = position["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO paper_positions(symbol, name, shares, avg_cost, opened_at, "
                    "status, run_id, note) VALUES(?, ?, ?, ?, ?, 'open', ?, ?)",
                    (symbol, name, shares, round((gross + fee) / shares, 4), day.isoformat(),
                     run_id, note),
                )
                position_id = int(cursor.lastrowid)
            conn.execute(
                "UPDATE paper_account SET cash = cash - ? WHERE id = 1", (gross + fee,)
            )
        else:
            position = conn.execute(
                "SELECT * FROM paper_positions WHERE symbol = ? AND status = 'open'",
                (symbol,),
            ).fetchone()
            if not position:
                raise PaperError(f"没有 {symbol} 的持仓")
            if shares > position["shares"] + 1e-6:
                raise PaperError(f"持仓不足：持有 {position['shares']:,.0f} 股，卖出 {shares:,.0f} 股")
            position_id = position["id"]
            remaining = position["shares"] - shares
            if remaining <= 1e-6:
                conn.execute(
                    "UPDATE paper_positions SET shares = 0, status = 'closed', closed_at = ? "
                    "WHERE id = ?",
                    (day.isoformat(), position_id),
                )
                conn.execute("UPDATE paper_positions SET closed_at = ? WHERE id = ?",
                             (day.isoformat(), position_id))
            else:
                conn.execute(
                    "UPDATE paper_positions SET shares = ? WHERE id = ?",
                    (remaining, position_id),
                )
            conn.execute("UPDATE paper_account SET cash = cash + ? WHERE id = 1", (gross - fee,))

        conn.execute(
            "INSERT INTO paper_trades(position_id, symbol, side, shares, price, fee, amount, "
            "trade_date, run_id, note, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (position_id, symbol, side, shares, price, fee, gross, day.isoformat(),
             run_id, note, now_iso()),
        )

    rebuild_nav()
    return {
        "symbol": symbol,
        "name": name,
        "side": side,
        "shares": shares,
        "price": price,
        "gross": gross,
        "fee": fee,
        "trade_date": day.isoformat(),
    }


def trade_from_run(run_id: int, fraction: float = 1.0) -> dict[str, Any]:
    """Open a position sized by the risk manager's final position percentage."""
    from app.agents import store as agent_store

    run = agent_store.get_run(run_id)
    if not run:
        raise PaperError(f"推演 {run_id} 不存在")
    verdict = run.get("verdict") or {}
    decision = verdict.get("decision")
    pct = verdict.get("final_position_pct")
    if decision in {"否决", "无法评估"}:
        raise PaperError(f"推演 {run_id} 的裁定是「{decision}」，不能建仓")
    if decision not in {"批准", "调整后批准"}:
        raise PaperError(f"推演 {run_id} 的裁定是「{decision or '无'}」，不支持建仓")
    if pct in (None, 0):
        raise PaperError(
            f"推演 {run_id} 的最终仓位是 {pct or 0}%（观望），按裁定不应建仓"
        )

    account = ensure_account()
    equity = account["cash"]  # no leverage; holdings valued separately below
    with connection() as conn:
        held = conn.execute(
            "SELECT COALESCE(SUM(shares), 0) FROM paper_positions WHERE status = 'open'"
        ).fetchone()[0]
    equity = account["cash"] + _positions_value()
    budget = equity * float(pct) / 100.0 * fraction
    if budget <= 0:
        raise PaperError("按这个仓位算出来的可买金额为 0")

    day, price = _latest_close(run["symbol"])
    shares = _shares_from_amount(budget, price)
    if shares <= 0:
        raise PaperError(
            f"仓位 {pct}% 约 {budget:,.0f} 元，按 {price:.2f} 元买不到一手（100 股）"
        )
    note = f"来自推演 #{run_id}：{decision}，仓位 {pct}%"
    return trade(run["symbol"], "buy", shares=shares, run_id=run_id, note=note)


# ------------------------------------------------------------------ valuation
def _positions_value() -> float:
    with connection() as conn:
        rows = conn.execute(
            "SELECT symbol, shares FROM paper_positions WHERE status = 'open'"
        ).fetchall()
    total = 0.0
    for row in rows:
        bars = store.get_daily(row["symbol"], limit=1)
        if bars:
            total += float(row["shares"]) * float(bars[-1]["close"])
    return total


def positions() -> list[dict[str, Any]]:
    ensure_account()
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'open' ORDER BY opened_at"
        ).fetchall()

    output = []
    for row in rows:
        bars = store.get_daily(row["symbol"], limit=1)
        price = float(bars[-1]["close"]) if bars else None
        value = price * row["shares"] if price else None
        cost = row["avg_cost"] * row["shares"]
        output.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "name": row["name"],
                "shares": row["shares"],
                "avg_cost": round(row["avg_cost"], 4),
                "price": price,
                "market_value": round(value, 2) if value else None,
                "cost": round(cost, 2),
                "pnl": round(value - cost, 2) if value else None,
                "pnl_pct": round((value / cost - 1) * 100, 2) if value and cost else None,
                "opened_at": row["opened_at"],
                "run_id": row["run_id"],
                "note": row["note"],
            }
        )
    return output


def summary() -> dict[str, Any]:
    account = ensure_account()
    held = positions()
    market_value = sum(item["market_value"] or 0 for item in held)
    total = account["cash"] + market_value
    return {
        "account": account,
        "cash": round(account["cash"], 2),
        "market_value": round(market_value, 2),
        "total": round(total, 2),
        "pnl": round(total - account["initial_cash"], 2),
        "pnl_pct": round((total / account["initial_cash"] - 1) * 100, 2),
        "positions": held,
        "position_count": len(held),
    }


# ---------------------------------------------------------------------- NAV
def rebuild_nav() -> int:
    """Replay trades against daily closes to produce the equity curve.

    Suspended symbols carry their last known close instead of dropping to zero —
    a halt is not a -100% move.
    """
    account = ensure_account()
    with connection() as conn:
        trades_rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY trade_date, id"
        ).fetchall()
    if not trades_rows:
        with connection() as conn:
            conn.execute("DELETE FROM paper_nav")
        return 0

    first_day = date.fromisoformat(trades_rows[0]["trade_date"])
    last_day = store.latest_trade_date() or first_day
    days = store.get_trade_days(first_day, last_day)
    if not days:
        return 0

    symbols = sorted({row["symbol"] for row in trades_rows})
    closes: dict[str, dict[date, float]] = {}
    for symbol in symbols:
        closes[symbol] = {
            bar["trade_date"]: float(bar["close"])
            for bar in store.get_daily(symbol, start=first_day, limit=5000)
        }
    benchmark_closes = _benchmark_series(first_day, last_day)

    by_day: dict[date, list] = {}
    for row in trades_rows:
        by_day.setdefault(date.fromisoformat(row["trade_date"]), []).append(row)

    cash = account["initial_cash"]
    held: dict[str, float] = {}
    last_price: dict[str, float] = {}
    benchmark_base = None
    payload = []

    for day in days:
        for row in by_day.get(day, []):
            if row["side"] == "buy":
                cash -= row["amount"] + row["fee"]
                held[row["symbol"]] = held.get(row["symbol"], 0.0) + row["shares"]
            else:
                cash += row["amount"] - row["fee"]
                held[row["symbol"]] = held.get(row["symbol"], 0.0) - row["shares"]

        market_value = 0.0
        for symbol, count in held.items():
            if count <= 0:
                continue
            price = closes[symbol].get(day)
            if price is not None:
                last_price[symbol] = price
            price = last_price.get(symbol)
            if price:
                market_value += count * price

        benchmark = benchmark_closes.get(day)
        if benchmark_base is None and benchmark:
            benchmark_base = benchmark
        payload.append(
            {
                "trade_date": day.isoformat(),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "total": round(cash + market_value, 2),
                "benchmark": round(benchmark / benchmark_base, 6)
                if benchmark and benchmark_base
                else None,
            }
        )

    stamped = now_iso()
    with connection() as conn:
        conn.execute("DELETE FROM paper_nav")
        conn.executemany(
            "INSERT INTO paper_nav(trade_date, cash, market_value, total, benchmark, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            [
                (item["trade_date"], item["cash"], item["market_value"], item["total"],
                 item["benchmark"], stamped)
                for item in payload
            ],
        )
    return len(payload)


def _benchmark_series(start: date, end: date) -> dict[date, float]:
    """沪深300 close per trading day, forward-filled (see app.services.benchmark)."""
    return benchmark_service.series(start, end, BENCHMARK)


def nav_series() -> list[dict[str, Any]]:
    rebuild_nav()
    with connection() as conn:
        rows = conn.execute(
            "SELECT trade_date, cash, market_value, total, benchmark FROM paper_nav "
            "ORDER BY trade_date"
        ).fetchall()
    if not rows:
        return []
    base = rows[0]["total"] or 1.0
    output = []
    for row in rows:
        output.append(
            {
                "trade_date": row["trade_date"],
                "total": row["total"],
                "nav": round(row["total"] / base, 4),
                "benchmark": round(row["benchmark"], 4) if row["benchmark"] else None,
                "cash": row["cash"],
                "market_value": row["market_value"],
            }
        )
    return output


def trades(limit: int = 50) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
