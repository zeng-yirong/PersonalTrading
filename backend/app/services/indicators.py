"""Technical indicators implemented with plain Python.

The frontend receives arrays aligned with the K-line dates; leading warm-up
positions are ``None``.
"""

from __future__ import annotations

import math
from typing import Any, Optional

Series = list[Optional[float]]


def sma(values: list[float], window: int) -> Series:
    out: Series = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        out.append(round(total / window, 3) if index >= window - 1 else None)
    return out


def ema(values: list[float], window: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (window + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def macd(values: list[float]) -> dict[str, Series]:
    if not values:
        empty: Series = []
        return {"dif": empty, "dea": empty, "hist": empty}
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = ema(dif, 9)
    hist = [2 * (a - b) for a, b in zip(dif, dea)]
    start = min(25, len(values) - 1)
    return {
        "dif": [None if i < start else round(v, 4) for i, v in enumerate(dif)],
        "dea": [None if i < start else round(v, 4) for i, v in enumerate(dea)],
        "hist": [None if i < start else round(v, 4) for i, v in enumerate(hist)],
    }


def rsi(values: list[float], window: int = 14) -> Series:
    if len(values) <= window:
        return [None] * len(values)
    out: Series = [None] * len(values)
    gains = 0.0
    losses = 0.0
    for i in range(1, window + 1):
        diff = values[i] - values[i - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain = gains / window
    avg_loss = losses / window
    out[window] = 100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 2)
    for i in range(window + 1, len(values)):
        diff = values[i] - values[i - 1]
        avg_gain = (avg_gain * (window - 1) + max(diff, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-diff, 0.0)) / window
        out[i] = 100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 2)
    return out


def boll(values: list[float], window: int = 20, width: float = 2.0) -> dict[str, Series]:
    mid = sma(values, window)
    upper: Series = []
    lower: Series = []
    for index, center in enumerate(mid):
        if center is None:
            upper.append(None)
            lower.append(None)
            continue
        window_values = values[index - window + 1 : index + 1]
        mean = sum(window_values) / window
        variance = sum((v - mean) ** 2 for v in window_values) / window
        std = math.sqrt(variance)
        upper.append(round(center + width * std, 3))
        lower.append(round(center - width * std, 3))
    return {"mid": mid, "upper": upper, "lower": lower}


def compute_all(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(item["close"]) for item in candles if item.get("close") is not None]
    if not closes:
        return {
            "ma5": [],
            "ma10": [],
            "ma20": [],
            "ma60": [],
            "macd": {"dif": [], "dea": [], "hist": []},
            "rsi14": [],
            "boll": {"mid": [], "upper": [], "lower": []},
            "latest": {},
        }

    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    macd_data = macd(closes)
    rsi14 = rsi(closes, 14)
    boll_data = boll(closes, 20, 2.0)

    def last(series: Series) -> Optional[float]:
        for value in reversed(series):
            if value is not None:
                return value
        return None

    latest = {
        "close": closes[-1],
        "ma5": last(ma5),
        "ma10": last(ma10),
        "ma20": last(ma20),
        "ma60": last(ma60),
        "dif": last(macd_data["dif"]),
        "dea": last(macd_data["dea"]),
        "macd_hist": last(macd_data["hist"]),
        "rsi14": last(rsi14),
        "boll_upper": last(boll_data["upper"]),
        "boll_mid": last(boll_data["mid"]),
        "boll_lower": last(boll_data["lower"]),
    }
    return {
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "macd": macd_data,
        "rsi14": rsi14,
        "boll": boll_data,
        "latest": latest,
    }