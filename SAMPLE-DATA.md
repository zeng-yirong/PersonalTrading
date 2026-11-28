# 示例数据（30 天）

这个分支（`data-sample`）只放一份 **63 MB 的示例面板**，用来换电脑时先把项目跑起来。
它不是全量面板，也不适合做回测 / 因子研究——那些需要十年数据。

## 里面有什么

| 项 | 值 |
|---|---|
| 区间 | 2026-10-28 → 2026-11-27（30 个自然日，约 22 个交易日） |
| 规模 | 41 张表 / 1,260,340 行 / 63.3 MB |
| 日频 | `daily`、`adj_factor`、`daily_basic`、`moneyflow`、`stk_limit`、`cyq_perf`、`stk_factor`、`margin_detail`、`fund_adj`、`sw_daily`、`index_daily`、热榜与龙虎榜等 |
| 维度 | `symbols`（7840）、`calendar`（到 2027-12-31）、`fund_basic`、`index_basic`、`stock_company` 等整表 |
| 选股 | `screen_snapshot` 已是最新交易日，选股页可直接用 |
| 财报 | 最近一个月公告的 `income_vip` / `balancesheet_vip` / `cashflow_vip` / `fina_indicator_vip` |
| 元信息 | `sample_meta` 记录源面板最新日、截断日与导出时间 |

## 怎么用

在已经 clone 下来的仓库里：

```powershell
git fetch origin data-sample
git checkout origin/data-sample -- data/market.duckdb
```

之后按 README 启动后端即可，`PERSONALTRADING_MARKET_DB_PATH` 不需要改（默认就是 `data/market.duckdb`）。
想保留自己的全量面板时，把示例文件放到别处并改 `.env` 指向它。

重新生成一份（可选）：

```powershell
python scripts/export_sample.py --days 30
```

## 限制

- **只有 30 天**：MA60、近 60 日涨幅、52 周回撤、20 日资金等长窗口指标会大面积为空，推演里会显示「数据缺失」而不是 0；
- **回测只有 1–2 期**：只能验证流程能跑通，不能用来判断策略好坏；
- **财报只保留最近一个月公告的**：多数股票的基本面为空；
- **不含 `stk_surv`（机构调研，无日期列、整表 40 万行）与 `dataset_progress`**：对开发演示无用；
- 要恢复完整面板：`python scripts/sync_market.py backfill --years 10`（需要 Tushare token）。

## 它是怎么生成的

`scripts/export_sample.py`：按 `trade_date >= 全库最新交易日 - N 天` 截取日频表，按 `ann_date` 截取公告类，维度表整表拷贝。
同一脚本可以导出任意窗口，例如 `--days 60`。
