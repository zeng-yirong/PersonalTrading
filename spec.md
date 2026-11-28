# 实现方案与验收标准

> 目标本身写在 [goat.md](goat.md)——本文件承载**功能设计、实现方案与验收**。
> 编号与 goat.md 的「目标分层」一一对应（0.x / 1.x / 2.x / 3.x），第 4 节对应「质量属性」。
> 验收命令默认在 `PersonalTrading/` 目录执行，后端跑在 `127.0.0.1:8010`；涉及模型的用例标了 token 预算，跑之前先确认 `.env` 里的 key。

## 通用约定

- **能跑才算验收**：每条验收都是一个命令、一个端点或一条 pytest 用例，不接受"看起来对"。
- **口径先于结论**：改复权、as-of、单位、百分比，必须同时改测试与 README 的口径段，并在验收里给出改前 / 改后数字。
- **缺数据显式**：新增数据依赖时，验收必须包含"上游为空时报告里怎么写"。
- **可复现**：涉及排序或并列值的查询，末尾固定 `symbol ASC`；同一输入连跑三次结果一致。
- **门槛预注册**：因子 / 策略 / 技能的晋级门槛写成常量，改动单独提交并写明原因。

## 目标 → 实现 → 验收 索引

| 目标 | 主要实现入口 | 验收入口 |
|---|---|---|
| 0.1 全市场面板 | `backend/app/data/store.py`、`sync.py`、`backfill.py`、`datasets.py` | `scripts/sync_market.py status` |
| 0.2 日更可靠性 | `sync_market.py day`、`ops/health.py`、`daily_chain.py` | `scripts/ops.py health` |
| 0.3 数据质量护栏 | `topic/context.py`、`agents/context.py`、`data_gaps` | 主题报告 + 快照对账 |
| 0.4 数据可迁移 | `data/backup.py`、`scripts/backup_panel.py` | `backup_panel.py --dry-run` / 实跑 |
| 0.5 数据版本 | `data/fingerprint.py`、`/api/data/fingerprint` | 指纹两次对比 |
| 0.6 搜索与自选 | `services/symbols.py`、`routers/watchlist.py` | `/api/search`、`/api/watchlist` |
| 1.1 单股研究 | `services/market.py`、`indicators.py`、`fundamentals.py`、`detail.py` | `/api/stocks/{symbol}/detail` |
| 1.2 多智能体推演 | `agents/graph.py`、`nodes.py`、`prompts.py`、`runner.py`、`guards.py` | `scripts/research.py` |
| 1.3 主题研究 | `topic/context.py`、`prompts.py`、`service.py` | `scripts/topic.py` |
| 2.1 选股器 | `screener/fields.py`、`nl.py`、`snapshot.py` | `scripts/screen.py` |
| 2.2 回测 | `backtest/spec.py`、`features.py`、`engine.py`、`service.py` | `scripts/backtest.py` |
| 2.3 模拟盘 + 复盘 | `paper/service.py`、`paper/review.py`、`fees.py` | `scripts/paper.py` |
| 2.4 统一决策账本 | `ops/ledger.py`、`routers/ops.py` | `scripts/ops.py ledger` / `chain` |
| 2.5 置信度校准 | `agents/runner.py`、`db.py`、`ops/ledger.py`、`guards.py` | `/api/ledger/calibration` |
| 2.6 今日推荐 | 待建：`picks/service.py`、`picks/store.py`、`routers/picks.py` | `/api/picks` |
| 3.0 技能加载 | `app/skills/`、`agents/runner.py`、`agents/commentary.py` | `pytest`、`commentary` 接口 |
| 3.0.1 技能进化 | 待建：`scripts/replay.py`、`skill_runs` 表 | 回放 + 配对 bootstrap |
| 3.1 记忆检索 | `agents/memory.py`、`memory_index` / `memory_fts` | `scripts/memory_eval.py` |
| 3.2 因子挖掘 | `factor/library.py`、`promoted.py`、`evaluate.py`、`service.py` | `scripts/factor.py` |
| 3.2.1 外部因子导入 | `factor/alpha158.py`、`academic.py`、`gtja.py`、`fundamental.py` | `factor.py --source ... --incremental` |
| 3.3 策略自进化 | `strategy/store.py`、待建 `strategy/generator.py` | `/api/strategies` |
| 3.4 策略周期分类 | `strategy/horizon.py`、`backtest/engine.py`（新指标） | `python scripts/preset_backtest.py` |
| 3.5 策略来源 A–E | 待建 `strategy/generator.py`、`strategies.invalidation` | `python scripts/generate.py --source ...` |

## 0. 数据资产（L0）

### 0.1 全市场面板

**实现**

- `data/store.py` 用 DuckDB 建 `symbols` / `calendar` / `daily` / `daily_basic` / `sync_log`；`daily` 存**未复权** OHLCV + 累计 `adj_factor`，后复权 `close × adj_factor`、前复权再除以最新因子，两种口径在查询层还原。
- `data/datasets.py` 是数据集注册表：接口 → 表、模式（按日 / 按代码 / 按报告期）、单位换算（Tushare 手 → 股、千元 → 元）、`daily=True` 是否进日更、`retry_days` 回补天数。
- `data/sync.py` 编排日历 / 列表 / 快照 / 历史；`data/backfill.py` 负责断点续传（`dataset_progress`）、幂等 upsert、全局限速（默认 180 次/分钟，40203 自动退避）。
- `data/providers/` 按能力选源：Tushare → 新浪 → 东财，token 无效时进程内跳过。

**验收**

- `python scripts/sync_market.py status`：标的 ≥ 5000、日线 ≥ 1300 万、最早日期 ≤ 2016-12-01、最新日期 = 最近交易日，`token_status` 可见。
- 抽 10 只票核对：`close × adj_factor` 与东财 / 同花顺后复权收盘误差 ≤ 0.01 元。
- 单位抽 3 行：`daily.volume` 是股、`amount` 是元，与上游原始值按 ×100 / ×1000 对得上。
- 单日全市场截面查询（约 6500 行）p95 ≤ 300 ms（见第 4 节）。

### 0.2 日更可靠性

**实现**

- 工作日 18:00 跑 `sync_market.py day`，20:30 再跑一次补晚发布数据；`retry_days` 让当日为空的按日数据集自动回补前 1–2 个交易日。
- 交易日历驱动，非交易日直接跳过；日历超过 15 天未更新则拒绝写快照；15:05 前拒绝写日线（除非显式 `--allow-intraday`）。
- `ops/health.py` 覆盖 17 张关键表，输出最新日期、滞后交易日、残缺日。
- 失败可见：任务退出码非 0、`sync_log` 记 failed、体检标红；18:20 三段式在生成复盘前检查面板是否写完。

**验收**

- 连续 5 个交易日：每天 18:00 后 `daily` 最新日期 = 当日，`python scripts/ops.py health` 没有未解释的滞后 / 残缺。
- 非交易日跑 `day` 不写数据、退出码 0。
- 造一次上游失败（错误 token）：退出码非 0，`sync_log` 有 failed 行，health 能看见。
- `margin_detail` 当日为空 → 自动回补前一交易日，不出现"永远差一天"。

### 0.3 数据质量护栏

**实现**

- 快照层做完整性判断：两融按当日标的数对比近期中位数，不完整就回退到上一个完整日并在快照里注明。
- 报告 schema 固定 `data_gaps`，prompt 明令"缺失写缺失"；`topic/service.py` 与 `agents/runner.py` 把它渲染成「数据缺失」而不是 0。
- 北向、申万、涨跌停等滞后块各自带 `as_of`，前端早于面板日打「滞后」标。

**验收**

- 人为把某天 `margin_detail` 删到一半，生成主题报告：`margin` 块跳过该日并写明回退日期，`data_gaps` 出现对应条目。
- `hsgt_top10.net_amount` 近 20 日全空 → `north` 块不出现，`data_gaps` 写明「无法给出北向净流入」。
- 报告里的数字与快照 JSON 逐项对账，0 处不一致（一次性对账脚本）。

### 0.4 数据可迁移

**实现**

- `scripts/backup_panel.py` 把 `market.duckdb`（连同 `.wal`）与 `personaltrading.db` 拷到另一块盘，默认保留最近 5 份，只清理自己命名的 `YYYY-MM-DD_HHMMSS` 目录。
- 拷完回读：表数、`daily` 行数、最新交易日与源一致；SQLite 副本过 `integrity_check`。

**验收**

- `python scripts/backup_panel.py --dry-run` 打印源规模与目标目录，不写文件。
- `python scripts/backup_panel.py --out D:\bk`：结束输出三项校验结果且全部一致（5 GB 量级约 1 分钟）。
- 把副本删掉一行后重新比对 → 校验必须报差异（证明校验真的在比，不是只看"文件存在"）。
- 恢复演练（待做）：停后端 → 放回两个文件 → `sync_market.py status` 与 `ops.py health` 与备份时一致。

### 0.5 数据版本

**实现**

- `data/fingerprint.py` 取 `daily` 最大日期 + 行数 + 各表最新日期，算 12 位 digest；`GET /api/data/fingerprint` 返回。
- 报告头写入 digest（待做）：`runner.build_report` 的 front matter 增加面板指纹，`agent_runs` 加 `data_fingerprint` 列（`init_db()` 用 `PRAGMA table_info` 补列）。

**验收**

- `curl /api/data/fingerprint` 两次一致；手工插入一行测试数据后 digest 变化，删除后恢复。
- 报告头出现面板 digest，与 `agent_runs.data_fingerprint` 一致。
- 同一份面板重跑，报告引用的数字完全一致。

### 0.6 搜索与自选

**实现**

- `services/symbols.py` 统一代码 / 名称 / 拼音匹配（含 ETF 与交易所推断）；`/api/search` 返回带类型的候选。
- 自选落 SQLite `watchlist`；前端右键菜单增删，列表与详情状态同步。
- `watchlist` 增 `added_trade_date` / `added_close` / `added_adj` / `added_source`（`init_db` 用 `PRAGMA` 补列）；加入自选时冻结当时的基准价：实时价优先，取不到用面板收盘价。
- `GET /api/watchlist/metrics`：批量 Sina 行情（一次请求、3 秒超时）+ `daily` 最近 8 根，返回 `price` / `pct_chg` / `pct_5d` / `pct_since_add`；行情取不到时退回面板复权收盘，不返回 500。
- 口径：今日优先用行情的 `pct_chg`（交易所 `pre_close` 已除权），否则用面板复权收盘算；近 5 日与添加后都用 `close × adj_factor`；旧自选在第一次读指标时按 `created_at` 回填一次基准。

**验收**

- 搜 `000001`、`平安银行`、`payh` 命中同一标的；ETF `510050` 可搜可加。
- 加自选后重启后端仍在；删除后列表与详情状态同步。
- 不存在的代码返回空列表，不返回 500。
- 6 根日线 10 → 12.5、添加价 10.5：近 5 日 +25.00%、添加后 +19.05%、今日 +4.17%（单测断言）。
- 行情比面板新一天时，近 5 日窗口向前移一根，价格显示行情价；10 送 10（收盘 10→5、因子 1→2）时三个涨跌幅都是 0，不出现 -50%。
- `curl /api/watchlist/metrics` 在面板就绪时 ≤ 0.5 s；行情不可用时 `quote_source=panel` 且字段可读。

## 1. 研究（L1）

### 1.1 单股研究

**实现**

- `services/market.py` 实时行情与 K 线（新浪优先、东财兜底，5 s / 300 s 缓存）；`services/indicators.py` 算 MA / MACD / RSI / BOLL；`services/fundamentals.py` 财务快照；`services/detail.py` 聚合详情。
- `routers/stocks.py` 暴露 `/quote`、`/chart`、`/fundamentals`、`/detail`、`/intraday`；前端 ECharts 画日 / 周 / 月线与成交量。
- 分时走东财 `trends2`、新浪兜底，结果进 SQLite `intraday_cache`（盘中 60 s、收盘后 12 小时），不写市场面板。

**验收**

- `GET /api/stocks/600519/detail` 在面板已同步时 ≤ 3 s，返回报价 + 120 日 K + 因子 + 财务 + 分时 + 近期推演。
- `GET /api/stocks/600519/chart?period=day&limit=250` 返回 250 根，MA5/10/20/60 前 59 根允许为空、之后无空洞。
- 行情接口失败时页面显示占位（「暂无数据」）而不是白屏或 500。

### 1.2 多智能体推演

**实现**

- LangGraph 编排（`agents/graph.py`）：7 位分析师并行 → 多空 N 轮 → 研究主管 → 交易员 → 三方风控并行 → 风控负责人；每个节点在 `nodes.py` 解析固定 JSON，缺字段追加一轮纠正。
- `agents/llm.py` 是 OpenAI 兼容 DeepSeek 客户端（JSON 模式 + usage 统计）；各角色 completion 预算 1600–3400，推理内容计入 `max_tokens`。
- `agents/runner.py` 流式 yield `stage` / `run_start` / `node_start` / `node_end` / `run_end`；`build_plan` 用收盘价算期望收益、下行与赔率（百分比不由模型算）；`guards.py` 做决策体检；`agents/store.py` 落 `agent_runs` + `agent_messages`。
- 置信度：分析师 `confidence`、多空 `conviction`、研究主管 `confidence`、交易员 `conviction`、风控负责人 `confidence`，统一口径为 0–100 的**把握程度**（不是涨跌概率）；交易员仓位必须与信心自洽（`equity-desk` 规则）。

**验收**

- `python scripts/research.py 600519 --rounds 2`：16–18 次调用、≤ 7 万 tokens、2.5 分钟内完成，报告含结论 / 分析师 / 辩论 / 交易员 / 目标与赔率 / 风控 / 决策体检。
- 报告「结论」的研究主管置信度、「交易员草案」的信心，与 `agent_messages.payload_json` 一致。
- mock 一个角色抛错：记入 `errors`、报告标「运行异常」，整轮仍产出裁定。
- 目标价低于现价、止损高于现价、仓位越界、否决却给仓位 → 出现在「决策体检」；20 条陷阱回归全绿。

### 1.3 主题研究

**实现**

- `topic/context.py` 把面板打成快照：`indices` / `breadth` / `turnover` / `limits` / `margin` / `north` / `hot_concepts` / `industries` / `industry_detail` / `sw_l1`，每块带自己的 `as_of`。
- `topic/prompts.py` 固定 schema（标题 / 范围 / 裁定 / 信心 / 摘要 / 关键判断 / 驱动 / 风险 / 观察信号 / 数据缺口 / 三段展开）；`topic/service.py` 校验、渲染 markdown、落 `topic_reports`。
- 每日复盘（`topic/daily.py`）复用同一套快照，18:20 由三段式自动生成，当天缓存。

**验收**

- `python scripts/topic.py "白酒行业还能拿吗" --context` 只出快照不调模型，≤ 1 s。
- 生成一次 30–90 s、约 1.2 万 tokens；抽查 20 条判断，每条都能在快照里找到对应数字。
- 缺字段触发一次纠正；两次仍缺 → 400，不落库空壳报告。
- 观察信号带阈值与方向（可次日判定），不出现「关注后续走势」。

## 2. 决策与验证（L2）

### 2.1 选股器

**实现**

- `screener/fields.py` 维护 53 字段白名单（含 8 个晋级因子）；`screener/nl.py` 调模型把自然语言翻成 `{filters, sort}`，字段名必须在白名单、运算符限定集合。
- 值一律参数绑定，只有白名单字段名拼进 SQL；`screener/snapshot.py` 把每股一行物化成 `screen_snapshot`（约 6500 行，首次约 0.9 s，同一交易日复用）。
- 辅助数据按各自最近可用日期取，`GET /api/screener/meta` 返回每个数据块的日期。

**验收**

- `python scripts/screen.py --fields` 列出 53 个字段；未知字段返回 400 并附可用字段。
- `python scripts/screen.py "市盈率低于30、ROE大于15%、20日主力净流入为正"` 返回结果 ≤ 2 s。
- `POST /api/screener/run` 传入非白名单字段 → 400，不执行任何 SQL。
- 解析失败给一轮纠正；两次失败 → 400，不跑"猜的"查询。

### 2.2 回测

**实现**

- `backtest/spec.py` 校验参数与默认值；`backtest/features.py` 一次物化所有调仓日截面（MA、涨幅、52 周高点、按 `ann_date` 关联财报、按当日关联资金 / 筹码 / 热度）。
- `backtest/engine.py`：信号在调仓日收盘后算、次日**开盘价**成交、涨停买不到 / 跌停卖不出 / 停牌跳过或顺延、费用走 `app/fees.py`、收益用复权口径、每期等权最多 `top_n` 只。
- `backtest/service.py` 算指标（年化 / 超额 / 回撤 / 胜率 / 仓位 / 空仓期）并落 `backtest_runs`；`pe_pct_3y` 显式拒绝。

**验收**

- `python scripts/backtest.py "ROE大于15%、市盈率低于30"` 默认近一年、20 日调仓；5 年 61 期约 7 s。
- 同一条件连跑三次，收益完全一致（`ORDER BY` 末尾固定 `symbol ASC`）。
- 构造"次日一字涨停"样本 → 买入被跳过；"次日跌停" → 卖出顺延；停牌 → 跳过 / 顺延。
- look-ahead、日期边界、复权口径的既有用例全绿。

### 2.3 模拟盘 + 复盘

**实现**

- `paper/service.py` 虚拟账户：按面板收盘价成交、100 股一手、费用走 `app/fees.py`（佣金万 2.5 最低 5 元 + 印花税 0.05% + 过户费 0.001%）；净值由交易重放，停牌沿用上一收盘。
- `paper/review.py`：每条推演冻结 as-of 日收盘价为入场价，算 T+5 / T+20 收益与相对沪深300 超额；看多需上涨、空仓 / 否决需下跌，|收益| < 1% 记中性；`prediction` 与 `trigger` 分开统计。

**验收**

- `python scripts/paper.py buy 600519 --amount 200000` 取整到 100 股，现金与手续费可手算复现。
- `python scripts/paper.py nav` 与逐笔重放结果一致；连跑两次同一条曲线。
- `python scripts/paper.py from-run 3` 按风控**最终裁定**建仓；否决 / 0% 不建仓。
- `python scripts/paper.py review` 输出 T+5 / T+20 判定与命中率；|T+20| < 1% 不计入正误。

### 2.4 统一决策账本

**实现**

- `ops/ledger.py` 把 `agent_runs` / `topic_reports` / `backtest_runs` / `paper_trades` 归一成一行（类型 / 时间 / 标题 / 数据日 / 结论 / 仓位或分数 / 复盘 / tokens），只读不造第五份真相。
- `source_run_id` 从回测 / 主题 / 成交挂回推演；`chain` 展开「推演 → 复盘 → 回测 → 主题 → 成交」；`init_db()` 用 `PRAGMA` 补列，老库免迁移。

**验收**

- `python scripts/ops.py ledger --limit 20` 四类记录都能出现且字段齐全。
- `python scripts/ops.py chain 10` 展开完整链路。
- 带 `--source-run 10` 的回测 / 主题，在 `chain 10` 里能查到。
- `GET /api/ledger/summary` 的计数与各表 `COUNT(*)` 一致。

### 2.5 置信度校准

**实现**

- **输出**：交易员 `conviction` 与风控负责人 `confidence` 已在 schema 里；报告「结论」增加**最终置信度**，来源优先级为 风控负责人 `confidence` → 交易员 `conviction` → 「未给出」（不写 0）。
- **落库**：`agent_runs` 加 `confidence INTEGER` 与 `confidence_source TEXT`（`PRAGMA table_info` 补列）；`agent_reviews` 加 `confidence`（裁定置信度），保留 `conviction`（交易员信心）。
- **校准**：`ops/ledger.py` 新增 `calibration(days)`：按 10 分档统计 n / 命中率 / 平均 T+20 超额 / Brier。事件 `y`：看多（批准或调整后批准且仓位 > 0）记 1，否决 / 0% 记 0；`p = confidence / 100`；`Brier = mean((p − y)²)`，基线为全样本命中率的常数预测。样本 < 30 条只展示、不写结论。
- **体检**：`guards.check_proposal` 增「信心 < 50 却给 > 20% 仓位」（与 `equity-desk` 一致），命中写进决策体检。
- **展示**：报告头、`GET /api/ledger/calibration?days=365`、前端运维面板的分档表（只展示，不改判定）。

**验收**

- `python scripts/research.py 600519` 报告头出现「最终置信度 xx/100（来源：风控负责人）」；模型未给时显示「未给出」。
- `agent_runs.confidence`、`agent_reviews.confidence` 与报告一致；旧数据回填为 NULL，不是 0。
- fixture 造 12 条已判定样本 → `GET /api/ledger/calibration` 返回 ≥ 3 档，各档 n 之和 = 12，Brier 与基线可比较。
- 新增 pytest：来源降级、Brier 手算、信心-仓位体检各 1 条。

### 2.6 今日推荐

**实现（待建）**

- **只推荐 champion**：读 `strategies` 表 `status='champion'`，用最近一批 `strategy_runs` 的 1 年 `excess_cagr_pct` 排序，要求 2 年窗口同为正；最多取前 3 个。candidate / watch 一律不进，**没有合格策略就返回空清单 + 原因，不凑数**。
- **选股**：用策略自己的 `query_json` 跑 `screener/snapshot.py` 的当日快照；按「策略权重（第 1/2/3 名 = 1.0/0.8/0.6）× 策略内排序（有 `sort` 用 `sort`，否则按成交额）」合并去重，最多 10 只；同一策略 ≤4 只、同一行业 ≤3 只。
- **过滤**：停牌（当日无 K）、ST / *ST、上市 <60 个交易日、20 日均额 <5000 万、次日大概率买不到（今日涨停且换手异常）——最后一种不直接删，标 `chased` 留给复盘区分。
- **每只输出**：`strategy_id` / `strategy_name`、命中条件、参考入场区间（当日收盘 ±1.5%，次日高开 >3% 标「不追」）、参考仓位（`min(10%, 100 / top_n)`）、失效条件（跌破 MA20 或策略条件失效）、风险点、`as_of`；整份清单标「研究参考，不是买入指令」。
- **时点与前置检查**：工作日 18:20 作为 `daily_chain.py` 第 4 步（复盘刷新 → 今日复盘 → 自动推演 → 今日推荐）；`daily` 最新日必须 = 当日，否则拒绝生成并写明原因；非交易日不生成，页面展示最近一份并标 `for_date`。
- **落库**：`daily_picks`（`trade_date`, `for_date`, `strategy_id`, `symbol`, `rank`, `score`, `entry_low`, `entry_high`, `max_position_pct`, `reasons_json`, `invalidation`, `chased`, `created_at`）；`pick_reviews`（`pick_id`, `entry_date`, `entry_price`, `fillable`, `t1_return`, `t1_excess`, `t5_return`, `t5_excess`, `outcome`）。
- **复盘**：次日**开盘价**入场；一字涨停 / 停牌 / 高开 >3% 记 `fillable=0`，不进命中率；T+1 与 T+5 用复权收盘算相对沪深300 超额；主判定 T+5 超额 >1% 为正确、<-1% 为错误、其余中性。
- **API / UI**：`GET /api/picks?date=`、`POST /api/picks/generate`、`GET /api/picks/review`；导航新增「今日推荐」，展示清单、来源策略与次日复盘；生成 0 次模型调用（可选点评另算，≤2k tokens 且可关）。
- **复现**：评分与排序末尾固定 `symbol ASC`；同一份面板 + 同一批 champion 重跑，清单、顺序、仓位完全一致。

**验收**

- `POST /api/picks/generate` 在面板就绪时 ≤2 s，返回 0–10 只；非空时每个 `strategy_id` 都是 champion，且其 1 年、2 年超额年化都 >0。
- 同一面板连跑三次，清单、顺序、仓位完全一致。
- 过滤用例各 1 条：停牌、ST、上市第 59 个交易日、20 日均额 4000 万、今日涨停（次日一字）；被过滤的写进 `skipped` 原因。
- 面板最新日 ≠ 当日时生成失败并返回原因；周末访问显示最近一份且 `for_date` = 下一交易日。
- 复盘：一字板样本 `fillable=0` 且不进命中率；`GET /api/picks/review` 返回样本数、T+1/T+5 命中率与平均超额，样本 <20 只标「样本不足」。
- 口径：清单只能引用 `as_of` 及之前的数据，生成时间晚于 `as_of` 收盘；禁止用旧面板推荐明天。
- 成本：生成 0 tokens；开启点评时 ≤2k tokens，点评失败不影响清单。

## 3. 进化（L3）

### 3.0 技能加载

**实现**

5 个 skill，一个核心功能一个，只加载在**决策层**（分析层保留现有证据规则）：

| skill | 功能 | 加载点 | 适应度 |
|---|---|---|---|
| `equity-desk` | 单股推演 | 交易员 + 风控负责人 | 仓位加权超额 − 破止损惩罚 + 方向 Brier |
| `theme-desk` | 主题 / 行业 / 情绪 | 主题研究调用 | 观察信号命中率 + 缺口精确率 |
| `screen-dsl` | 选股条件 | 选股器解析 | 一次解析成功率 |
| `strategy-review` | 回测 / 因子评审 | 回测或因子评估之后 | 与真实样本外结果的一致率 |
| `review-desk` | 复盘与归因 | 复盘时 | 建议被采纳后下轮适应度是否提升 |

- `app/skills/` 放 5 个 md + `manifest.json`，加载时由 `runner.py` 按 manifest 校验后注入。
- 启动校验：总数 ≤ 5、每个角色最多 1 个正式版、正文 ≤ 2400 字（约 1.5k tokens），超了抛 `SkillError` 而不是静默跳过。
- 推演把 `equity-desk@v0.1` 写进 `agent_runs.skill_version`；其余加载点写 `skill_calls`（模块 / 技能 / 版本 / 对应 id）。
- 回测、因子、复盘本身没有模型调用，走 `POST .../commentary`：先跑确定性 rubric，再让模型解释。

**验收**

- 故意加第 6 个技能 → 启动失败并报错；`pytest` 全绿。
- `python scripts/research.py 600519` 报告头带 `equity-desk@v0.1`，`skill_calls` 有对应行。
- `POST /api/backtest/runs/5/commentary` 先返回四条硬门槛 rubric，再返回解释；模型不可用时 rubric 仍可用。

### 3.0.1 技能进化

**实现（待建）**

进化规则（预注册，不得事后改）：

1. **1 个正式版 + ≤ 4 个副本**，同时最多 2 个副本在跑；副本**不接正式决策**，先离线回放 30–50 个历史决策（固定上下文、temperature 0）。
2. **回放与比较**：同一上下文重跑，有 / 无副本配对比较，bootstrap 95% 区间。
3. **晋级条件**：区间不含 0；安全指标（schema 合规、缺口声明、赔率字段完整）不回退；正式版必须人工批准。
4. **变异来源**：失败分类（目标价拍脑袋 / 忽略失效条件 / 用缺失数据 / 仓位超限），每轮最多 2 处改动、正文增幅 ≤ 15%、新增数值门槛必须能指回数据。
5. **留档**：每次决策记录 `skill_id@version`；`skill_runs` 写区间、样本内外指标与结论；正式版降级保留历史版本可回滚。

**验收**

- `python scripts/replay.py --skill equity-desk@v0.1 --contexts 30`（待建）：输出逐条对比与配对 bootstrap 95% 区间。
- 同一上下文重跑两次结果一致（固定面板 + temperature 0）。
- 副本晋级必须有 `skill_runs` 留档（区间、样本内外、结论）；未过门槛时接口拒绝。
- 正式版回滚后，新决策记录的版本号回到上一版。

### 3.1 记忆检索

**实现**

- `agents/memory.py`：`memory_index` + `memory_fts`（FTS5 trigram，中文子串可搜），索引推演与主题结论（标题 / 裁定 / 置信 / 日期 / 复盘结果）。
- 决策层注入：交易员与风控负责人 prompt 带同标的最近 3 条结论，每条带日期与复盘判定，整块标「历史结论（不是事实）……不能当作今天的数字」。

**验收**

- 中文子串（如「白酒」）能命中主题报告；代码能命中推演。
- 第二次推演的报告或 payload 里能看到 3 条历史结论块，且带「不是事实」声明。
- `python scripts/memory_eval.py` 输出有 / 无记忆两组观察性对照，并声明不是因果结论。
- 历史结论不得被写成今日数字：与快照比对 0 处泄漏。

### 3.2 因子挖掘

**实现**

- 面板因子：37 内建 + 4 技术（KDJ / CCI）+ 8 晋级 = 49；外部：Alpha158 无量纲子集 50、学术 9、GTJA191 子集 31、基本面 3。
- `factor/evaluate.py`：截面 Spearman IC、ICIR、t 值、5 分组分层、样本外分段、残差 IC（对 log 市值 / 换手 / 20 日动量逐日截面回归取残差，先去行业均值）。
- 门槛：|ICIR| ≥ 0.3 且 |t| ≥ 3；样本外同号且 |ICIR| ≥ 0.3；残差 |ICIR| ≥ 0.15 且同号；稀疏因子（> 90% 为 0）只报秩相关、不做分组。

**验收**

- `python scripts/factor.py` 默认 3 年、月度截面、20 日前瞻，输出 49 个面板因子表。
- `python scripts/factor.py --oos-months 12 --incremental` 输出三道门通过数（当前双通过 12 个）。
- 手工构造"未来收益的秩"当因子 → IC ≈ 1 且分层严格单调，证明评估器方向正确。
- 每期截面 < 30 只、或有值天数 < 8 期的因子不出结论。

### 3.2.1 外部因子导入

**实现**

- 只按公开公式实现无量纲因子（不抄代码），出处与公式写进模块 docstring；Alpha158 去掉价量水平项，GTJA 丢掉 RANK 外壳与递归 SMA。
- 导入 → 三道门 → 晋级才进 `bt_feat` / `screen_snapshot`；`factor/promoted.py` 维护晋级清单。

**验收**

- 每个外部因子有公式与出处；`python scripts/factor.py --source alpha158 --incremental` 输出 57 个导入 / 7 个双通过。
- 晋级因子出现在 `/api/screener/fields`，可直接写条件、可在回测里排序。
- 未过残差门的因子不进面板（如 `ac_amihud_20` 扣掉市值后方向反转 → 不晋级）。

### 3.3 策略自进化

**实现**

- `strategy/store.py`：`strategies` / `strategy_runs` 两张表 + 窄状态机（template → candidate → watch → champion，可逐级降级，任意状态可淘汰且必须带 reason）。
- `seed_from_presets()` 幂等导入**模板**（`status=template`，不是 champion）：这 12 条是 2026-11-24 选股器 UI 的手写示例，**没有做过参数搜索 / 样本外 / 分组测试**；`provenance_json` 记录 `origin=hand`、`verified=false`、口头依据与建立时间。旧库中误标为 champion 的示例行由 `backfill_legacy_presets()` 一次性改回 template（已人工升级过的行不动）。
- 每周全量回测跑的是 `list_testable()`（champion + template）：模板的成绩正是"值不值得升为候选"的依据；champion 只留给过了门槛、走完观察期的策略。
- 候选生成（待建，P6-2）：`strategy/generator.py` 三条路——因子组合（1–3 个面板因子 + 方向 / 分位阈值）、参数邻域（champion 阈值 ± 20%）、模板（估值 / 质量 / 动量 / 情绪）；输出统一 `ScreenQuery`，复用回测引擎。
- 门槛（P6-4）：`CHAMPION_GATE`（观察 8 周、最近 12 个月超额 > 0、回撤 ≤ 25%、胜率 ≥ 50%、与任一 champion 日收益相关 < 0.8、滑点 5→20bp 仍为正、阈值 ±20% 的 5 个点里 ≥ 4 个超额为正）；`RETIRE_RULE`（连续 12 周跑输 / 回撤破 -30% / 连续 12 周后 25%）；不足 8 周不下淘汰结论。
- 建议（P6-3）：每周六 09:00 全量跑 1 年 + 2 年两个窗口，出「建议晋级 / 淘汰」清单，只建议不执行；状态变更人工确认并写理由。

**验收**

- `POST /api/strategies/generate {"n":10}`（待建）一次产出 5–10 个候选且全部有回测指标；重复执行内容 hash 相同不重复入库。
- 门槛未过时 `POST /api/strategies/{id}/status` 返回 400 并逐项列出未过项；通过时写 `promoted_at`。
- 淘汰清单每周出现；人工确认后 `retired_at` + `retire_reason` 落库，`GET /api/strategies?status=retired` 可查。
- 新 champion 与既有 champion 日收益相关 ≥ 0.8 时被拒。
- 同一候选连跑 3 次指标一致。

### 3.4 策略周期分类（horizon）

**为什么必须分**

同一个信号在不同调仓周期下测的是**不同的策略**。2026-11-28 实测（同一份面板、同一年份）：

| 策略 | 20 日调仓 | 5 日调仓 | 3 日调仓 |
|---|---|---|---|
| 量价齐升 | −65.2%（胜率 42%，226 笔） | −47.9%（469 笔） | −48.0%（胜率 56%，463 笔） |
| 涨停回踩 | −51.6%（胜率 25%） | −72.3% | −79.0%（换手翻倍） |

短周期信号的半衰期只有几天；把它持有 20 天，测的是"追高后一个月"的另一种效应。
用一套参数给所有策略排名，既不公平也失真。

**实现**

- `strategy/horizon.py`：三组默认参数与门槛（策略自己的 `params_json` 优先）
  - `short`：3 日调仓 / 5 只；门槛 = 交易 ≥200 笔、胜率 ≥45%、盈亏比 ≥1.3、年化费用拖累 ≤25%、回撤 ≥-35%
  - `mid`：10 日 / 8 只；门槛 = 交易 ≥60 笔、超额年化 >0、回撤 ≥-30%、胜率 ≥45%
  - `long`：20 日 / 10 只；门槛 = 期数 ≥12、超额年化 >0、回撤 ≥-25%、胜率 ≥50%
- `strategies.horizon` 字段（迁移幂等）；`presets.json` 每个预设标注周期；种子导入时带上。
- 回测引擎新增五个指标：`avg_holding_days`、`turnover_pct`（年化单边换手）、`fee_drag_pct`（年化费用占净资产）、`win_loss_ratio`、`skipped_buys`（涨停买不进次数）。
- 排名**分组进行**：先按 short → mid → long，再在组内按超额年化；`rank` 是组内名次，不跨组混排。

**验收**

- `pytest backend/tests/test_strategy.py -q`：`params_for` 三组参数不同、短线圈门槛能逐项列出未过项、长线圈对"低波动"这类稳态策略放行。
- `python scripts/preset_backtest.py`：输出按 horizon 分组，短线策略用 3 日调仓（`avg_holding_days` ≈ 3）。
- 策略库面板可按周期筛选；分组后组内 <3 条时只展示不排名（人工判读）。
- 同一策略连跑三次，五个新指标完全一致（`symbol ASC` 兜底）。

### 3.5 策略来源（A–E）

**为什么需要这一节**

策略库的管道已经跑通了（template → candidate → watch → champion，分组参数、门槛、周度排名都在），但库里 `champion = 0`。缺的不是管道，是**候选从哪来**。下面五个来源是允许的入口；除此之外不许直接往库里塞条件（比如"看着像"的随手一写）。

五个来源的统一约束：

- 产出物必须是 `ScreenQuery` + `horizon` + **`invalidation`（失效条件）+ `provenance.origin`**（取值 `hand` / `factor_combo` / `neighbourhood` / `profile` / `theme`）；
- 都走同一条流水线：分组参数 → 回测（1 年 + 2 年，含费用/涨跌停/T+1）→ `horizon.gate_check` → candidate → 观察 8 周 → champion（人工确认）；
- **生成类来源（B/C/D/E）不调模型**：必须可复现、可计多重检验（每周生成数记入 `generated_count`，门槛按 N 收紧）；
- `invalidation` 为空时 `POST /api/strategies` 返回 400——"没有失效条件的策略"等于永远不淘汰。

| 来源 | 一句话 | 实现入口 | 状态 |
|---|---|---|---|
| A 人工假设 | 盘面/研报/观察 → 一句可证伪的假设 → 条件 → 回测 | 选股页「存为候选」+ `POST /api/strategies` | 管道有，UI 入口缺 |
| B 因子组合 | 从已晋级因子里取 2–3 个 + 分位阈值 → 批量生成 | `strategy/generator.py` | 未实现 |
| C 参数邻域 | 现有策略阈值 ×0.8/×1.2、调仓 3/5/10/20 扫描 | `strategy/generator.py` | 未实现 |
| D 反向构造 | 回测最好/最差那一期的持仓画像 → 反推条件 | `strategy/generator.py` | 未实现 |
| E 事件/主题驱动 | 主题报告的行业结论 → 行业约束 + 财务/动量条件 | `topic/` + `strategies` | 未实现 |

#### A. 人工假设（成本最低，现在就能用）

**实现**

- `strategies.invalidation TEXT` 字段 + `POST /api/strategies` 强制校验（留空 400）——**已实现**；
- `scripts/seed_manual_batch.py`：批量录入手写假设（假设 / 条件 / 失效条件），可带 `--backtest` 一次跑完并自动晋级过门槛的——**已实现（2026-11-28 首批 50 条）**；
- 选股页「存为候选策略」按钮（UI）：把当前 `query`、`horizon`、`notes`（假设原话）、`invalidation`（失效条件）提交到 `POST /api/strategies`——**待做**；
- `provenance = {"origin": "hand", "rationale": "<假设原话>", "invalidation": "...", "verified": false}`。

**验收**

- 从选股条件一键建候选，库里出现 `status=candidate`、`source=hand`、`provenance.origin=hand`；
- `invalidation` 留空时返回 400 并说明"必须写失效条件"；填了则原样落库并可查；
- 新建的候选在下一批周度回测里有成绩（不需要等周六，`POST /api/strategies/{id}/run` 即可出 1 年成绩）。

#### B. 因子组合

**实现**

- `strategy/generator.py::from_factors(n)`：从 `factor/promoted.py` 的 7 个晋级因子（`a_vstd_20` / `a_vstd_5` / `a_qtl20_60` / `a_corr_20` / `a_corr_5` / `a_mingap_60` / `a_kbar_kup`）里取 2–3 个；
- 每个因子按分位切一刀（20/40/60/80），方向按因子在评估里 IC 的符号；再叠一个估值或流动性兜底条件（`pe_ttm` / `amount`），避免选出一堆无法成交的票；
- 生成数量上限 `n`（默认 10），按条件内容 hash 去重，重复执行不重复入库；
- `provenance = {"origin": "factor_combo", "factors": [...], "quantiles": [...], "verified": false}`。

**验收**

- `python scripts/generate.py --source factor_combo --n 10`：一次产出 ≤10 个候选，全部自动回测并入库为 `candidate`；
- 同一命令连跑两次，第二次新增 0 条（hash 去重）；
- 0 次模型调用（脚本内无 LLM 依赖）；
- 每个候选的 `provenance.factors` 能指回 `factor/promoted.py` 里的因子名。

#### C. 参数邻域 / 市场状态变体

**实现**

- `neighbourhood(strategy_id)`：对策略每个数值阈值生成 `×0.8` / `×1.2` 两个变体，调仓周期换成 3/5/10/20 中与当前不同的两个；
- 输出一组"邻域"结果，**不做最优选择**，只回答"这条策略是不是靠在某个点上碰巧正收益"；
- 稳健标记：5 个邻域点里 ≥4 个同向（超额为正）记为 `steady=True`，否则 `fragile`。

**验收**

- 对任一 champion 生成 5 个邻域变体并全部回测，输出 `steady` / `fragile`；
- 报告里给出邻域明细（每个变体的超额年化），不只给结论；
- 结果只影响"是否建议晋级"，不自动改状态。

#### D. 反向构造（画像）

**实现**

- `profile_from_run(strategy_id, best: bool)`：取该策略最好（或最差）的一期的 holdings，统计画像：行业中位数、市值分位、`pe_ttm` 分位、`a_vstd_20` 分位、`ret20` 分位；
- 把画像写成条件（例如"市值 200–800 亿、PE <30、20 日波动 <0.35、近 20 日涨幅 >5%"），生成候选；
- 报告里必须带样本数（那一期几只票）与"该期是最好还是最差"。

**验收**

- `python scripts/generate.py --source profile --strategy <id> --best`：产出 1 个候选 + 画像摘要；
- 样本 <5 只时拒绝生成（"画像不足以支撑条件"）；
- 生成的候选明确标注"由某一期反推，存在选择性偏差"。

#### E. 事件/主题驱动

**实现**

- 从 `topic_reports` 取最近一份报告里的行业/题材结论（存在 `scope` 里）；
- 生成条件：行业约束（`industry in (...)`）+ 财务或动量条件（`profit_yoy ≥ x` 或 `ret20 ≥ x`）+ 流动性兜底；
- `provenance = {"origin": "theme", "report_id": ..., "sector": ...}`。

**验收**

- 从一份主题报告生成 1–3 个候选，条件里能看到行业约束；
- 报告缺失该行业时明确报"没有可用的行业结论"，不硬凑；
- 生成的候选在回测里能跑出结果（行业约束不会把股票池筛成空）。

**共同的退出口**

任何来源的候选，最终只由两件事决定生死：**分组门槛（§3.4）+ 观察期（§3.3）**。来源只影响"从哪来"，不影响"能不能留下"。

## 4. 质量属性

| 属性 | 实现 | 验收 |
|---|---|---|
| 证据链 | 报告结论带数据日期与 `skill_id@version`；`agent_runs.skill_version`、`topic_reports.as_of` | 抽查 10 条结论，每条都能指回表 / 字段 / 日期 |
| 数据指纹 | `data/fingerprint.py` + `/api/data/fingerprint` + 报告头 | 同面板两次一致；改一行后变化 |
| 成本台账 | `ops/ledger.py` 的 usage 汇总 + `/api/usage` | 与 `agent_runs` / `topic_reports` 的 tokens 求和对得上 |
| 数据健康 | `ops/health.py` 覆盖 17 张表 | `python scripts/ops.py health` 无未解释的滞后 / 残缺 |
| 查询性能 | DuckDB 列存 + `screen_snapshot` 物化 | 单查询 p95 ≤ 300 ms；快照构建 ≤ 2 s |
| 测试 | `pytest backend/tests -q` + GitHub Actions | 全绿；输入校验 / 边界 / 无未来函数有对应用例 |
| 可运维 | 一键脚本 + 结构化错误 | 被锁 / 缺表返回可读错误，不是 500 堆栈 |
| 版本 | git + tag + CHANGELOG + 面板指纹 | `git describe` 有 tag；每个版本在 CHANGELOG 有记录 |

## 维护约定

- 目标增删改 → 先改 [goat.md](goat.md)（只写目标与现状），再改本文件（实现 + 验收），最后改 [todo.md](todo.md)（排期与执行）。
- 验收里出现的新命令 / 新接口必须真实存在；尚未实现的标「待建」，不写成既成事实。
- 门槛常量（因子三道门、策略 `CHAMPION_GATE` / `RETIRE_RULE`、技能晋级）改动时，本文件与代码一起改。
