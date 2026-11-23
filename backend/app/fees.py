"""A-share trading costs and lot sizing, shared by paper trading and backtests.

Both paths must charge the same money, otherwise a backtest is not an estimate of
what this project's own paper account would earn. Keeping the constants in one
place is the cheapest way to guarantee that.
"""

from __future__ import annotations

import math

LOT = 100
COMMISSION_RATE = 0.00025   # 万 2.5
COMMISSION_MIN = 5.0        # per order
STAMP_RATE = 0.0005         # sells only
TRANSFER_RATE = 0.00001     # 过户费


def fees(side: str, amount: float) -> float:
    """Round-trip cost of one order whose notional is ``amount`` yuan."""
    commission = max(COMMISSION_MIN, amount * COMMISSION_RATE)
    transfer = amount * TRANSFER_RATE
    stamp = amount * STAMP_RATE if side == "sell" else 0.0
    return round(commission + transfer + stamp, 2)


def shares_for(amount: float, price: float) -> int:
    """Whole lots (100 shares) affordable with ``amount`` at ``price``."""
    if price <= 0:
        return 0
    return int(math.floor(amount / price / LOT) * LOT)
