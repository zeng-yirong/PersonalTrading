"""The screener's field catalogue.

A natural-language query is only safe if the model can pick from a closed set of
fields. This module is that set: name, type, unit and a one-line description that
goes into the prompt, plus the validation the runner applies before building SQL.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str          # money | ratio | count | text | rank
    unit: str
    description: str
    scale: float = 1.0  # value multiplier applied before display only


# ``kind`` drives both formatting and which operators make sense.
FIELDS: tuple[Field, ...] = (
    # ------------------------------------------------------------------ price
    Field("close", "收盘价", "money", "元", "最新收盘价（未复权）"),
    Field("pct_chg", "当日涨跌幅", "ratio", "%", "最近一个交易日涨跌幅"),
    Field("ret5", "近5日涨跌幅", "ratio", "%", "5 个交易日累计涨跌幅"),
    Field("ret20", "近20日涨跌幅", "ratio", "%", "20 个交易日累计涨跌幅"),
    Field("ret60", "近60日涨跌幅", "ratio", "%", "60 个交易日累计涨跌幅"),
    Field("amount", "成交额", "money", "元", "最近交易日成交额"),
    Field("avg_amount_20d", "20日均成交额", "money", "元", "20 日平均成交额，衡量流动性"),
    Field("turnover_rate", "换手率", "ratio", "%", "最近交易日换手率"),
    Field("volume_ratio", "量比", "ratio", "倍", "当日成交量 / 过去5日均量"),
    # -------------------------------------------------------------- technical
    Field("gap_ma20", "偏离20日线", "ratio", "%", "收盘价相对 MA20 的偏离度"),
    Field("gap_ma60", "偏离60日线", "ratio", "%", "收盘价相对 MA60 的偏离度"),
    Field("drawdown_52w", "距52周高点", "ratio", "%", "收盘价相对近一年最高价的回撤（负值）"),
    Field("rsi12", "RSI12", "ratio", "", "12 日相对强弱指标，0-100，>70 超买"),
    Field("macd_hist", "MACD柱", "ratio", "", "MACD 柱状值，正为多头"),
    Field("boll_pos", "布林带位置", "ratio", "", "0=下轨 1=上轨，>1 突破上轨"),
    # -------------------------------------------------------------- valuation
    Field("pe_ttm", "市盈率TTM", "ratio", "倍", "滚动市盈率，负值表示亏损"),
    Field("pb", "市净率", "ratio", "倍", "市净率"),
    Field("ps_ttm", "市销率TTM", "ratio", "倍", "滚动市销率"),
    Field("dv_ttm", "股息率TTM", "ratio", "%", "滚动股息率"),
    Field("total_mv", "总市值", "money", "元", "总市值"),
    Field("circ_mv", "流通市值", "money", "元", "流通市值"),
    Field("pe_pct_3y", "PE三年分位", "ratio", "%", "当前 PE 在近三年中的分位（0-100）"),
    # ------------------------------------------------------------ fundamental
    Field("roe", "ROE", "ratio", "%", "净资产收益率（最近报告期）"),
    Field("gross_margin", "毛利率", "ratio", "%", "最近报告期毛利率"),
    Field("net_margin", "净利率", "ratio", "%", "最近报告期净利率"),
    Field("debt_ratio", "资产负债率", "ratio", "%", "最近报告期资产负债率"),
    Field("revenue_yoy", "营收同比", "ratio", "%", "最近报告期营业收入同比增速"),
    Field("profit_yoy", "净利同比", "ratio", "%", "最近报告期归母净利润同比增速"),
    # ------------------------------------------------------------------ flow
    Field("mf_net_1d", "当日主力净流入", "money", "万元", "当日主力资金净流入"),
    Field("mf_net_20d", "20日主力净流入", "money", "万元", "20 日累计主力资金净流入"),
    Field("elg_net_1d", "当日超大单净额", "money", "万元", "当日超大单净买入"),
    Field("margin_bal", "融资余额", "money", "元", "最新融资余额"),
    Field("margin_chg_20d", "融资余额20日变化", "ratio", "%", "融资余额近 20 日变化率"),
    Field("chip_winner", "筹码获利比例", "ratio", "%", "当前价格下的获利盘占比"),
    # --------------------------------------------------------------- sentiment
    Field("limit_up_90d", "近90日涨停次数", "count", "次", "90 日内涨停次数"),
    Field("limit_up_5d", "近5日涨停次数", "count", "次", "5 个交易日内涨停次数，用于避开连续涨停的票"),
    Field("lhb_90d", "近90日龙虎榜次数", "count", "次", "90 日内登上龙虎榜次数"),
    Field("north_90d", "近90日北向上榜", "count", "次", "90 日内进入沪深股通十大次数"),
    Field("hot_rank", "热度排名", "rank", "名", "同花顺热榜当前排名，越小越热"),
    # ------------------------------------------------- 技术指标（Tushare 预计算）
    Field("kdj_k", "KDJ-K", "ratio", "", "随机指标 K 值，0-100（Tushare 预计算）"),
    Field("kdj_d", "KDJ-D", "ratio", "", "随机指标 D 值，0-100"),
    Field("kdj_j", "KDJ-J", "ratio", "", "随机指标 J 值，可超过 100 或低于 0"),
    Field("cci", "CCI", "ratio", "", "顺势指标，±100 为超买超卖参考"),
    # ------------------------------------------- 外部因子（Alpha158，已晋级面板）
    Field("a_vstd_20", "量能波动20日", "ratio", "", "20 日成交量标准差 / 均值（Alpha158 VSTD20）"),
    Field("a_vstd_5", "量能波动5日", "ratio", "", "5 日成交量标准差 / 均值（Alpha158 VSTD5）"),
    Field("a_qtl20_60", "60日20分位偏离", "ratio", "%", "60 日收盘 20 分位 / 当前价 − 1（Alpha158 QTLD60）"),
    Field("a_corr_20", "量价相关20日", "ratio", "", "20 日收盘与对数成交量的相关系数（Alpha158 CORR20）"),
    Field("a_corr_5", "量价相关5日", "ratio", "", "5 日收盘与对数成交量的相关系数（Alpha158 CORR5）"),
    Field("a_mingap_60", "距60日低点", "ratio", "%", "当前价 / 60 日最低价 − 1（Alpha158 LOW60）"),
    Field("a_kbar_kup", "上影线占比", "ratio", "%", "（最高 − 开盘）/ 开盘（Alpha158 KUP）"),
    Field("fd_pead", "公告后漂移", "ratio", "%",
          "最近一次财报公告日至今日相对沪深300 的累计超额（≤120 个交易日，正=跑赢）"),
    # ------------------------------------------------------------- attributes
    Field("industry", "行业", "text", "", "Tushare 行业分类"),
    Field("board", "板块", "text", "", "沪市主板/创业板/科创板/北交所/ETF"),
)

BY_NAME: dict[str, Field] = {field.name: field for field in FIELDS}

NUMERIC_KINDS = {"money", "ratio", "count", "rank"}

OPERATORS = ("<", "<=", ">", ">=", "==", "!=", "between", "in", "contains")


def describe_for_prompt() -> str:
    """Compact catalogue used in the NL→query prompt."""
    lines = []
    for field in FIELDS:
        unit = f"，单位：{field.unit}" if field.unit else ""
        lines.append(f"- {field.name}（{field.label}，{field.kind}{unit}）：{field.description}")
    return "\n".join(lines)


def validate_field(name: str) -> Field:
    field = BY_NAME.get(name)
    if field is None:
        raise ValueError(f"未知字段 {name}；可用字段见 /api/screener/fields")
    return field
