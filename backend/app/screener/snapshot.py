"""Materialise one wide row per symbol, so screening is a plain WHERE clause.

Computing returns, MA gaps, MACD and 90-day event counts for 5 500 symbols on
every request would take seconds; doing it once per trading day takes about a
second and makes each query a simple filtered scan of ~5 500 rows.

Auxiliary tables are joined on **their own latest available date** (not the
requested one), because margin / chip / technical-factor data publishes hours
after the close. The response reports which date each block came from rather than
pretending everything is from today.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from app.data import store
from app.factor import pead as pead_module
from app.factor import promoted

# Auxiliary tables and the column that carries their date.
AUX_TABLES = {
    "daily_basic": "trade_date",
    "stk_factor": "trade_date",
    "moneyflow": "trade_date",
    "margin_detail": "trade_date",
    "cyq_perf": "trade_date",
    "limit_list_d": "trade_date",
    "top_list": "trade_date",
    "hsgt_top10": "trade_date",
    "ths_hot": "trade_date",
}

SNAPSHOT_NAME = "screen_snapshot"

COLUMNS = (
    "trade_date", "symbol", "ts_code", "name", "industry", "board", "asset_type",
    "close", "pct_chg", "ret5", "ret20", "ret60", "amount", "avg_amount_20d",
    "turnover_rate", "volume_ratio",
    "gap_ma20", "gap_ma60", "drawdown_52w", "rsi12", "macd_hist", "boll_pos",
    "kdj_k", "kdj_d", "kdj_j", "cci",
    "a_vstd_20", "a_vstd_5", "a_qtl20_60", "a_corr_20", "a_corr_5", "a_mingap_60", "a_kbar_kup",
    "fd_pead",
    "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "pe_pct_3y",
    "roe", "gross_margin", "net_margin", "debt_ratio", "revenue_yoy", "profit_yoy",
    "mf_net_1d", "mf_net_20d", "elg_net_1d", "margin_bal", "margin_chg_20d",
    "chip_winner",
    "limit_up_90d", "limit_up_5d", "lhb_90d", "north_90d", "hot_rank",
)


def _latest_date(conn, table: str, column: str, upto: Optional[date]) -> Optional[date]:
    if not store.table_exists(conn, table):
        return None
    sql = f"SELECT MAX({column}) FROM {table}"
    params: list[Any] = []
    if upto:
        sql += f" WHERE {column} <= ?"
        params.append(upto)
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] else None


def _sql(latest: date, aux: dict[str, Optional[date]]) -> str:
    def d(name: str, fallback: date) -> str:
        value = aux.get(name) or fallback
        return f"DATE '{value.isoformat()}'"

    pe_since = latest - timedelta(days=365 * 3)
    events_since = latest - timedelta(days=90)
    flow_since = latest - timedelta(days=40)
    margin_since = latest - timedelta(days=45)
    promoted_columns = promoted.select_columns(indent="        ")
    pead_cte = pead_module.cte(pead_module.dates_for_snapshot(latest))

    return f"""
CREATE OR REPLACE TABLE {SNAPSHOT_NAME} AS
WITH bars AS (
    SELECT symbol, trade_date, close,
           close * COALESCE(adj_factor, 1) AS c_adj,
           high  * COALESCE(adj_factor, 1) AS h_adj,
           amount, pre_close,
           row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
    FROM daily
    WHERE trade_date >= (DATE '{latest.isoformat()}' - INTERVAL 400 DAY)
),
feat_bars AS (
    SELECT symbol, trade_date,
           open * COALESCE(adj_factor, 1)  AS o_adj,
           high * COALESCE(adj_factor, 1)  AS h_adj,
           low  * COALESCE(adj_factor, 1)  AS l_adj,
           close * COALESCE(adj_factor, 1) AS c_adj,
           volume,
           close * COALESCE(adj_factor, 1)
               / NULLIF(lag(close * COALESCE(adj_factor, 1))
                        OVER (PARTITION BY symbol ORDER BY trade_date), 0) - 1 AS ret
    FROM daily
    WHERE trade_date >= (DATE '{latest.isoformat()}' - INTERVAL 400 DAY)
),
feat AS (
    SELECT symbol,
        {promoted_columns},
        row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn2
    FROM feat_bars
),
price AS (
    SELECT
        symbol,
        MAX(CASE WHEN rn = 1 THEN close END)                        AS close,
        MAX(CASE WHEN rn = 1 THEN c_adj END)                        AS c_adj,
        -- 收益率与均线一律用复权价：除权/送股日的原始价会显示成假跌
        MAX(CASE WHEN rn = 1 THEN c_adj END)
            / NULLIF(MAX(CASE WHEN rn = 2 THEN c_adj END), 0) - 1   AS pct_chg,
        MAX(CASE WHEN rn = 1 THEN c_adj END)
            / NULLIF(MAX(CASE WHEN rn = 6 THEN c_adj END), 0) - 1   AS ret5,
        MAX(CASE WHEN rn = 1 THEN c_adj END)
            / NULLIF(MAX(CASE WHEN rn = 21 THEN c_adj END), 0) - 1  AS ret20,
        MAX(CASE WHEN rn = 1 THEN c_adj END)
            / NULLIF(MAX(CASE WHEN rn = 61 THEN c_adj END), 0) - 1  AS ret60,
        AVG(CASE WHEN rn <= 20 THEN c_adj END)                      AS ma20,
        AVG(CASE WHEN rn <= 60 THEN c_adj END)                      AS ma60,
        MAX(CASE WHEN rn <= 250 THEN h_adj END)                     AS high_52w,
        MAX(CASE WHEN rn = 1 THEN amount END)                       AS amount,
        AVG(CASE WHEN rn <= 20 THEN amount END)                     AS avg_amount_20d
    FROM bars
    GROUP BY symbol
),
basic AS (
    SELECT symbol, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv,
           turnover_rate, volume_ratio
    FROM daily_basic WHERE trade_date = {d('daily_basic', latest)}
),
pe_rank AS (
    SELECT symbol, pe_pct FROM (
        SELECT symbol, pe_ttm,
               PERCENT_RANK() OVER (PARTITION BY symbol ORDER BY pe_ttm) * 100 AS pe_pct,
               row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
        FROM daily_basic
        WHERE trade_date >= DATE '{pe_since.isoformat()}' AND pe_ttm IS NOT NULL
    ) WHERE rn = 1
),
factor AS (
    SELECT symbol_of(ts_code) AS symbol, rsi_12 AS rsi12, macd AS macd_hist,
           boll_upper, boll_lower, kdj_k, kdj_d, kdj_j, cci
    FROM stk_factor WHERE trade_date = {d('stk_factor', latest)}
),
flow AS (
    SELECT symbol_of(ts_code) AS symbol,
           MAX(CASE WHEN trade_date = {d('moneyflow', latest)} THEN net_mf_amount END) AS mf_net_1d,
           SUM(CASE WHEN rn <= 20 THEN net_mf_amount END) AS mf_net_20d,
           MAX(CASE WHEN trade_date = {d('moneyflow', latest)}
                    THEN buy_elg_amount - sell_elg_amount END) AS elg_net_1d
    FROM (
        SELECT *, row_number() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
        FROM moneyflow
        WHERE trade_date >= DATE '{flow_since.isoformat()}'
    )
    GROUP BY 1
),
margin_prev AS (
    SELECT trade_date FROM (
        SELECT DISTINCT trade_date FROM margin_detail
        WHERE trade_date <= {d('margin_detail', latest)}
        ORDER BY trade_date DESC LIMIT 1 OFFSET 20
    )
),
margin AS (
    SELECT symbol_of(ts_code) AS symbol,
           MAX(CASE WHEN trade_date = {d('margin_detail', latest)} THEN rzye END) AS margin_bal,
           MAX(CASE WHEN trade_date = (SELECT trade_date FROM margin_prev) THEN rzye END) AS margin_old
    FROM margin_detail
    WHERE trade_date >= DATE '{margin_since.isoformat()}'
    GROUP BY symbol_of(ts_code)
),
margin_calc AS (
    SELECT symbol, margin_bal,
           CASE WHEN margin_old > 0 THEN margin_bal / margin_old - 1 END AS margin_chg_20d
    FROM margin
),
chips AS (
    SELECT symbol_of(ts_code) AS symbol, winner_rate AS chip_winner
    FROM cyq_perf WHERE trade_date = {d('cyq_perf', latest)}
),
limits AS (
    SELECT symbol_of(ts_code) AS symbol,
           COUNT(*) FILTER (WHERE "limit" = 'U') AS limit_up_90d,
           COUNT(*) FILTER (WHERE "limit" = 'U'
                            AND trade_date >= (DATE '{latest.isoformat()}' - INTERVAL 7 DAY)) AS limit_up_5d
    FROM limit_list_d WHERE trade_date >= DATE '{events_since.isoformat()}'
    GROUP BY symbol_of(ts_code)
),
lhb AS (
    SELECT symbol_of(ts_code) AS symbol, COUNT(*) AS lhb_90d
    FROM top_list WHERE trade_date >= DATE '{events_since.isoformat()}'
    GROUP BY symbol_of(ts_code)
),
north AS (
    SELECT symbol_of(ts_code) AS symbol, COUNT(*) AS north_90d
    FROM hsgt_top10 WHERE trade_date >= DATE '{events_since.isoformat()}'
    GROUP BY symbol_of(ts_code)
),
hot AS (
    SELECT symbol_of(ts_code) AS symbol, MIN(rank) AS hot_rank
    FROM ths_hot WHERE trade_date = {d('ths_hot', latest)}
    GROUP BY symbol_of(ts_code)
),
fin AS (
    SELECT ts_code, roe, gross_margin, net_margin, debt_ratio, revenue_yoy, profit_yoy
    FROM (
        SELECT ts_code, roe, grossprofit_margin AS gross_margin, netprofit_margin AS net_margin,
               debt_to_assets AS debt_ratio, or_yoy AS revenue_yoy, netprofit_yoy AS profit_yoy,
               row_number() OVER (PARTITION BY ts_code ORDER BY end_date DESC) AS rn
        FROM fina_indicator_vip
        WHERE ann_date IS NOT NULL AND ann_date <= DATE '{latest.isoformat()}'
    ) WHERE rn = 1
),
{pead_cte}
SELECT
    DATE '{latest.isoformat()}' AS trade_date,
    s.symbol, s.ts_code, s.name, s.industry, s.board, s.asset_type,
    p.close, ROUND(p.pct_chg * 100, 4) AS pct_chg,
    ROUND(p.ret5 * 100, 4) AS ret5, ROUND(p.ret20 * 100, 4) AS ret20,
    ROUND(p.ret60 * 100, 4) AS ret60,
    p.amount, p.avg_amount_20d,
    b.turnover_rate, b.volume_ratio,
    ROUND((p.c_adj / NULLIF(p.ma20, 0) - 1) * 100, 4) AS gap_ma20,
    ROUND((p.c_adj / NULLIF(p.ma60, 0) - 1) * 100, 4) AS gap_ma60,
    ROUND((p.c_adj / NULLIF(p.high_52w, 0) - 1) * 100, 4) AS drawdown_52w,
    f.rsi12, f.macd_hist,
    CASE WHEN f.boll_upper > f.boll_lower
         THEN (p.close - f.boll_lower) / (f.boll_upper - f.boll_lower) END AS boll_pos,
    f.kdj_k, f.kdj_d, f.kdj_j, f.cci,
    ft.a_vstd_20, ft.a_vstd_5, ft.a_qtl20_60, ft.a_corr_20, ft.a_corr_5,
    ft.a_mingap_60, ft.a_kbar_kup,
    pe.fd_pead,
    b.pe_ttm, b.pb, b.ps_ttm, b.dv_ttm, b.total_mv, b.circ_mv,
    ROUND(pr.pe_pct, 2) AS pe_pct_3y,
    fin.roe, fin.gross_margin, fin.net_margin, fin.debt_ratio, fin.revenue_yoy, fin.profit_yoy,
    fl.mf_net_1d, fl.mf_net_20d, fl.elg_net_1d, mc.margin_bal,
    ROUND(mc.margin_chg_20d * 100, 4) AS margin_chg_20d,
    ch.chip_winner,
    COALESCE(li.limit_up_90d, 0) AS limit_up_90d,
    COALESCE(li.limit_up_5d, 0) AS limit_up_5d,
    COALESCE(lh.lhb_90d, 0) AS lhb_90d,
    COALESCE(no.north_90d, 0) AS north_90d,
    ho.hot_rank
FROM symbols s
LEFT JOIN price p  ON p.symbol = s.symbol
LEFT JOIN basic b  ON b.symbol = s.symbol
LEFT JOIN pe_rank pr ON pr.symbol = s.symbol
LEFT JOIN factor f ON f.symbol = s.symbol
LEFT JOIN feat ft  ON ft.symbol = s.symbol AND ft.rn2 = 1
LEFT JOIN flow fl  ON fl.symbol = s.symbol
LEFT JOIN margin_calc mc ON mc.symbol = s.symbol
LEFT JOIN chips ch ON ch.symbol = s.symbol
LEFT JOIN limits li ON li.symbol = s.symbol
LEFT JOIN lhb lh  ON lh.symbol = s.symbol
LEFT JOIN north no ON no.symbol = s.symbol
LEFT JOIN hot ho  ON ho.symbol = s.symbol
LEFT JOIN fin  ON fin.ts_code = s.ts_code
LEFT JOIN pead pe ON pe.symbol = s.symbol AND pe.trade_date = DATE '{latest.isoformat()}'
WHERE p.close IS NOT NULL
"""


def build(trade_date: Optional[date] = None) -> dict[str, Any]:
    """Rebuild the snapshot table; returns row count and per-block source dates."""
    # One write connection for the whole build: DuckDB refuses to open a read-only
    # handle to a file this process already has open read-write.
    with store.session() as conn:
        conn.execute(
            "CREATE OR REPLACE MACRO symbol_of(code) AS split_part(code, '.', 1)"
        )
        latest = trade_date or conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if latest is None:
            raise RuntimeError("面板为空，请先运行 scripts/sync_market.py")

        aux = {
            name: _latest_date(conn, name, column, latest)
            for name, column in AUX_TABLES.items()
        }
        conn.execute(_sql(latest, aux))
        rows = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_NAME}").fetchone()[0]
        sources = {name: (value.isoformat() if value else None) for name, value in aux.items()}
        # sources 也要落库：只在"重建那一次"返回的话，缓存命中时前端就看到 null，
        # 会以为所有数据源都没有日期。
        conn.execute(
            "CREATE OR REPLACE TABLE screen_meta AS "
            "SELECT ?::DATE AS trade_date, now() AS built_at, ? AS sources_json",
            [latest, json.dumps(sources, ensure_ascii=False)],
        )
    return {
        "trade_date": latest.isoformat(),
        "symbols": int(rows),
        "sources": sources,
    }


def ensure_built() -> dict[str, Any]:
    """Build once per trading day; also rebuild when the schema drifted."""
    state: Optional[dict[str, Any]] = None
    try:
        with store.session(read_only=True) as conn:
            try:
                meta = conn.execute(
                    "SELECT trade_date, built_at, sources_json FROM screen_meta"
                ).fetchone()
            except Exception:  # noqa: BLE001 - 老库的 screen_meta 还没有这一列
                meta = conn.execute("SELECT trade_date, built_at FROM screen_meta").fetchone()
            sources: Optional[dict[str, Any]] = None
            if meta is not None and len(meta) > 2 and meta[2]:
                try:
                    sources = json.loads(meta[2])
                except json.JSONDecodeError:
                    sources = None
            latest = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
            existing = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info('{SNAPSHOT_NAME}')").fetchall()
            }
            count = (
                conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_NAME}").fetchone()[0]
                if existing
                else 0
            )
            # 老库的 screen_meta 没有 sources_json：缓存命中时现算一次（7 个
            # MAX(date) 查询，毫秒级），下一次重建会把它写进表里。
            if sources is None and count:
                sources = {
                    name: (
                        value.isoformat()
                        if (value := _latest_date(conn, name, column, latest))
                        else None
                    )
                    for name, column in AUX_TABLES.items()
                }
        # A new field in COLUMNS means the code changed: the cached table is stale.
        missing = set(COLUMNS) - existing
        if count and meta and latest and meta[0] == latest and not missing:
            state = {
                "trade_date": meta[0].isoformat(),
                "symbols": int(count),
                "cached": True,
                "sources": sources,
            }
    except Exception:  # noqa: BLE001 - tables missing on first run
        state = None

    if state is not None:
        return state
    return build()
