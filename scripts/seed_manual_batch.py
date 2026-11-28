"""来源 A（人工假设）批量录入：50 条手写策略假设。

每条都写清三件事：**假设**（为什么现在有效）、**条件**（可执行的 ScreenQuery）、
**失效条件**（什么情况下停用）。没有失效条件的假设不是策略，只是一句感想。

    python scripts/seed_manual_batch.py                 # 只入库，不跑回测
    python scripts/seed_manual_batch.py --backtest      # 入库 + 跑近一年回测
    python scripts/seed_manual_batch.py --backtest --two-years 6   # 再给前 6 名补近两年

入库状态一律是 ``candidate``（未观察），`source='hand'`，
`provenance.origin='hand'`。回测过了分组门槛的会转 ``watch`` 并在 reason 里写明依据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db import init_db  # noqa: E402
from app.screener import query as screen_query  # noqa: E402
from app.strategy import horizon as horizon_mod  # noqa: E402
from app.strategy import store  # noqa: E402

# ---------------------------------------------------------------------------
# 50 条假设。filters 用 (字段, 运算符, 阈值)，sort 用 (字段, 是否降序)。
# 阈值全部落在面板字段的真实单位上（金额=元、资金=万元、比率=百分数）。
# ---------------------------------------------------------------------------

BATCH: list[dict[str, Any]] = [
    # ---------------- 长线 20 条：估值 / 质量 / 股息 / 低波 ----------------
    {
        "id": "a01-deep-value",
        "name": "深度价值",
        "horizon": "long",
        "hypothesis": "PE≤10 且 PB≤1.2 且 ROE≥10 的票，估值已计入悲观预期，长期持有赚均值回归。",
        "invalidation": "全市场 PE 中位数跌破 15（整体便宜，此条失去相对优势）或组合连续两季跑输沪深300 时停用。",
        "filters": [("pe_ttm", "<=", 10), ("pb", "<=", 1.2), ("roe", ">=", 10), ("amount", ">=", 50_000_000)],
        "sort": ("roe", True),
    },
    {
        "id": "a02-high-dividend-debt",
        "name": "高股息低负债",
        "horizon": "long",
        "hypothesis": "股息率≥5% 且负债率≤50% 的公司，分红是真金白银，负债低说明分红可持续。",
        "invalidation": "十年期国债收益率上行超过 3.5%（股息相对吸引力下降）时停用。",
        "filters": [("dv_ttm", ">=", 5), ("debt_ratio", "<=", 50), ("pe_ttm", "<=", 15)],
        "sort": ("dv_ttm", True),
    },
    {
        "id": "a03-cash-cow",
        "name": "现金奶牛",
        "horizon": "long",
        "hypothesis": "净利率≥15% 且 ROE≥12% 说明定价权和资本效率都在，PE≤20 时买入有安全垫。",
        "invalidation": "毛利率连续两个季度下滑超过 5 个百分点（定价权松动）时停用。",
        "filters": [("net_margin", ">=", 15), ("roe", ">=", 12), ("pe_ttm", "<=", 20), ("debt_ratio", "<=", 60)],
        "sort": ("roe", True),
    },
    {
        "id": "a04-below-book-dividend",
        "name": "破净分红",
        "horizon": "long",
        "hypothesis": "PB≤0.9 还能维持 3% 股息，说明账面资产真实且现金流转得动。",
        "invalidation": "破净股占比超过全市场 8%（系统性资产质量问题）时停用。",
        "filters": [("pb", "<=", 0.9), ("dv_ttm", ">=", 3), ("roe", ">=", 8)],
        "sort": ("dv_ttm", True),
    },
    {
        "id": "a05-cheap-growth",
        "name": "低估值成长",
        "horizon": "long",
        "hypothesis": "营收与利润双 20% 增长而 PE≤25，说明增长没被充分定价。",
        "invalidation": "组合的整体利润增速预期被下调（季报 miss 超过一半）时停用。",
        "filters": [("pe_ttm", "<=", 25), ("revenue_yoy", ">=", 15), ("profit_yoy", ">=", 20), ("roe", ">=", 10)],
        "sort": ("profit_yoy", True),
    },
    {
        "id": "a06-lowvol-dividend",
        "name": "低波红利",
        "horizon": "long",
        "hypothesis": "股息≥3% 叠加 20 日量能波动≤0.35，是波动最低的一档红利资产，适合长期底仓。",
        "invalidation": "红利指数相对沪深300 的 60 日超额超过 15%（拥挤）时停用。",
        "filters": [("dv_ttm", ">=", 3), ("a_vstd_20", "<=", 0.35), ("total_mv", ">=", 10_000_000_000)],
        "sort": ("dv_ttm", True),
    },
    {
        "id": "a07-large-bluechip",
        "name": "大盘蓝筹",
        "horizon": "long",
        "hypothesis": "市值≥1000 亿且 ROE≥12% 的公司流动性好、盈利稳，适合大资金长期持有。",
        "invalidation": "沪深300 成交额占全市场比例持续低于 30%（资金完全转向小盘）时停用。",
        "filters": [("total_mv", ">=", 100_000_000_000), ("roe", ">=", 12), ("debt_ratio", "<=", 70)],
        "sort": ("roe", True),
    },
    {
        "id": "a08-small-value",
        "name": "小盘价值",
        "horizon": "long",
        "hypothesis": "市值 10–500 亿、PE≤15、PB≤1.5 的小盘股，估值折价更极端，弹性也更大。",
        "invalidation": "小盘股（中证1000）相对沪深300 的 60 日超额超过 20%（风格过热的另一面）时停用。",
        "filters": [("total_mv", ">=", 1_000_000_000), ("total_mv", "<=", 50_000_000_000),
                   ("pe_ttm", "<=", 15), ("pb", "<=", 1.5)],
        "sort": ("pe_ttm", False),
    },
    {
        "id": "a09-high-quality",
        "name": "高毛利高质量",
        "horizon": "long",
        "hypothesis": "毛利率≥40%、ROE≥20%、负债率≤50% 是「好生意」的财务画像。",
        "invalidation": "组合 ROE 中位数跌破 12%（质量因子整体走弱）时停用。",
        "filters": [("roe", ">=", 20), ("gross_margin", ">=", 40), ("debt_ratio", "<=", 50)],
        "sort": ("roe", True),
    },
    {
        "id": "a10-steady-growth",
        "name": "稳健增长",
        "horizon": "long",
        "hypothesis": "营收与利润都保持 10% 以上、负债率≤60%、PE≤30，追求「不惊艳但不翻车」的复利。",
        "invalidation": "组合营收增速中位数转负时停用。",
        "filters": [("revenue_yoy", ">=", 10), ("profit_yoy", ">=", 10), ("debt_ratio", "<=", 60), ("pe_ttm", "<=", 30)],
        "sort": ("profit_yoy", True),
    },
    {
        "id": "a11-dividend-growth",
        "name": "股息增长",
        "horizon": "long",
        "hypothesis": "股息率≥4% 且利润仍在增长（≥5%），说明分红不是靠吃老本。",
        "invalidation": "组合利润增速中位数连续两季为负时停用。",
        "filters": [("dv_ttm", ">=", 4), ("profit_yoy", ">=", 5), ("pe_ttm", "<=", 18)],
        "sort": ("dv_ttm", True),
    },
    {
        "id": "a12-cheap-lowvol",
        "name": "低估低波",
        "horizon": "long",
        "hypothesis": "PE≤12 且 20 日量能波动≤0.4，便宜且没被资金炒作，回撤天然更小。",
        "invalidation": "组合年化波动超过 30% 时停用（不再符合「低波」定义）。",
        "filters": [("pe_ttm", "<=", 12), ("a_vstd_20", "<=", 0.4), ("amount", ">=", 30_000_000)],
        "sort": ("pe_ttm", False),
    },
    {
        "id": "a13-pe-percentile",
        "name": "三年 PE 低位",
        "horizon": "long",
        "hypothesis": "当前 PE 处于自身近三年 20% 分位以下，是「相对自己便宜」而不是「相对市场便宜」。",
        "invalidation": "组合的三年 PE 分位中位数回到 50% 以上时停用。",
        "filters": [("pe_pct_3y", "<=", 20), ("roe", ">=", 10), ("pe_ttm", ">=", 0.01)],
        "sort": ("pe_pct_3y", False),
    },
    {
        "id": "a14-below-book-repair",
        "name": "破净待修复",
        "horizon": "long",
        "hypothesis": "PB≤0.8 且 ROE≥5%、利润不下滑，资产折价迟早被修正。",
        "invalidation": "组合 ROE 中位数跌破 3% 时停用（资产在贬值而非折价）。",
        "filters": [("pb", "<=", 0.8), ("roe", ">=", 5), ("profit_yoy", ">=", 0)],
        "sort": ("pb", False),
    },
    {
        "id": "a15-large-lowvol",
        "name": "大盘低波",
        "horizon": "long",
        "hypothesis": "市值≥500 亿、量能波动≤0.3、股息≥2%，是「跌得少」的底仓组合。",
        "invalidation": "组合最大回撤超过 15%（低波假设被打破）时停用。",
        "filters": [("total_mv", ">=", 50_000_000_000), ("a_vstd_20", "<=", 0.3), ("dv_ttm", ">=", 2)],
        "sort": ("dv_ttm", True),
    },
    {
        "id": "a16-high-margin",
        "name": "高毛利高净利",
        "horizon": "long",
        "hypothesis": "毛利率≥45% 且净利率≥15%，中间费用吞不掉利润，规模效应真实。",
        "invalidation": "组合毛利率中位数下滑 5 个百分点以上时停用。",
        "filters": [("gross_margin", ">=", 45), ("net_margin", ">=", 15), ("roe", ">=", 15)],
        "sort": ("net_margin", True),
    },
    {
        "id": "a17-long-uptrend",
        "name": "长期上升趋势",
        "horizon": "long",
        "hypothesis": "60 日涨幅≥10% 且站上 MA60，趋势一旦形成会延续，配 PE≤40 过滤泡沫。",
        "invalidation": "沪深300 跌破 120 日均线（系统趋势转弱）时停用。",
        "filters": [("ret60", ">=", 10), ("gap_ma60", ">=", 5), ("pe_ttm", "<=", 40)],
        "sort": ("ret60", True),
    },
    {
        "id": "a18-low-turnover-blue",
        "name": "低换手蓝筹",
        "horizon": "long",
        "hypothesis": "换手率≤2% 说明筹码稳定，市值≥300 亿保证能进能出，配 ROE≥10。",
        "invalidation": "组合平均换手率升到 5% 以上（筹码开始松动）时停用。",
        "filters": [("turnover_rate", "<=", 2), ("total_mv", ">=", 30_000_000_000), ("roe", ">=", 10)],
        "sort": ("total_mv", True),
    },
    {
        "id": "a19-cashflow-steady",
        "name": "现金流稳健",
        "horizon": "long",
        "hypothesis": "净利率≥10%、负债率≤40%、ROE≥10% 且还有 2% 股息，抗周期能力最强。",
        "invalidation": "组合资产负债率中位数升破 55% 时停用。",
        "filters": [("net_margin", ">=", 10), ("debt_ratio", "<=", 40), ("roe", ">=", 10), ("dv_ttm", ">=", 2)],
        "sort": ("roe", True),
    },
    {
        "id": "a20-value-with-flow",
        "name": "价值+主力",
        "horizon": "long",
        "hypothesis": "PE≤18、ROE≥10% 且 20 日主力净流入为正，便宜且有资金在买。",
        "invalidation": "组合 20 日主力资金转为净流出超过 10 亿时停用。",
        "filters": [("pe_ttm", "<=", 18), ("mf_net_20d", ">=", 0), ("roe", ">=", 10)],
        "sort": ("pe_ttm", False),
    },

    # ---------------- 中线 20 条：业绩 / 资金 / 形态 ----------------
    {
        "id": "a21-surprise-flow",
        "name": "业绩预增+资金",
        "horizon": "mid",
        "hypothesis": "利润增速≥50% 且主力 20 日净流入≥5000 万，业绩与资金共振时中线上涨概率更高。",
        "invalidation": "组合的预告净利润增速中位数回落到 20% 以下时停用。",
        "filters": [("profit_yoy", ">=", 50), ("mf_net_20d", ">=", 5000), ("pe_ttm", "<=", 40)],
        "sort": ("profit_yoy", True),
    },
    {
        "id": "a22-pead",
        "name": "公告后漂移",
        "horizon": "mid",
        "hypothesis": "财报公告后相对沪深300 仍有≥5% 超额，说明市场反应不足，漂移会延续 2–4 周。",
        "invalidation": "全市场公告后漂移因子的中位数转负（因子失效）时停用。",
        "filters": [("fd_pead", ">=", 5), ("pe_ttm", "<=", 40), ("amount", ">=", 100_000_000)],
        "sort": ("fd_pead", True),
    },
    {
        "id": "a23-main-inflow",
        "name": "主力持续流入",
        "horizon": "mid",
        "hypothesis": "20 日主力净流入≥2 亿、换手≥2%，是持续性建仓而非一日游。",
        "invalidation": "组合 20 日资金净流入之和转负时停用。",
        "filters": [("mf_net_20d", ">=", 20000), ("turnover_rate", ">=", 2), ("pe_ttm", "<=", 50)],
        "sort": ("mf_net_20d", True),
    },
    {
        "id": "a24-margin-adding",
        "name": "融资加仓",
        "horizon": "mid",
        "hypothesis": "融资余额 20 日增长≥10% 说明杠杆资金在加仓，通常领先股价 2–4 周。",
        "invalidation": "全市场融资余额 20 日变化率转负（去杠杆）时停用。",
        "filters": [("margin_chg_20d", ">=", 10), ("mf_net_20d", ">=", 0), ("amount", ">=", 50_000_000)],
        "sort": ("margin_chg_20d", True),
    },
    {
        "id": "a25-north-flow",
        "name": "北向+资金",
        "horizon": "mid",
        "hypothesis": "90 日内≥3 次进入沪深股通十大且主力净流入，外资与内资同时看多。",
        "invalidation": "北向资金连续 10 个交易日净流出时停用。",
        "filters": [("north_90d", ">=", 3), ("mf_net_20d", ">=", 5000), ("total_mv", ">=", 10_000_000_000)],
        "sort": ("north_90d", True),
    },
    {
        "id": "a26-lhb-active",
        "name": "龙虎榜活跃",
        "horizon": "mid",
        "hypothesis": "90 日内≥2 次上龙虎榜、换手≥3%，说明有资金反复参与，波动中可获利。",
        "invalidation": "组合日均振幅超过 8%（波动失控）时停用。",
        "filters": [("lhb_90d", ">=", 2), ("turnover_rate", ">=", 3), ("pe_ttm", "<=", 60)],
        "sort": ("lhb_90d", True),
    },
    {
        "id": "a27-ma-bull",
        "name": "均线多头",
        "horizon": "mid",
        "hypothesis": "站上 MA20、MA60 且 20 日涨幅≥10%，多头排列下的回调往往被买回。",
        "invalidation": "沪深300 跌破 60 日均线时停用。",
        "filters": [("gap_ma20", ">=", 0), ("gap_ma60", ">=", 5), ("ret20", ">=", 10)],
        "sort": ("ret20", True),
    },
    {
        "id": "a28-macd-cross",
        "name": "MACD 金叉",
        "horizon": "mid",
        "hypothesis": "MACD 柱转正且 5 日涨幅≥2%，动能刚转多，2–3 周内延续概率较高。",
        "invalidation": "组合 MACD 柱中位数转负时停用。",
        "filters": [("macd_hist", ">=", 0.1), ("ret5", ">=", 2), ("amount", ">=", 50_000_000)],
        "sort": ("macd_hist", True),
    },
    {
        "id": "a29-boll-break",
        "name": "突破布林上轨",
        "horizon": "mid",
        "hypothesis": "收盘突破布林上轨且量比≥1.5，突破有效时中线仍有惯性。",
        "invalidation": "组合突破后 5 日内跌回中轨的比例超过一半时停用。",
        "filters": [("boll_pos", ">=", 0.95), ("volume_ratio", ">=", 1.5), ("amount", ">=", 100_000_000)],
        "sort": ("volume_ratio", True),
    },
    {
        "id": "a30-rsi-pullback",
        "name": "RSI 回踩",
        "horizon": "mid",
        "hypothesis": "RSI12≤35 且 5 日跌≥5%，短期超卖但 ROE≥5% 的票有基本面托底。",
        "invalidation": "组合 ROE 中位数跌破 3% 时停用。",
        "filters": [("rsi12", "<=", 35), ("ret5", "<=", -5), ("roe", ">=", 5)],
        "sort": ("rsi12", False),
    },
    {
        "id": "a31-oversold-repair",
        "name": "超卖修复",
        "horizon": "mid",
        "hypothesis": "回撤≥35% 且 RSI≤30，情绪宣泄充分，修复弹性大。",
        "invalidation": "组合 20 日平均成交额跌破 3000 万（流动性枯竭）时停用。",
        "filters": [("rsi12", "<=", 30), ("drawdown_52w", "<=", -35), ("amount", ">=", 50_000_000)],
        "sort": ("drawdown_52w", False),
    },
    {
        "id": "a32-shrink-pullback",
        "name": "缩量回调",
        "horizon": "mid",
        "hypothesis": "量比≤0.7 说明抛压枯竭，同时仍在 MA60 上方，属于强势整理。",
        "invalidation": "组合跌破 MA60 的比例超过一半时停用。",
        "filters": [("volume_ratio", "<=", 0.7), ("ret5", "<=", -3), ("gap_ma60", ">=", 0)],
        "sort": ("volume_ratio", False),
    },
    {
        "id": "a33-low-launch",
        "name": "低位启动",
        "horizon": "mid",
        "hypothesis": "距离 60 日低点≤5%（刚启动）且 5 日涨≥5%、量比≥1.5，是右侧第一波。",
        "invalidation": "组合启动后 10 日内跌破 60 日低点的比例超过三成时停用。",
        "filters": [("a_mingap_60", "<=", 5), ("ret5", ">=", 5), ("volume_ratio", ">=", 1.5)],
        "sort": ("ret5", True),
    },
    {
        "id": "a34-vol-price-diverge",
        "name": "量价背离",
        "horizon": "mid",
        "hypothesis": "价格涨但量价相关性转负（缩量上涨），说明抛压小，仍可持有。",
        "invalidation": "组合量价相关性中位数转正（放量滞涨）时停用。",
        "filters": [("a_corr_20", "<=", -0.2), ("ret20", ">=", 5), ("amount", ">=", 50_000_000)],
        "sort": ("a_corr_20", False),
    },
    {
        "id": "a35-small-upper-shadow",
        "name": "上影线小",
        "horizon": "mid",
        "hypothesis": "上影线占比≤2% 且 5 日涨≥3%，说明没有明显抛压，尾盘不砸。",
        "invalidation": "组合次日高开低走比例超过四成时停用。",
        "filters": [("a_kbar_kup", "<=", 2), ("ret5", ">=", 3), ("volume_ratio", ">=", 1.2)],
        "sort": ("ret5", True),
    },
    {
        "id": "a36-high-turnover-break",
        "name": "高换手突破",
        "horizon": "mid",
        "hypothesis": "换手≥5% 且 20 日涨≥15%，高换手配合上涨说明有新资金接力。",
        "invalidation": "组合换手中位数升到 12% 以上（过热）时停用。",
        "filters": [("turnover_rate", ">=", 5), ("ret20", ">=", 15), ("amount", ">=", 100_000_000)],
        "sort": ("ret20", True),
    },
    {
        "id": "a37-low-profit-chip",
        "name": "获利盘较低",
        "horizon": "mid",
        "hypothesis": "获利盘≤30% 但 20 日已涨≥10%，上方套牢盘少、抛压轻。",
        "invalidation": "组合获利盘中位数升到 70% 以上时停用。",
        "filters": [("chip_winner", "<=", 30), ("ret20", ">=", 10), ("amount", ">=", 50_000_000)],
        "sort": ("chip_winner", False),
    },
    {
        "id": "a38-hot-rank",
        "name": "热榜靠前",
        "horizon": "mid",
        "hypothesis": "热榜前 50 且换手≥3%，注意力就是短期资金，2 周内仍有惯性。",
        "invalidation": "热榜前 50 的组合 5 日平均收益转负（注意力失效）时停用。",
        "filters": [("hot_rank", "<=", 50), ("turnover_rate", ">=", 3), ("pe_ttm", "<=", 80)],
        "sort": ("hot_rank", False),
    },
    {
        "id": "a39-growth-momentum",
        "name": "业绩+动量",
        "horizon": "mid",
        "hypothesis": "利润增速≥30%、20 日涨≥10% 且站上 MA20，业绩与动量双击。",
        "invalidation": "组合利润增速中位数跌破 15% 时停用。",
        "filters": [("profit_yoy", ">=", 30), ("ret20", ">=", 10), ("gap_ma20", ">=", 0)],
        "sort": ("profit_yoy", True),
    },
    {
        "id": "a40-dividend-flow",
        "name": "分红+资金",
        "horizon": "mid",
        "hypothesis": "股息≥3%、主力净流入≥3000 万且 PE≤25，防御与资金兼顾。",
        "invalidation": "组合 20 日主力净流入之和转负时停用。",
        "filters": [("dv_ttm", ">=", 3), ("mf_net_20d", ">=", 3000), ("pe_ttm", "<=", 25)],
        "sort": ("dv_ttm", True),
    },

    # ---------------- 短线 10 条：1–5 日 ----------------
    {
        "id": "a41-first-limit-up",
        "name": "首板放量",
        "horizon": "short",
        "hypothesis": "5 日内首次涨停（90 日涨停≤2 次）且成交额≥1 亿，首板后的溢价概率高于连板。",
        "invalidation": "全市场涨停家数连续 3 日低于 30 家（情绪冰点）时停用。",
        "filters": [("limit_up_5d", ">=", 1), ("limit_up_90d", "<=", 2), ("amount", ">=", 100_000_000)],
        "sort": ("amount", True),
    },
    {
        "id": "a42-limit-up-pullback",
        "name": "涨停回踩",
        "horizon": "short",
        "hypothesis": "近 5 日涨停后回踩、换手≥5%，洗盘结束当日可能再起。",
        "invalidation": "组合次日平均收益转负时停用。",
        "filters": [("limit_up_5d", ">=", 1), ("turnover_rate", ">=", 5), ("pe_ttm", "<=", 60)],
        "sort": ("turnover_rate", True),
    },
    {
        "id": "a43-second-board-fade",
        "name": "连板后首阴",
        "horizon": "short",
        "hypothesis": "5 日内≥2 次涨停后首次收阴且换手≥5%，接力资金仍在，次日反抽概率大。",
        "invalidation": "组合次日开盘溢价中位数转负时停用。",
        "filters": [("limit_up_5d", ">=", 2), ("ret5", "<=", -3), ("turnover_rate", ">=", 5)],
        "sort": ("limit_up_5d", True),
    },
    {
        "id": "a44-price-volume-up",
        "name": "量价齐升",
        "horizon": "short",
        "hypothesis": "量比≥1.5、20 日涨≥10%、成交额≥5 亿，放量上涨是短期最强的确认信号。",
        "invalidation": "组合量比中位数跌破 1.0 时停用。",
        "filters": [("volume_ratio", ">=", 1.5), ("ret20", ">=", 10), ("amount", ">=", 500_000_000)],
        "sort": ("ret20", True),
    },
    {
        "id": "a45-volume-breakout",
        "name": "放量突破",
        "horizon": "short",
        "hypothesis": "5 日涨≥8%、量能波动≥0.3、成交额≥3 亿，波动放大通常伴随资金进场。",
        "invalidation": "组合 5 日平均收益转负时停用。",
        "filters": [("ret5", ">=", 8), ("a_vstd_20", ">=", 0.3), ("amount", ">=", 300_000_000)],
        "sort": ("ret5", True),
    },
    {
        "id": "a46-shrink-limit-up",
        "name": "缩量涨停",
        "horizon": "short",
        "hypothesis": "涨停但量比≤0.8，说明抛压极小，次日继续冲高概率大。",
        "invalidation": "组合次日跌幅中位数超过 3% 时停用。",
        "filters": [("limit_up_5d", ">=", 1), ("volume_ratio", "<=", 0.8), ("turnover_rate", ">=", 2)],
        "sort": ("volume_ratio", False),
    },
    {
        "id": "a47-sharp-drop-bounce",
        "name": "急跌反抽",
        "horizon": "short",
        "hypothesis": "单日跌≥6% 且 RSI≤25，情绪宣泄到极致，次日反抽概率高于继续下跌。",
        "invalidation": "组合次日平均收益转负时停用。",
        "filters": [("pct_chg", "<=", -6), ("rsi12", "<=", 25), ("amount", ">=", 50_000_000)],
        "sort": ("pct_chg", False),
    },
    {
        "id": "a48-close-buy",
        "name": "尾盘抢筹",
        "horizon": "short",
        "hypothesis": "当日主力净流入≥5000 万且涨≥3%，尾盘资金明显，次日高开概率大。",
        "invalidation": "组合次日高开低走比例超过一半时停用。",
        "filters": [("mf_net_1d", ">=", 5000), ("pct_chg", ">=", 3), ("amount", ">=", 50_000_000)],
        "sort": ("mf_net_1d", True),
    },
    {
        "id": "a49-big-order-buy",
        "name": "超大单买入",
        "horizon": "short",
        "hypothesis": "超大单净买入≥3000 万且当日涨≥2%，机构单是短线最可靠的跟风信号。",
        "invalidation": "组合次日平均收益转负时停用。",
        "filters": [("elg_net_1d", ">=", 3000), ("pct_chg", ">=", 2), ("amount", ">=", 50_000_000)],
        "sort": ("elg_net_1d", True),
    },
    {
        "id": "a50-low-position-volume",
        "name": "低位放量",
        "horizon": "short",
        "hypothesis": "处于 60 日 20 分位附近（≤5%）且量比≥2，低位放量往往是启动第一根。",
        "invalidation": "组合次日平均收益转负时停用。",
        "filters": [("a_qtl20_60", "<=", 5), ("volume_ratio", ">=", 2), ("amount", ">=", 50_000_000)],
        "sort": ("volume_ratio", True),
    },
]


# ---------------------------------------------------------------------------
# 技术指标批次（KDJ / BOLL / MACD / CCI / RSI / 均线组合）。
# 单日快照里没有"金叉"这种跨日事件，只能用"双线同处低位 + 当日转强"近似——
# 这一点必须写在假设里，否则回测出来的不是金叉。
# ---------------------------------------------------------------------------

TECH_BATCH: list[dict[str, Any]] = [
    {
        "id": "t01-kdj-oversold",
        "name": "KDJ 超卖",
        "horizon": "short",
        "hypothesis": "J 值≤0、K≤25 是短期极度超卖，反抽概率高于继续下跌（金叉需跨日，这里用低位近似）。",
        "invalidation": "组合次日平均收益转负，或 J 值分布长期贴地（指标钝化）时停用。",
        "filters": [("kdj_j", "<=", 0), ("kdj_k", "<=", 25), ("amount", ">=", 50_000_000)],
        "sort": ("kdj_j", False),
    },
    {
        "id": "t02-kdj-turn-up",
        "name": "KDJ 低位转强",
        "horizon": "mid",
        "hypothesis": "K、D 都在 35 以下但 5 日已涨≥2%，是低位刚拐头的形态，中线反弹更稳。",
        "invalidation": "组合 K 值中位数持续低于 20（没有拐头）时停用。",
        "filters": [("kdj_k", "<=", 35), ("kdj_d", "<=", 35), ("ret5", ">=", 2)],
        "sort": ("ret5", True),
    },
    {
        "id": "t03-kdj-strong",
        "name": "KDJ 强势钝化",
        "horizon": "mid",
        "hypothesis": "K、D 都在 80 以上的钝化说明趋势极强，强势股的回调通常浅。",
        "invalidation": "组合 K 值中位数跌破 50 时停用。",
        "filters": [("kdj_k", ">=", 80), ("kdj_d", ">=", 80), ("ret20", ">=", 10)],
        "sort": ("kdj_k", True),
    },
    {
        "id": "t04-boll-lower",
        "name": "BOLL 下轨",
        "horizon": "short",
        "hypothesis": "价格贴布林下轨（boll_pos≤0.15）叠加 RSI≤35，是均值回归的高胜率区。",
        "invalidation": "组合 5 日平均收益转负（下轨支撑失效）时停用。",
        "filters": [("boll_pos", "<=", 0.15), ("rsi12", "<=", 35), ("amount", ">=", 50_000_000)],
        "sort": ("boll_pos", False),
    },
    {
        "id": "t05-boll-breakout",
        "name": "BOLL 上轨突破",
        "horizon": "mid",
        "hypothesis": "突破上轨（boll_pos≥0.95）且量比≥1.5，开口向上时突破更容易延续。",
        "invalidation": "组合突破后 5 日跌回中轨的比例过半时停用。",
        "filters": [("boll_pos", ">=", 0.95), ("volume_ratio", ">=", 1.5), ("amount", ">=", 100_000_000)],
        "sort": ("volume_ratio", True),
    },
    {
        "id": "t06-boll-mid-pullback",
        "name": "BOLL 中轨回踩",
        "horizon": "mid",
        "hypothesis": "回落中轨附近（0.35–0.55）但仍在 MA20 上方、20 日仍涨 5%，属于强势整理。",
        "invalidation": "组合跌破 MA20 的比例过半时停用。",
        "filters": [("boll_pos", ">=", 0.35), ("boll_pos", "<=", 0.55), ("gap_ma20", ">=", 0), ("ret20", ">=", 5)],
        "sort": ("ret20", True),
    },
    {
        "id": "t07-boll-squeeze",
        "name": "布林收口",
        "horizon": "long",
        "hypothesis": "价格在中轨附近且量能波动≤0.3，是波动收敛形态，长线持有的回撤天然更小。",
        "invalidation": "组合年化波动升到 30% 以上时停用。",
        "filters": [("boll_pos", ">=", 0.4), ("boll_pos", "<=", 0.6), ("a_vstd_20", "<=", 0.3), ("amount", ">=", 50_000_000)],
        "sort": ("a_vstd_20", False),
    },
    {
        "id": "t08-macd-expand",
        "name": "MACD 柱放大",
        "horizon": "mid",
        "hypothesis": "MACD 柱≥0.3 且 5 日涨≥3%、量比≥1.2，动能与成交量同时扩张。",
        "invalidation": "组合 MACD 柱中位数转负时停用。",
        "filters": [("macd_hist", ">=", 0.3), ("ret5", ">=", 3), ("volume_ratio", ">=", 1.2)],
        "sort": ("macd_hist", True),
    },
    {
        "id": "t09-macd-flip",
        "name": "MACD 零轴翻红",
        "horizon": "mid",
        "hypothesis": "MACD 柱刚由负转正（0–0.1 之间）且 5 日不跌，是趋势切换的第一段。",
        "invalidation": "组合 10 日内 MACD 柱重新转负的比例过半时停用。",
        "filters": [("macd_hist", ">=", 0), ("macd_hist", "<=", 0.1), ("ret5", ">=", 0)],
        "sort": ("macd_hist", True),
    },
    {
        "id": "t10-cci-oversold",
        "name": "CCI 超卖",
        "horizon": "mid",
        "hypothesis": "CCI≤-100 且 5 日跌≥3%，但 ROE≥5%，属于情绪超卖而非基本面恶化。",
        "invalidation": "组合 ROE 中位数跌破 3% 时停用。",
        "filters": [("cci", "<=", -100), ("ret5", "<=", -3), ("roe", ">=", 5)],
        "sort": ("cci", False),
    },
    {
        "id": "t11-cci-breakout",
        "name": "CCI 突破",
        "horizon": "mid",
        "hypothesis": "CCI≥100 且量比≥1.5，突破 100 往往对应趋势启动。",
        "invalidation": "组合 5 日平均收益转负时停用。",
        "filters": [("cci", ">=", 100), ("volume_ratio", ">=", 1.5), ("amount", ">=", 100_000_000)],
        "sort": ("cci", True),
    },
    {
        "id": "t12-rsi-strong",
        "name": "RSI 强势区",
        "horizon": "mid",
        "hypothesis": "RSI12 在 55–70 之间且 20 日涨≥8%，是「强但没过热」的甜区。",
        "invalidation": "组合 RSI 中位数跌破 45 时停用。",
        "filters": [("rsi12", ">=", 55), ("rsi12", "<=", 70), ("ret20", ">=", 8)],
        "sort": ("ret20", True),
    },
    {
        "id": "t13-triple-resonance",
        "name": "三线共振",
        "horizon": "mid",
        "hypothesis": "站上 MA20、MA60 上方 5%、MACD 柱为正、RSI≥50——四个条件同时满足时趋势最确定。",
        "invalidation": "组合同时满足四项的比例跌破 20%（条件过严、样本太少）时停用。",
        "filters": [("gap_ma20", ">=", 0), ("gap_ma60", ">=", 5), ("macd_hist", ">=", 0), ("rsi12", ">=", 50)],
        "sort": ("gap_ma60", True),
    },
]

BATCHES = {"manual": BATCH, "tech": TECH_BATCH}


def build_query(item: dict[str, Any]) -> dict[str, Any]:
    field, desc = item["sort"]
    return {
        "title": item["name"],
        "filters": [
            {"field": name, "op": op, "value": value} for name, op, value in item["filters"]
        ],
        "sort": [{"field": field, "desc": desc}],
    }


def validate(items: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for item in items:
        if item["id"] in seen:
            raise SystemExit(f"id 重复：{item['id']}")
        seen.add(item["id"])
        if not item.get("invalidation"):
            raise SystemExit(f"{item['id']} 没有失效条件")
        query = screen_query.ScreenQuery.from_dict(build_query(item))
        try:
            query.where()
            query.order_by()
        except screen_query.QueryError as exc:
            raise SystemExit(f"{item['id']} 条件不合法：{exc}") from exc


def insert(items: list[dict[str, Any]]) -> int:
    written = 0
    for item in items:
        if store.get(item["id"]) is not None:
            continue
        store.create(
            item["name"],
            build_query(item),
            source="hand",
            status="candidate",
            horizon=item["horizon"],
            notes=item["hypothesis"],
            invalidation=item["invalidation"],
            provenance={
                "origin": "hand",
                "rationale": item["hypothesis"],
                "invalidation": item["invalidation"],
                "verified": False,
                "batch": "manual-2026-11-28",
            },
            strategy_id=item["id"],
        )
        written += 1
    return written


def backtest(items: list[dict[str, Any]], two_years: int = 0) -> list[dict[str, Any]]:
    from app.backtest import service as backtest_service
    from app.screener import ranking

    results: list[dict[str, Any]] = []
    end = ranking._panel_end()
    for index, item in enumerate(items, start=1):
        params = horizon_mod.params_for(item["horizon"])
        payload = {
            "query": build_query(item),
            "rebalance": params["rebalance"],
            "top_n": params["top_n"],
            "title": item["name"],
        }
        if end:
            payload["end"] = end.isoformat()
        try:
            result = backtest_service.run(payload, persist=False)
        except Exception as exc:  # noqa: BLE001 - 一条失败不打断整批
            results.append({"id": item["id"], "status": "failed", "error": str(exc)})
            print(f"[{index}/{len(items)}] {item['name']} 失败：{exc}")
            continue
        metrics = ranking._metrics_of(result)
        gate = horizon_mod.gate_check(item["horizon"], metrics)
        spec = result.get("spec") or {}
        store.save_run(
            item["id"], "1y", {**metrics, "horizon": item["horizon"], "params": params, "gate": gate},
            start=spec.get("start") or "", end=spec.get("end") or "",
        )
        results.append(
            {"id": item["id"], "name": item["name"], "horizon": item["horizon"],
             "metrics": metrics, "gate": gate}
        )
        print(
            f"[{index}/{len(items)}] {item['name']:<10} 超额 {metrics['excess_cagr_pct']:>8.2f}% "
            f"回撤 {metrics['max_drawdown_pct']:>7.1f}% 胜率 {metrics['hit_rate_pct']:>5.1f}% "
            f"{'PASS' if gate['passed'] else 'FAIL'}"
        )

    if two_years:
        from datetime import timedelta

        ranked = [row for row in results if row.get("status") != "failed"]
        ranked.sort(key=lambda row: -(row["metrics"].get("excess_cagr_pct") or -999))
        for row in ranked[:two_years]:
            item = next(entry for entry in items if entry["id"] == row["id"])
            params = horizon_mod.params_for(item["horizon"])
            start = (end - timedelta(days=730)).isoformat() if end else None
            payload = {
                "query": build_query(item),
                "rebalance": params["rebalance"],
                "top_n": params["top_n"],
                "title": item["name"],
            }
            if end:
                payload["end"] = end.isoformat()
            if start:
                payload["start"] = start
            try:
                long_result = backtest_service.run(payload, persist=False)
            except Exception as exc:  # noqa: BLE001
                print(f"  两年窗口失败 {item['name']}：{exc}")
                continue
            store.save_run(item["id"], "2y", ranking._metrics_of(long_result))
            print(f"  + 两年窗口 {item['name']}：超额 {ranking._metrics_of(long_result)['excess_cagr_pct']:.2f}%")
    return results


def promote(results: list[dict[str, Any]]) -> int:
    """过了分组门槛的 → watch（观察 8 周），理由写清楚。"""
    promoted = 0
    for row in results:
        if row.get("status") == "failed" or not row["gate"].get("passed"):
            continue
        try:
            store.set_status(row["id"], "watch", "首轮回测过门槛：" + "；".join(
                check["label"] for check in row["gate"]["checks"]
            ))
            promoted += 1
        except store.StrategyError as exc:
            print(f"  晋级失败 {row['id']}：{exc}")
    return promoted


def recheck() -> int:
    """不重跑回测，只按当前门槛重算已有成绩（门槛调整后用）。"""
    from app.db import connection

    changed = 0
    with connection() as conn:
        rows = conn.execute(
            "SELECT s.id, s.name, s.horizon, s.status, r.id AS run_id, r.metrics_json "
            "FROM strategies s JOIN strategy_runs r ON r.strategy_id = s.id "
            "WHERE r.window = '1y' AND (s.id LIKE 'a%' OR s.id LIKE 't%') ORDER BY r.id DESC"
        ).fetchall()
    seen: set[str] = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        metrics = json.loads(row["metrics_json"] or "{}")
        if not metrics.get("periods"):
            continue
        gate = horizon_mod.gate_check(row["horizon"], metrics)
        metrics["gate"] = gate
        with connection() as conn:
            conn.execute(
                "UPDATE strategy_runs SET metrics_json = ? WHERE id = ?",
                (json.dumps(metrics, ensure_ascii=False), row["run_id"]),
            )
        if gate["passed"] and row["status"] == "candidate":
            store.set_status(row["id"], "watch", "门槛复核通过：" + "；".join(
                check["label"] for check in gate["checks"]
            ))
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="来源 A：人工假设批量入库")
    parser.add_argument("--backtest", action="store_true", help="入库后跑近一年回测")
    parser.add_argument("--two-years", type=int, default=0, help="给前 N 名补两年窗口")
    parser.add_argument("--promote", action="store_true", help="过门槛的转 watch")
    parser.add_argument("--recheck", action="store_true", help="不重跑回测，只按当前门槛重算")
    parser.add_argument("--batch", default="manual", choices=sorted(BATCHES), help="manual（A 批次）或 tech（技术指标批次）")
    args = parser.parse_args(argv)

    init_db()
    if args.recheck:
        print(f"复核后转观察：{recheck()} 条")
        return 0
    items = BATCHES[args.batch]
    validate(items)
    written = insert(items)
    print(f"入库 {written} 条（已存在跳过 {len(items) - written} 条）")

    if args.backtest:
        results = backtest(items, two_years=args.two_years)
        if args.promote:
            promoted = promote(results)
            print(f"过门槛转观察：{promoted} 条")
        passed = [row for row in results if row.get("gate", {}).get("passed")]
        print(f"共 {len(results)} 条回测，{len(passed)} 条过门槛")
        print(json.dumps(
            [
                {"id": row["id"], "name": row.get("name"), "horizon": row.get("horizon"),
                 "excess": (row.get("metrics") or {}).get("excess_cagr_pct")}
                for row in sorted(
                    results,
                    key=lambda r: -((r.get("metrics") or {}).get("excess_cagr_pct") or -999),
                )
            ],
            ensure_ascii=False,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
