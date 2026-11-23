from app.services.symbols import asset_type, eastmoney_secid, exchange, normalize_code, sina_symbol


def test_normalize_code():
    assert normalize_code("000001") == "000001"
    assert normalize_code("sz000001") == "000001"
    assert normalize_code("600000.SH") == "600000"
    assert normalize_code("") == ""


def test_exchange_and_symbols():
    assert exchange("600000") == "SH"
    assert exchange("000001") == "SZ"
    assert exchange("830799") == "BJ"
    assert sina_symbol("600000") == "sh600000"
    assert sina_symbol("000001") == "sz000001"
    assert eastmoney_secid("600000") == "1.600000"
    assert eastmoney_secid("000001") == "0.000001"


def test_asset_type():
    assert asset_type("000001") == "stock"
    assert asset_type("600000") == "stock"
    assert asset_type("510050") == "etf"
    assert asset_type("159919") == "etf"