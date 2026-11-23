from app.services.indicators import boll, compute_all, macd, rsi, sma


def make_candles(closes):
    return [
        {
            "trade_date": f"2026-01-{index + 1:02d}",
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100,
            "amount": 1000,
        }
        for index, close in enumerate(closes)
    ]


def test_sma_known_values():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_rsi_bounds_and_length():
    values = [10 + (index % 5) for index in range(40)]
    result = rsi(values, 14)
    assert len(result) == len(values)
    assert result[0] is None
    numeric = [value for value in result if value is not None]
    assert numeric
    assert all(0 <= value <= 100 for value in numeric)


def test_macd_lengths():
    values = [10 + index * 0.1 for index in range(80)]
    result = macd(values)
    assert set(result) == {"dif", "dea", "hist"}
    assert len(result["dif"]) == len(values)
    assert result["dif"][-1] is not None


def test_boll_ranges():
    values = [10 + (index % 3) for index in range(40)]
    result = boll(values, 20, 2.0)
    assert len(result["mid"]) == len(values)
    assert result["upper"][-1] >= result["mid"][-1] >= result["lower"][-1]


def test_compute_all_shapes():
    result = compute_all(make_candles([10 + index * 0.2 for index in range(80)]))
    assert len(result["ma5"]) == 80
    assert len(result["macd"]["hist"]) == 80
    assert result["latest"]["close"] == 10 + 79 * 0.2
    assert result["latest"]["rsi14"] is not None