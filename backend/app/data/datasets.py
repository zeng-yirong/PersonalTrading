"""Dataset registry: what we pull from Tushare, and how.

Every Tushare interface falls into one of four pull shapes:

``date``    one request per trade date, whole market at once (``daily``)
``code``    one request per symbol with a date range (``index_daily``)
``period``  one request per report period, whole market at once (``income_vip``)
``once``    a single reference table (``stock_basic``)

The registry is data, not code: adding an interface is one :class:`DatasetSpec`
entry, and the backfill engine picks up its schema automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class DatasetSpec:
    name: str                 # table name in DuckDB
    api: str                  # Tushare api_name
    label: str
    mode: str                 # date | code | period | once
    date_param: str = "trade_date"
    date_field: str = "trade_date"
    fields: str = ""          # "" = let the API return its default field set
    fixed: tuple[tuple[str, str], ...] = ()
    code_source: str = "stocks"   # stocks | funds | index | sw | global
    keys: tuple[str, ...] = ("ts_code", "trade_date")
    daily: bool = False       # included in the "update today" command
    daily_date: bool = False  # code-mode spec the API can also serve by trade_date
    priority: int = 100       # lower runs first in a full backfill
    start_year: int = 2010    # earliest year worth backfilling
    note: str = ""

    @property
    def params_fixed(self) -> dict[str, str]:
        return dict(self.fixed)


# The order below is the backfill order for ``--all``: core price data first,
# then market internals, then financials and reference tables.
DATASETS: tuple[DatasetSpec, ...] = (
    # ---------------------------------------------------------------- prices
    DatasetSpec(
        name="daily",
        api="daily",
        label="A股日线",
        mode="date",
        daily=True,
        priority=10,
        start_year=2005,
        note="一次请求返回全市场当日行情",
    ),
    DatasetSpec(
        name="adj_factor",
        api="adj_factor",
        label="复权因子",
        mode="date",
        daily=True,
        priority=11,
        start_year=2005,
    ),
    DatasetSpec(
        name="daily_basic",
        api="daily_basic",
        label="每日指标",
        mode="date",
        daily=True,
        priority=12,
        start_year=2010,
        note="PE/PB/市值/换手率/量比",
    ),
    # ------------------------------------------------------------ market internals
    DatasetSpec(
        name="moneyflow",
        api="moneyflow",
        label="资金流向",
        mode="date",
        daily=True,
        priority=20,
        start_year=2015,
    ),
    DatasetSpec(
        name="stk_limit",
        api="stk_limit",
        label="涨跌停价",
        mode="date",
        daily=True,
        priority=21,
        start_year=2015,
    ),
    DatasetSpec(
        name="cyq_perf",
        api="cyq_perf",
        label="筹码分布",
        mode="date",
        daily=True,
        priority=22,
        start_year=2018,
    ),
    DatasetSpec(
        name="limit_list_d",
        api="limit_list_d",
        label="涨跌停统计",
        mode="date",
        daily=True,
        priority=23,
        start_year=2018,
    ),
    DatasetSpec(
        name="margin_detail",
        api="margin_detail",
        label="融资融券",
        mode="date",
        daily=True,
        priority=24,
        start_year=2015,
    ),
    DatasetSpec(
        name="top_list",
        api="top_list",
        label="龙虎榜",
        mode="date",
        priority=30,
        start_year=2015,
    ),
    DatasetSpec(
        name="top_inst",
        api="top_inst",
        label="龙虎榜机构",
        mode="date",
        priority=31,
        start_year=2015,
        keys=("trade_date", "ts_code", "exalter"),
    ),
    DatasetSpec(
        name="hsgt_top10",
        api="hsgt_top10",
        label="沪深股通十大",
        mode="date",
        priority=32,
        start_year=2018,
        keys=("trade_date", "ts_code"),
    ),
    DatasetSpec(
        name="stk_factor",
        api="stk_factor",
        label="技术因子",
        mode="date",
        daily=True,
        priority=40,
        start_year=2015,
        note="Tushare 预计算的技术指标，体积较大",
    ),
    DatasetSpec(
        name="block_trade",
        api="block_trade",
        label="大宗交易",
        mode="date",
        priority=41,
        start_year=2015,
        keys=("trade_date", "ts_code", "buyer", "seller"),
    ),
    # ------------------------------------------------------------------ funds
    DatasetSpec(
        name="fund_daily",
        api="fund_daily",
        label="ETF日线",
        mode="date",
        daily=True,
        priority=50,
        start_year=2010,
    ),
    DatasetSpec(
        name="fund_adj",
        api="fund_adj",
        label="ETF复权因子",
        mode="date",
        daily=True,
        priority=51,
        start_year=2010,
    ),
    DatasetSpec(
        name="fund_basic",
        api="fund_basic",
        label="ETF列表",
        mode="once",
        fixed=(("market", "E"),),
        priority=52,
        keys=("ts_code",),
    ),
    # ----------------------------------------------------------------- index
    DatasetSpec(
        name="index_basic",
        api="index_basic",
        label="指数列表",
        mode="once",
        priority=60,
        keys=("ts_code",),
    ),
    DatasetSpec(
        name="index_daily",
        api="index_daily",
        label="指数日线",
        mode="code",
        code_source="index",
        priority=61,
        start_year=2005,
    ),
    DatasetSpec(
        name="sw_daily",
        api="sw_daily",
        label="申万行业指数",
        mode="code",
        code_source="sw",
        daily=True,
        daily_date=True,
        priority=62,
        start_year=2010,
    ),
    DatasetSpec(
        name="index_global",
        api="index_global",
        label="全球指数",
        mode="code",
        code_source="global",
        priority=63,
        start_year=2010,
    ),
    # ------------------------------------------------------------ financials
    DatasetSpec(
        name="income_vip",
        api="income_vip",
        label="利润表",
        mode="period",
        date_param="period",
        date_field="end_date",
        keys=("ts_code", "end_date", "report_type"),
        priority=70,
    ),
    DatasetSpec(
        name="balancesheet_vip",
        api="balancesheet_vip",
        label="资产负债表",
        mode="period",
        date_param="period",
        date_field="end_date",
        keys=("ts_code", "end_date", "report_type"),
        priority=71,
    ),
    DatasetSpec(
        name="cashflow_vip",
        api="cashflow_vip",
        label="现金流量表",
        mode="period",
        date_param="period",
        date_field="end_date",
        keys=("ts_code", "end_date", "report_type"),
        priority=72,
    ),
    DatasetSpec(
        name="fina_indicator_vip",
        api="fina_indicator_vip",
        label="财务指标",
        mode="period",
        date_param="period",
        date_field="end_date",
        keys=("ts_code", "end_date"),
        priority=73,
    ),
    DatasetSpec(
        name="forecast_vip",
        api="forecast_vip",
        label="业绩预告",
        mode="period",
        date_param="period",
        date_field="end_date",
        keys=("ts_code", "end_date", "ann_date"),
        priority=74,
    ),
    DatasetSpec(
        name="express_vip",
        api="express_vip",
        label="业绩快报",
        mode="period",
        date_param="period",
        date_field="end_date",
        keys=("ts_code", "end_date"),
        priority=75,
    ),
    # ------------------------------------------------------------- reference
    DatasetSpec(
        name="dividend",
        api="dividend",
        label="分红送配",
        mode="date",
        date_param="ann_date",
        date_field="ann_date",
        priority=80,
        start_year=2015,
        keys=("ts_code", "ann_date", "end_date"),
    ),
    DatasetSpec(
        name="stk_holdernumber",
        api="stk_holdernumber",
        label="股东户数",
        mode="code",
        priority=81,
        start_year=2015,
        date_field="end_date",
        keys=("ts_code", "end_date"),
    ),
    DatasetSpec(
        name="stk_holdertrade",
        api="stk_holdertrade",
        label="股东增减持",
        mode="code",
        priority=82,
        start_year=2015,
        date_field="ann_date",
        # The API field is ``in_de`` (IN/DE), not ``direction``.
        keys=("ts_code", "ann_date", "holder_name", "in_de", "change_vol"),
    ),
    DatasetSpec(
        name="share_float",
        api="share_float",
        label="限售解禁",
        mode="code",
        priority=83,
        start_year=2015,
        date_field="ann_date",
        keys=("ts_code", "ann_date", "float_date"),
    ),
    DatasetSpec(
        name="stock_company",
        api="stock_company",
        label="公司概况",
        mode="code",
        priority=90,
        keys=("ts_code",),
        note="每个代码只有一行",
    ),
    DatasetSpec(
        name="new_share",
        api="new_share",
        label="新股发行",
        mode="once",
        priority=91,
        keys=("ts_code",),
    ),
    DatasetSpec(
        name="ths_index",
        api="ths_index",
        label="同花顺板块",
        mode="once",
        priority=92,
        keys=("ts_code",),
    ),
    # ------------------------------------------------ A-share sentiment proxies
    # News text and announcement APIs are not enabled on this gateway, so the
    # "sentiment" role is built on heat rankings, concept limit lists and
    # broker picks — real data instead of fabricated news.
    DatasetSpec(
        name="ths_hot",
        api="ths_hot",
        label="同花顺热榜",
        mode="date",
        daily=True,
        priority=25,
        start_year=2025,
        keys=("trade_date", "ts_code", "data_type"),
        note="盘中热度排名，是 A 股“社媒关注度”的可得代理",
    ),
    DatasetSpec(
        name="dc_hot",
        api="dc_hot",
        label="东财热榜",
        mode="date",
        priority=26,
        start_year=2025,
        keys=("trade_date", "ts_code", "data_type"),
    ),
    DatasetSpec(
        name="limit_cpt_list",
        api="limit_cpt_list",
        label="涨停概念榜",
        mode="date",
        priority=27,
        start_year=2025,
        keys=("trade_date", "ts_code"),
    ),
    DatasetSpec(
        name="broker_recommend",
        api="broker_recommend",
        label="券商金股",
        mode="month",
        date_param="month",
        priority=28,
        start_year=2024,
        keys=("month", "ts_code", "broker"),
    ),
    DatasetSpec(
        name="stk_surv",
        api="stk_surv",
        label="机构调研",
        mode="code",
        priority=29,
        start_year=2023,
        date_field="surv_date",
        keys=("ts_code", "surv_date", "rece_org"),
    ),
)

BY_NAME: dict[str, DatasetSpec] = {spec.name: spec for spec in DATASETS}

# Datasets that have a table but no spec entry would be invisible to `status`,
# so keep the core panel tables listed here as well.
CORE_TABLES = ("symbols", "calendar", "daily", "sync_log", "dataset_progress")


def specs_for(names: Optional[list[str]] = None) -> list[DatasetSpec]:
    if not names:
        return sorted(DATASETS, key=lambda spec: spec.priority)
    wanted = {name.strip() for name in names if name.strip()}
    unknown = wanted - set(BY_NAME)
    if unknown:
        raise KeyError("未知数据集：" + "、".join(sorted(unknown)))
    return sorted((BY_NAME[name] for name in wanted), key=lambda spec: spec.priority)


def quarter_ends(start_year: int, today: Optional[date] = None) -> list[date]:
    """Report periods to pull: every quarter end from ``start_year`` to now."""
    today = today or date.today()
    periods: list[date] = []
    for year in range(start_year, today.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            candidate = date(year, month, day)
            if candidate <= today:
                periods.append(candidate)
    return periods
