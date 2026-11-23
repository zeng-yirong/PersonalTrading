"""The factor library: which columns the research engine may rank, and why.

Every entry names a column that exists in the backtest cross-section (``bt_feat``),
so a factor is evaluated on exactly the same point-in-time data a backtest would
trade on. ``direction`` records the prior ("higher is better" = +1); the report
compares the measured sign with it instead of silently flipping the factor.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ("动量", "技术", "估值", "质量", "成长", "资金", "情绪", "规模")


@dataclass(frozen=True)
class Factor:
    name: str
    label: str
    category: str
    direction: int      # +1: 越大越好；-1: 越小越好；0: 无先验
    note: str
    source: str = "panel"   # panel = 本项目面板字段；alpha158 = 外部导入


FACTORS: tuple[Factor, ...] = (
    # ---------------------------------------------------------------- 动量
    Factor("ret5", "近5日涨幅", "动量", 1, "短期反转/动量，A 股短周期常呈反转"),
    Factor("ret20", "近20日涨幅", "动量", 1, "月度动量"),
    Factor("ret60", "近60日涨幅", "动量", 1, "季度动量"),
    Factor("pct_chg", "当日涨跌幅", "动量", 0, "单日涨跌，噪音大，作为对照"),
    # ---------------------------------------------------------------- 技术
    Factor("gap_ma20", "偏离20日线", "技术", 1, "收盘价相对 MA20 的偏离"),
    Factor("gap_ma60", "偏离60日线", "技术", 1, "中期趋势位置"),
    Factor("drawdown_52w", "距52周高点", "技术", 1, "回撤越小越接近新高"),
    Factor("rsi12", "RSI12", "技术", 0, "超买超卖，方向不预设"),
    Factor("macd_hist", "MACD柱", "技术", 1, "红柱代表多头动能"),
    Factor("boll_pos", "布林带位置", "技术", 0, "0 下轨 / 1 上轨"),
    # ---------------------------------------------------------------- 估值
    Factor("pe_ttm", "市盈率TTM", "估值", -1, "低估值溢价（负值表示亏损）"),
    Factor("pb", "市净率", "估值", -1, "低 PB 溢价"),
    Factor("ps_ttm", "市销率TTM", "估值", -1, "低 PS 溢价"),
    Factor("dv_ttm", "股息率TTM", "估值", 1, "高股息"),
    # ---------------------------------------------------------------- 规模
    Factor("total_mv", "总市值", "规模", -1, "小市值效应"),
    Factor("circ_mv", "流通市值", "规模", -1, "小流通盘效应"),
    # ---------------------------------------------------------------- 质量
    Factor("roe", "ROE", "质量", 1, "盈利能力"),
    Factor("gross_margin", "毛利率", "质量", 1, "定价权"),
    Factor("net_margin", "净利率", "质量", 1, "费用控制"),
    Factor("debt_ratio", "资产负债率", "质量", -1, "低杠杆"),
    # ---------------------------------------------------------------- 成长
    Factor("revenue_yoy", "营收同比", "成长", 1, "收入增长"),
    Factor("profit_yoy", "净利同比", "成长", 1, "利润增长"),
    # ---------------------------------------------------------------- 资金
    Factor("mf_net_1d", "当日主力净流入", "资金", 1, "单日资金"),
    Factor("mf_net_20d", "20日主力净流入", "资金", 1, "月度资金"),
    Factor("elg_net_1d", "当日超大单净额", "资金", 1, "大单方向"),
    Factor("margin_bal", "融资余额", "资金", 0, "绝对值受市值影响，方向不预设"),
    Factor("margin_chg_20d", "融资余额20日变化", "资金", 1, "杠杆资金加仓"),
    Factor("chip_winner", "筹码获利比例", "资金", 1, "获利盘占比，筹码集中度代理"),
    # ---------------------------------------------------------------- 情绪
    Factor("limit_up_90d", "近90日涨停次数", "情绪", 1, "题材活跃度"),
    Factor("limit_up_5d", "近5日涨停次数", "情绪", -1, "连续涨停后的回撤风险"),
    Factor("lhb_90d", "近90日龙虎榜次数", "情绪", 1, "资金关注度"),
    Factor("north_90d", "近90日北向上榜", "情绪", 1, "外资关注（仅十大成交股口径）"),
    Factor("hot_rank", "热度排名", "情绪", -1, "排名越小越热"),
    # ---------------------------------------------------------------- 流动性
    Factor("turnover_rate", "换手率", "流动性", 0, "过高偏低都不好，方向不预设"),
    Factor("volume_ratio", "量比", "流动性", 0, "放量程度"),
    Factor("amount", "成交额", "流动性", 0, "流动性规模"),
    Factor("avg_amount_20d", "20日均成交额", "流动性", 0, "流动性水平"),
)

BY_NAME: dict[str, Factor] = {factor.name: factor for factor in FACTORS}

# Tushare's precomputed technical indicators were always in the panel, but never
# offered as factors; they are cheap to evaluate and worth a look.
TECH_EXTRA: tuple[Factor, ...] = (
    Factor("kdj_k", "KDJ-K", "技术", 0, "随机指标 K 值（Tushare 预计算）"),
    Factor("kdj_d", "KDJ-D", "技术", 0, "随机指标 D 值（Tushare 预计算）"),
    Factor("kdj_j", "KDJ-J", "技术", 0, "随机指标 J 值（Tushare 预计算）"),
    Factor("cci", "CCI", "技术", 0, "顺势指标（Tushare 预计算）"),
)

# Promoted from the fundamentals batch: now a panel column (bt_feat / snapshot).
FUND_EXTRA: tuple[Factor, ...] = (
    Factor(
        "fd_pead",
        "公告后漂移",
        "外部因子·已晋级",
        1,
        "最近一次财报公告日至今日相对沪深300 的累计超额（≤120 个交易日，正=跑赢）",
    ),
)


def panel_factors() -> tuple[Factor, ...]:
    """Everything evaluable directly from ``bt_feat`` (base + promoted + tech)."""
    from app.factor import promoted

    return FACTORS + TECH_EXTRA + FUND_EXTRA + tuple(promoted.factors())


def registry() -> dict[str, Factor]:
    """Panel factors plus the imported ones (lazy import avoids a cycle)."""
    from app.factor import academic, alpha158, fundamental, gtja

    merged = {factor.name: factor for factor in panel_factors()}
    for module in (alpha158, academic, gtja, fundamental):
        merged.update({factor.name: factor for factor in module.FACTORS})
    return merged


def validate(names: list[str]) -> list[Factor]:
    """Resolve requested factor names; unknown names are a hard error."""
    known = registry()
    if not names:
        return list(panel_factors())
    missing = [name for name in names if name not in known]
    if missing:
        raise ValueError(f"未知因子：{'、'.join(missing)}；可用因子见 /api/factors/library")
    return [known[name] for name in names]


def imported() -> list[Factor]:
    from app.factor import academic, alpha158, fundamental, gtja

    output: list[Factor] = []
    for module in (alpha158, academic, gtja, fundamental):
        output.extend(module.FACTORS)
    return output


def alpha158_factors() -> list[Factor]:
    from app.factor import alpha158

    return list(alpha158.FACTORS)


def academic_factors() -> list[Factor]:
    from app.factor import academic

    return list(academic.FACTORS)


def gtja_factors() -> list[Factor]:
    from app.factor import gtja

    return list(gtja.FACTORS)


def fundamental_factors() -> list[Factor]:
    from app.factor import fundamental

    return list(fundamental.FACTORS)


def catalogue(include_imported: bool = False) -> list[dict[str, object]]:
    factors = list(panel_factors()) + (imported() if include_imported else [])
    return [
        {
            "name": factor.name,
            "label": factor.label,
            "category": factor.category,
            "direction": factor.direction,
            "expected": {1: "越大越好", -1: "越小越好", 0: "无先验"}[factor.direction],
            "note": factor.note,
            "source": factor.source,
        }
        for factor in factors
    ]
