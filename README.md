# PersonalTrading

本地个人 **A 股 / ETF 研究助手**。单机运行，不依赖 MongoDB / Redis，不做用户体系，数据与笔记全部保存在本地 SQLite。

> 目标价与操作建议均为模型基于本地面板的推算，不保证准确。行情来自公开网页接口，可能延迟、缺失或临时限流。

## 功能

- **自选股 / ETF 管理**：代码、名称、拼音搜索，一键加入自选。
- **实时行情**：最新价、涨跌幅、开高低、成交量/额，新浪优先、东财兜底。
- **K 线图表**：日 / 周 / 月线，蜡烛图、MA5/10/20/60、成交量、缩放。
- **技术指标**：MACD、RSI14、BOLL、均线排列与简单信号提示。
- **基本面快照**：营收、归母净利润、ROE、毛利率、资产负债率、EPS/BPS、报告期。
- **AI 研究笔记**：基于当前行情、指标与基本面，调用 DeepSeek 生成结构化中文研究笔记，并保存在本地。
- **多智能体推演**：7 位分析师 → 多空辩论 → 交易员 → 三方风控，流式输出完整报告。
- **主题研究**：用一句话研究大盘 / 行业 / 题材 / 市场情绪，输出带数据的结构化备忘。
- **选股器**：自然语言条件 → 全市场横截面筛选（53 个字段）。
- **策略回测**：把选股条件当成策略，在历史上逐期换仓，算收益、超额、回撤与胜率。
- **因子挖掘**：在真实面板上算 IC / ICIR 与分层收益，看哪个信号真的能区分未来涨跌。
- **模拟持仓**：按推演裁定建仓的虚拟账户，T+5 / T+20 自动复盘。
- **运维视图**：统一决策账本、token 成本台账、数据健康与面板指纹。
- **本地缓存**：行情 5 秒、K 线 5 分钟、基本面 1 小时，减少公开接口压力。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.11、FastAPI、httpx、SQLite（标准库 sqlite3） |
| 市场面板 | DuckDB（全市场日线 / 复权因子 / 交易日历） |
| 前端 | Vue 3、TypeScript、Vite、ECharts |
| 行情 | 新浪财经、东方财富公开 HTTP 接口、Tushare Pro（可选）、AKShare（交易日历） |
| 模型 | DeepSeek OpenAI 兼容接口（默认 `deepseek-flash`） |
| 存储 | `data/personaltrading.db`（自选/笔记）+ `data/market.duckdb`（市场面板） |

## 项目结构

```
PersonalTrading/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI 入口
│   │   ├── config.py            .env 配置
│   │   ├── db.py                SQLite 表与读写（自选/笔记）
│   │   ├── schemas.py           请求/响应模型
│   │   ├── data/                P0 数据底座
│   │   │   ├── store.py         DuckDB 面板：符号/日历/日线/估值
│   │   │   ├── sync.py          同步编排（日历/列表/快照/历史）
│   │   │   ├── datasets.py      数据集注册表（接口 → 表）
│   │   │   ├── backfill.py      通用回填引擎（断点续传/幂等/限速）
│   │   │   ├── fingerprint.py   面板指纹（日期 + 行数 digest）
│   │   │   └── providers/       tushare / sina / eastmoney / akshare
│   │   ├── agents/              P1 多智能体研究
│   │   │   ├── context.py       从面板构建分析上下文
│   │   │   ├── llm.py           DeepSeek 客户端（JSON 模式 + 用量）
│   │   │   ├── prompts.py       各角色 system prompt 与 schema
│   │   │   ├── nodes.py         角色节点
│   │   │   ├── graph.py         LangGraph 编排
│   │   │   ├── runner.py        运行、流式事件、报告
│   │   │   └── store.py         决策日志
│   │   ├── backtest/            P3 策略回测
│   │   │   ├── spec.py          参数校验与默认值
│   │   │   ├── features.py      历史截面（as-of，无未来函数）
│   │   │   ├── engine.py        组合模拟（次日开盘成交、费用、复权）
│   │   │   ├── service.py       编排、指标、落库
│   │   │   └── store.py         回测记录（SQLite）
│   │   ├── topic/               主题研究
│   │   │   ├── context.py       市场快照（指数/广度/行业/题材/两融）
│   │   │   ├── prompts.py       system prompt 与报告 schema
│   │   │   ├── service.py       调用、校验、渲染、落库
│   │   │   └── store.py         主题报告（SQLite）
│   │   ├── factor/              因子挖掘
│   │   │   ├── library.py       37 内建 + 4 技术指标（KDJ/CCI）
│   │   │   ├── promoted.py      7 个已晋级因子（进入面板与快照）
│   │   │   ├── alpha158.py      Qlib Alpha158 无量纲子集（余 50 个）
│   │   │   ├── academic.py      学术因子（MAX / 偏度 / Beta / IVOL / Amihud 等 9 个）
│   │   │   ├── gtja.py          国泰君安 Alpha191 子集（31 个，仅需 OHLCV）
│   │   │   ├── fundamental.py   应计 / SUE / 公告后漂移 / 换手波动（4 个）
│   │   │   ├── evaluate.py      IC / ICIR / 分层 / 样本外 / 残差 IC
│   │   │   ├── service.py       参数校验、编排、落库
│   │   │   └── store.py         因子评估记录（SQLite）
│   │   ├── ops/                 运维视图
│   │   │   ├── ledger.py        统一决策账本 + 成本台账
│   │   │   └── health.py        数据健康（滞后 / 残缺日）
│   │   ├── paper/               模拟账户与复盘
│   │   ├── routers/             meta / watchlist / stocks / research / market / agents / screener / backtest / topics / paper
│   │   └── services/
│   │       ├── symbols.py       代码识别、交易所、搜索
│   │       ├── market.py        实时行情与 K 线（单股）
│   │       ├── indicators.py    MA / MACD / RSI / BOLL
│   │       ├── fundamentals.py  财务快照
│   │       ├── benchmark.py     指数基准（前向填充）
│   │       └── research.py      AI 研究笔记
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── api.ts
│   │   ├── types.ts
│   │   └── components/          自选、行情头、图表、指标、基本面、笔记
│   ├── package.json
│   └── vite.config.ts
├── data/                        SQLite、DuckDB 与运行时日志（gitignore）
├── scripts/                     启动脚本 + sync_market / research / screen / backtest / paper
├── .env.example
└── README.md
```

## 快速开始

> 想先人工过一遍功能？照着 [manual-check.md](manual-check.md) 的 15 条用例走，约 60–90 分钟（会花约 60k tokens）。

### 1. 后端

```bash
cd PersonalTrading

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r backend/requirements.txt

copy .env.example .env            # Windows
# cp .env.example .env            # macOS / Linux
```

编辑 `.env`，至少填写：

```dotenv
PERSONALTRADING_DEEPSEEK_API_KEY=sk-...
PERSONALTRADING_DEEPSEEK_MODEL=deepseek-flash
```

启动：

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8010
```

或 Windows：

```powershell
scripts\start_backend.ps1
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

或 Windows：

```powershell
scripts\start_frontend.ps1
```

打开 http://127.0.0.1:5173 。

### 3. 首次使用

1. 左侧搜索 `000001`、`510050` 或“平安银行”；
2. 点击搜索结果加入自选；
3. 右侧查看行情、K 线、指标和基本面；
4. 点击「生成研究笔记」调用 DeepSeek。

## 数据底座（P0）

单股页面靠的是「现查现取」。筛选、因子挖掘、回测需要的是**全市场横截面**，所以先把面板落到本地 DuckDB。

### 为什么是 DuckDB

因子计算要的是「某一天全市场所有股票」，SQLite 的 `(symbol, trade_date)` 索引对这种查询无效，会全表扫。DuckDB 是列存的，`WHERE trade_date = ?` 直接按区扫描，5000 只 × 10 年（约 1300 万行）也在毫秒级。SQLite 继续只放自选、笔记这类小表。

### 表结构

| 表 | 内容 |
|----|------|
| `symbols` | 代码、名称、交易所、板块、行业、上市/退市日 |
| `calendar` | 每个自然日是否交易日（含未来日期） |
| `daily` | 未复权 OHLCV + `adj_factor` 累计复权因子 |
| `daily_basic` | PE/PB/市值/换手率 |
| `sync_log` | 每次同步的作业、行数、状态 |

### 复权口径

库里存**未复权**价格和累计复权因子，两种口径都能精确还原：

- 后复权：`close × adj_factor`
- 前复权：`close × adj_factor / 最新 adj_factor`（最新一根与原始价一致）

因子由「后复权序列 ÷ 未复权序列」逐日推导，来源为东财 `push2his` 的 `fqt=0` 与 `fqt=2` 两次请求；Tushare 可用时直接取官方 `adj_factor`。

### 同步命令

```bash
# 首次：日历 + 列表 + 当日快照 + 历史（增量）
python scripts/sync_market.py all

# 日常：收盘后更新当日截面（一次请求拉全市场）
python scripts/sync_market.py snapshot

# 指定标的补历史
python scripts/sync_market.py history --symbols 600000,000001 --start 2020-01-01

# 查看覆盖情况与数据源可用性
python scripts/sync_market.py status
```

当前面板实测规模：**6506 只标的、627 万根日线，覆盖 2021-01-04 至 2026-11-20**。历史全量是一次性成本，按 6 并发约 13 分钟，**可中断，重跑自动续传**。

### 三条防错规则

1. **快照按交易日归档**：周六跑 `snapshot`，数据记在周五名下，不会记成周六。日历超过 15 天未更新则直接拒绝，避免把当天行情写到旧日期上。
2. **盘中拒绝写日线**：15:05 前抓取会得到半日 K 线，快照与历史同步都会跳过当天，必须显式 `--allow-intraday` 才写快照。
3. **已有数据不被降级**：重抓原始价不会覆盖已有 `adj_factor` 和 `amount`；因子写入只用 COALESCE，绝不写 NULL。

### 数据源降级

`provider_for(capability)` 按能力挑第一个可用的源，Tushare 的 token 会在进程内探测一次，无效就自动跳过：

| 能力 | 优先级 |
|------|--------|
| 交易日历 | Tushare → AKShare |
| 股票列表 / 全市场快照 | Tushare → 新浪 |
| 单股历史 + 复权因子 | Tushare → 新浪 → 东方财富 |

## Tushare 网关与全量下载

### 接入方式

本项目直接用 HTTP 调 Tushare 协议，不依赖 `tushare` 包。协议一致，改地址即可切换到第三方网关：

```dotenv
PERSONALTRADING_TUSHARE_TOKEN=...
PERSONALTRADING_TUSHARE_BASE_URL=https://quantdata888.duckdns.org
PERSONALTRADING_TUSHARE_RATE_PER_MINUTE=180
```

官方地址 `https://api.tushare.pro` 是默认值；网关默认限 200 次/分钟，代码里有全局滑动窗口限速 + 40203 自动退避，多线程也不会打爆。

用 `status` 可看到 token 是否可用，`token_status` 会显示套餐与到期时间。

### 数据集清单

| 名称 | 接口 | 模式 | 说明 |
|------|------|------|------|
| `daily` | daily | 按日 | 全市场日线，写入核心 `daily` 表 |
| `adj_factor` | adj_factor | 按日 | 复权因子，落表后一次性物化到 `daily` |
| `daily_basic` | daily_basic | 按日 | PE/PB/市值/换手，写入核心 `daily_basic` |
| `fund_daily` / `fund_adj` | 同名 | 按日 | ETF 日线与复权，并入核心 `daily` |
| `moneyflow` | moneyflow | 按日 | 主力资金流向 |
| `stk_limit` | stk_limit | 按日 | 涨跌停价 |
| `cyq_perf` | cyq_perf | 按日 | 筹码分布 |
| `limit_list_d` | limit_list_d | 按日 | 涨跌停统计 |
| `margin_detail` | margin_detail | 按日 | 融资融券 |
| `top_list` / `top_inst` | 同名 | 按日 | 龙虎榜与机构席位 |
| `hsgt_top10` | hsgt_top10 | 按日 | 沪深股通十大 |
| `stk_factor` | stk_factor | 按日 | Tushare 预计算技术指标 |
| `block_trade` | block_trade | 按日 | 大宗交易 |
| `index_basic` / `index_daily` | 同名 | 一次 / 按代码 | 指数列表与日线 |
| `sw_daily` | sw_daily | 按代码 | 申万一级行业指数（31 个） |
| `index_global` | index_global | 按代码 | 恒生等全球指数 |
| `income_vip` … `express_vip` | 同名 | 按报告期 | 三大报表、财务指标、预告、快报 |
| `dividend` / `stk_holdernumber` / `stk_holdertrade` / `share_float` / `stock_company` / `new_share` / `ths_index` | 同名 | 按日 / 按代码 / 一次 | 事件与参考数据 |
| `ths_hot` / `dc_hot` | 同名 | 按日 | 同花顺 / 东财热榜（关注度） |
| `limit_cpt_list` | limit_cpt_list | 按日 | 涨停概念榜（题材热度） |
| `broker_recommend` | broker_recommend | 按月 | 券商月度金股 |
| `stk_surv` | stk_surv | 按代码 | 机构调研记录 |

新增一个接口只需要在 `backend/app/data/datasets.py` 里加一条 `DatasetSpec`，表结构会根据接口返回的字段自动推断。

### 下载命令

```bash
# 看计划与已完成进度
python scripts/sync_market.py plan --years 10

# 全量回填（断点续传，可随时 Ctrl-C）
python scripts/sync_market.py backfill --years 10

# 只回填指定数据集
python scripts/sync_market.py backfill --datasets daily,adj_factor,daily_basic --years 10

# 修复某一天的残缺数据（不改动其他日期）
python scripts/sync_market.py backfill --datasets margin_detail --date 2026-11-20 --years 1

# 先看计划不下载
python scripts/sync_market.py backfill --dry-run

# 日更：只追加某一天
python scripts/sync_market.py day
```

回填引擎的四条性质：

1. **断点续传**：每完成一段（一天/一只/一个报告期）就写 `dataset_progress`，中断后重跑只补未完成的；
2. **幂等**：重复拉同一段会按主键 upsert，不会产生重复行；
3. **单位只换算一次**：Tushare 的成交量是手、成交额是千元，入库统一换成股和元；
4. **限速共享**：所有线程共用一个 180 次/分钟的窗口，超限自动退避。

### token 有效期与配额

当前套餐为 30 天（`token_status` 可查到期时间），且**有每日请求上限**——实测约 3 万次/天，一次全量回填跑不完，会返回 `42901 token daily limit exceeded`。

引擎对此的处理：一遇到 42901 就**立即停止整轮**（剩余分段不空跑），已完成的分段照常落库，次日重跑同一条命令自动续传。

到期后：

- 已下载的数据不受影响，历史面板照常可用；
- 日更会失效，此时 `snapshot` 会自动降级到新浪（无复权因子）；
- 续费后重新执行 `day` 或 `backfill` 即可补齐缺口。

### 已知限制

- **Tushare 套餐只有 30 天有效期**：到期后历史数据仍可查询，但日更与因子回填会停。续费后重跑 `day` / `backfill` 即可补齐。
- **网关按分钟限流**（默认 200 次/分钟）。全量回填是几万次调用，十几分钟到几小时量级，务必用 `backfill` 的断点续传而不是反复重跑。
- **未开通的接口**：`susp_d`、`concept*`、`hk_daily`、`stk_mins`、`rt_min`、`ggt_top10`、`stk_auction_o` 返回 `api not purchased`，注册表里没有收录。
- **新浪/东财仍是兜底**：Tushare 不可用时 `snapshot` 走新浪全市场截面（无因子），`history` 走新浪 1023 根（约 4 年）。
- **东财 `clist` 与 `82.push2` 分片主机**在部分网络不可用，本项目不依赖它们。
- **AKShare 函数名变动频繁**，只在交易日历一处使用并做名字容错。
- **ETF 无财务数据**：`daily_basic` 的 PE/PB 对 ETF 为 NULL。

### 数据清单（2026-11-21 实测）

| 表 | 行数 | 内容 |
|---|---:|---|
| `daily` | 13,424,672 | 股票 + ETF 日线，2016-11-22 起 |
| `adj_factor` | 11,152,730 | 复权因子（已物化到 `daily`） |
| `stk_limit` | 10,970,210 | 每日涨跌停价 |
| `daily_basic` | 10,746,996 | PE/PB/市值/换手率 |
| `moneyflow` | 10,496,196 | 主力资金流向 |
| `cyq_perf` | 9,814,551 | 筹码分布 |
| `margin_detail` | 6,183,458 | 融资融券 |
| `index_daily` | 2,992,409 | 交易所指数日线（1013 个） |
| `fund_adj` / `fund_basic` | 2,778,901 / 3,037 | ETF 复权与列表 |
| `sw_daily` | 1,247,351 | 申万行业指数（961 个） |
| `fina_indicator_vip` | 237,929 | 财务指标 |
| `income_vip` / `balancesheet_vip` / `cashflow_vip` | 217,981 / 208,406 / 198,583 | 三大报表 |
| `top_inst` / `top_list` | 205,678 / 145,868 | 龙虎榜 |
| `limit_list_d` | 169,894 | 涨跌停统计 |
| `forecast_vip` / `express_vip` | 75,506 / 18,429 | 业绩预告 / 快报 |
| `index_global` / `index_basic` | 27,369 / 8,000 | 全球指数 / 指数元数据 |

合计约 **8137 万行、3.5 GB**。

## 多智能体研究（P1）

单次 LLM 调用写不出一份经得起反驳的研究。P1 把研究拆成一条有对抗的流水线：

```
START ─┬─ 市场分析师   ─┐
       ├─ 基本面分析师 ─┤
       ├─ 资金面分析师 ─┤
       ├─ 热度与舆情   ─┼─▶ 多头 ⇄ 空头（N 轮辩论）─▶ 研究主管
       ├─ 事件驱动     ─┤                              │
       ├─ 大盘分析师   ─┤                              ▼
       └─ 板块分析师   ─┘   风控负责人 ◀─ 风控三方 ◀─ 交易员
```

### 七位分析师与 A 股适配

角色划分参考 TradingAgents-CN，但**按本项目的真实数据可得性裁剪**，没有的接口就换成可验证的代理指标，绝不编造新闻：

| 角色 | 看什么 | 数据来源 |
|---|---|---|
| 市场分析师 | 均线排列、MACD/RSI/BOLL、回撤与区间 | 日线（前复权） |
| 基本面分析师 | 盈利质量、成长性、财务结构、估值分位 | 三大报表 + 财务指标 + 每日指标 |
| 资金面分析师 | 主力净流入结构、两融、筹码分布 | `moneyflow` + `margin_detail` + `cyq_perf` |
| 热度与舆情分析师 | 热榜排名、涨停概念、股东户数、龙虎榜、北向 | `ths_hot`/`dc_hot`/`limit_cpt_list`/`top_list`/`hsgt_top10` |
| 事件驱动分析师 | 业绩预告/快报、分红、解禁、增减持、大宗、调研 | `forecast_vip`/`dividend`/`share_float`/`stk_holdertrade`/`block_trade`/`stk_surv` |
| 大盘分析师 | 指数走势、涨停/跌停家数、成交额、两融余额 | `index_daily` + `limit_list_d` + `daily` + `margin_detail` |
| 板块分析师 | 申万一级轮动、同业估值与上涨占比 | `sw_daily` + `symbols.industry` |

> **为什么没有真正的「新闻分析师」**：网关未开通 `news`、`anns`、`cctv_news`、`major_news`、`research_report`、`irm_qa_*`。与其让模型凭空编新闻，不如用公告的可验证结果（业绩预告、分红、解禁、增减持）做事件分析，用热榜与概念做关注度分析——这两类在 A 股的实际信息含量并不低。
>
> **为什么没有「社媒分析师」**：股吧/雪球正文同样不可得。`ths_hot`/`dc_hot` 是交易所生态里的公开热度排名，比爬股吧更稳定、也更少噪音。

### 决策链上的其余角色

| 角色 | 职责 |
|---|---|
| 多头/空头研究员 | 各建最强论证，逐条反驳对方，说明各自逻辑的证伪条件 |
| 研究主管 | 判谁更站得住脚，列出真正的分歧点（不折中） |
| 交易员 | 动作、仓位、**目标价与止损价**、入场区间、失效条件、持有周期、改变信号 |
| 风控三方 | 激进/中性/保守三种立场各挑漏洞，各自给仓位上限 |
| 风控负责人 | 汇总冲突，给最终裁定与最终仓位 |

### 运行

```bash
# 命令行（约 2-4 分钟，15-20 次调用）
python scripts/research.py 600519
python scripts/research.py 600519 --rounds 3 --report data/600519.md
```

```bash
# 流式接口（SSE），前端 AgentPanel 用的就是它
curl -N http://127.0.0.1:8010/api/agents/analyze/600519?rounds=2
```

事件序列：`stage` → `run_start` → 每个节点 `node_start` / `node_end` → `run_end`（带完整 markdown 报告、用量、裁定）。节点一结束就推送，不会等整轮跑完。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agents/status` | 模型、面板、辩论轮数配置 |
| GET | `/api/agents/analyze/{symbol}?rounds=2` | SSE 流式推演 |
| POST | `/api/agents/analyze/{symbol}` | 同步版本，返回最终结果 |
| GET | `/api/agents/runs?symbol=` | 历史推演列表 |
| GET | `/api/agents/runs/{id}` | 单次推演的完整报告与逐节点 payload |

### 设计约束

1. **只用面板数据**：`context.py` 里每个数字都来自 DuckDB 查询，运行时零网络取数，同一份面板重跑结果可复现。
2. **缺失就说缺失**：prompt 明令禁止编造，数据缺口会进入 `data_gaps` 字段，渲染成"数据缺失"而不是零。
3. **结构化输出**：每个角色返回固定 schema 的 JSON，`_checked_json` 缺字段会追加一轮纠正请求；不改用自然语言解析。
4. **失败可降级**：任一角色失败只记录到 `errors`，报告里标"运行异常"，不中断整轮。
5. **决策日志**：`agent_runs` + `agent_messages` 两表存下每个节点的原始 payload——这是 P2 记忆与复盘的数据基础，几周后可以回来判断当轮的多头和空头谁对。

### 成本与耗时

| 轮数 | 调用次数 | tokens | 耗时 |
|---|---:|---:|---:|
| 1 轮 | 16 | ~45k | ~2 分钟 |
| 2 轮 | 18 | ~65k | ~2.5 分钟 |

### 目标价与赔率

交易员必须给出 `target_price` 与 `stop_price`，**但百分比不由模型算**：

```
期望收益 = 目标价 / 收盘价 − 1
下行风险 = 止损价 / 收盘价 − 1
赔率     = (目标价 − 收盘价) / (收盘价 − 止损价)
```

代码按收盘价计算并写进报告的「目标与赔率（推算）」一节，赔率低于 1.5:1 时会额外提示"按交易员规则本应观望"。这样做的原因是：模型单独写"目标价 1650，预期上涨 10%"很容易自相矛盾；把目标价变成唯一输入，其余派生量就不会打架。

模型给不出数字（写"数据不足"）时，报告会明说，而不是补一个看起来合理的值。

模型默认 `deepseek-flash`。注意它是推理模型，`reasoning_content` 也算在 `max_tokens` 里，预算给少了 JSON 会被截断——各角色的预算已按此调到 1600-3400。

一次 2 轮推演的分歧度是有意义的：实测 600519 时，市场分析师给 +55、板块分析师 +35、资金面 +48，
而大盘分析师给 **-68**（指数全线下跌、两融去杠杆），最终交易员只给出 20% 仓位、风控再压到 10%。
多个角色同时看多的场景会因大盘一项被显著拉低——这正是加「大盘分析师」的目的。

## 主题研究

单股推演回答"这只票怎么办"，但它答不了"市场现在什么位置""白酒还能不能拿""AI 算力这波还有多久"——这些没有单一标的的问题。主题研究把面板打成一份**市场快照**，让模型在快照上写一份可证伪的备忘。

```bash
python scripts/topic.py "市场情绪现在处于什么位置"
python scripts/topic.py "白酒行业还能拿吗" --md data/baijiu.md
python scripts/topic.py "白酒" --context          # 只看数据快照，不调模型
python scripts/topic.py --list                    # 历史报告
```

前端在页面上半部分有「主题研究」面板：输入主题或点预设，30-90 秒后得到一份带裁定的报告，答辩前可以展开看它引用了哪些数据。

### 快照里有什么

| 块 | 内容 | 口径 |
|---|---|---|
| `indices` | 上证 / 深证 / 创业板 / 沪深300 / 中证500 / 中证1000 / 科创50 / 北证50 | 1/5/20/60 日涨跌，各自最新日期 |
| `breadth` | 上涨 / 下跌家数、站上 MA20 / MA60 的比例 | 全市场个股 |
| `turnover` | 当日成交额、20 日均额、量比、近 5 日序列 | 个股 amount 合计 |
| `limits` | 涨停 / 跌停家数、5 日均值 | `limit_list_d` |
| `margin` | 两融余额、20 日变化 | 只取**完整**交易日（见下方护栏） |
| `north` | 沪深股通十大成交股 20 日净买入 | 不是全市场北向，字段里带 note |
| `hot_concepts` | 涨停概念榜前 12（连板数、涨停家数） | `limit_cpt_list` |
| `industries` | 行业涨幅两端各 15 个 + 与主题匹配的行业（20 日收益、5 日上涨占比、20 日资金） | `symbols.industry` ≈ 110 个 |
| `industry_detail` | 匹配行业的 5/20/60 日收益、PE 中位、资金流、龙头与前 3 落后股 | 同上 |
| `sw_l1` | 申万一级 31 个行业的当日涨跌、PE、PB | 按固定代码取，不会混进 L2/L3 |

**模型只写不查**：它拿到的每个数字都来自这份快照，prompt 明令禁止引入外部数字；快照里缺的（比如北向净流入），必须写进 `data_gaps`，不允许脑补。报告 schema 也是固定的——标题、范围、裁定（看多/中性/看空/无法评估）、信心、摘要、关键判断（判断 + 证据）、驱动、风险、观察信号、数据缺口、三段展开。缺字段会触发一次纠正；两次都缺就返回 400，**不会保存一份空壳报告**。

### 三条数据护栏

1. **残缺的两融日会被跳过**：某天同步只写了一半标的时，直接求和会变成"两融一天去杠杆 50%"的假信号。`margin` 只取标的数接近近期中位数的日期，并说明回退到了哪天。
2. **缺失就说缺失**：`hsgt_top10.net_amount` 近 20 日全为空时，`north` 块整个不出现，`data_gaps` 写明"无法给出北向净流入"。
3. **滞后要说清楚**：申万行业指数、涨跌停统计比个股面板晚一个交易日时，报告末尾的口径行会写出来源日期，不会把 11-20 的数据说成 11-23。

### 实测（2026-11-23 的快照）

问「白酒行业还能拿吗」，模型给 **中性 / 58 分**，摘要里每个判断都挂着数字：

> 白酒 19 只成分股 ret20 -1.83%、ret60 -0.11%，明显强于上证 -4.29%/-9.38% 和创业板 -13.57%/-35.69%。资金面 mf_net_20d +172.7 亿，在有数据行业中仅次于银行的 377.1 亿……但短期动能已断：ret5 -7.8%、up_ratio_5d 0.0%，19 只无一只 5 日上涨。

观察信号也写成了可验证的形式（"up_ratio_5d 能否从 0.0% 回升到 30% 以上""古井贡酒 ret20 能否守住 +10%"），而不是"关注后续走势"这类没法打分的句子。

### 成本

一次报告约 **5.4k prompt + 3.9k completion**（≈1.2 万 tokens），耗时 30-90 秒。快照本身 0.4 秒内查完，可以作为 `/api/topics/context` 单独调用，不花模型钱。


## 选股器

数据层有 9500 万行横截面，但"今天该看什么"一直缺一个入口。选股器把自然语言条件翻成结构化查询，直接筛全市场。

```bash
python scripts/screen.py "市盈率低于30、ROE大于15%、20日主力净流入为正、近5日没有涨停"
python scripts/screen.py --fields                 # 列出 41 个可用字段
python scripts/screen.py --json '{"filters":[{"field":"pe_ttm","op":"<=","value":20}]}'
```

前端在页面顶部有「选股器」面板：输入条件或点预设，结果表任意一行可直接点开做研究。

### 怎么做到快的

每次查询都现算 5500 只股票的 20 日涨幅、均线偏离、MACD、90 日涨停次数要几秒。所以先物化成一张宽表 `screen_snapshot`（每股一行，约 6500 行），查询就是一次带 WHERE 的扫描：

- 首次构建约 **0.9 秒**，同一交易日内复用缓存；
- 表结构变了（新增字段）会自动重建；
- 辅助数据按**各自最近可用日期**取——两融、筹码、技术因子在收盘后几小时才发布，不会因为"今天没有"就整列变空，`/api/screener/meta` 会返回每个数据块的日期。

### 字段目录（41 个）

| 类别 | 字段 |
|---|---|
| 价格 | `close` `pct_chg` `ret5` `ret20` `ret60` `amount` `avg_amount_20d` `turnover_rate` `volume_ratio` |
| 技术 | `gap_ma20` `gap_ma60` `drawdown_52w` `rsi12` `macd_hist` `boll_pos` |
| 估值 | `pe_ttm` `pb` `ps_ttm` `dv_ttm` `total_mv` `circ_mv` `pe_pct_3y` |
| 基本面 | `roe` `gross_margin` `net_margin` `debt_ratio` `revenue_yoy` `profit_yoy` |
| 资金 | `mf_net_1d` `mf_net_20d` `elg_net_1d` `margin_bal` `margin_chg_20d` `chip_winner` |
| 情绪 | `limit_up_90d` `limit_up_5d` `lhb_90d` `north_90d` `hot_rank` |
| 属性 | `industry` `board` |

### 两条安全边界

1. **模型不写 SQL**。它只填 JSON 条件，字段名必须在白名单里；未知字段直接 400 并把可用字段告诉它，不会静默生成一个错误筛选。
2. **值一律参数绑定**，只有白名单后的字段名会拼进 SQL。

解析失败会给模型一轮纠正（附上具体错误），仍失败则返回 400 而不是跑一个"猜的"查询。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/screener/fields` | 字段目录与可用运算符 |
| GET | `/api/screener/meta` | 快照日期、标的数、各数据块来源日期 |
| POST | `/api/screener/parse` | 自然语言 → 查询条件（不执行） |
| POST | `/api/screener/run` | 结构化条件 → 结果 |
| POST | `/api/screener/ask` | 自然语言 → 结果（一步到位） |
| POST | `/api/screener/refresh` | 强制重建快照 |

## 策略回测

选股器回答「今天哪些票符合条件」，回测回答**「如果过去一直这样做，能不能赚钱」**。两条路共用同一份条件语法和同一个字段白名单：筛出什么，就回测什么。

```bash
# 一句话条件，直接跑（默认最近两年、20 日调仓）
python scripts/backtest.py "ROE大于15%、市盈率低于30"

# 指定区间、调仓周期和持仓数，并打印每期明细
python scripts/backtest.py "股息率超过4%、市盈率低于15" --start 2023-01-01 --rebalance 60 --top 5 --detail

# 结构化条件（与选股器同格式）
python scripts/backtest.py --json-file query.json

# 历史回测记录
python scripts/backtest.py --list
```

前端在页面上半部分有「策略回测」面板：先在选股器里筛一次，点结果右上角的**「用这些条件回测」**，面板会带着这份条件、按当前参数立刻回测，并画出净值对比曲线。

### 规则

回测最容易骗人的地方不是收益算错，而是**用了当时还拿不到的信息**。所以规则写死在这几条上：

| 环节 | 做法 | 为什么 |
|---|---|---|
| 信号 | 调仓日**收盘后**用当日数据算条件 | 与选股器完全同源 |
| 成交 | **下一个交易日开盘价**成交 | 用当日收盘价成交等于白拿一根 K 线 |
| 涨停 | 开盘价 ≥ 涨停价 99.9% 视为买不到，跳过 | 一字板买不进是常态 |
| 跌停 | 开盘价 ≤ 跌停价 100.1% 视为卖不出，顺延到下一交易日 | 不能假设想卖就能卖 |
| 停牌 | 没有当日 K 线就跳过买入 / 顺延卖出 | 停牌不是 -100% |
| 复权 | 收益与净值都用 `close × adj_factor` 口径 | 分红送转是收益，不是假跌 |
| 财报 | 按 `ann_date`（公布日）关联，绝不按 `end_date` | 报告期结束 ≠ 市场当天知道 |
| 费用 | 佣金万 2.5（最低 5 元）+ 印花税 0.05% + 过户费 0.001% + 滑点（默认 5bp） | 与模拟盘同一套 `app/fees.py` |
| 仓位 | 每期等权，最多 `top_n` 只，100 股一手，不加杠杆、闲置现金不计息 | 结果可复现 |

`features.py` 一次物化出所有调仓日的横截面（在 DuckDB 窗口函数里算 MA、涨幅、52 周高点，再按当日关联资金/筹码/热度，按公布日关联财报），所以查询本身只是一次带 `row_number()` 的扫描：**5 年、61 期、36 万行截面约 7 秒**。

### 指标怎么读

| 指标 | 含义 | 什么时候不可信 |
|---|---|---|
| 年化收益 / 基准年化 | 按 252 个交易日折算 | 区间短于 1 年时波动极大 |
| 超额年化 | 策略年化 − 沪深300 年化 | 基准缺失时为 0，会在 warnings 里提示 |
| 最大回撤 | 净值曲线的峰谷最大跌幅 | 只看回撤会偏爱空仓策略 |
| 胜率 | 各期跑赢基准的比例 | 期数少时（< 10 期）只看胜率没意义 |
| 仓位 | 日均持仓市值 / 总资产 | 仓位低说明信号经常凑不满 `top_n` |
| 空仓期 | 没有任何标的可买的期数 | 空仓期多说明条件过严或数据缺失 |

### 两个口径提醒

1. **回测的「20 日主力净流入」是最近 20 个交易日的累计**；选股器快照的同一字段是按 40 个自然日窗口近似的，两者会有差异。
2. **`pe_pct_3y` 在回测里不可用**：三年滚动分位要逐行扫三年窗口，代价比整张特征表还高。传了这个字段会直接 400 并说明原因，而不是悄悄返回 NULL。

### 已知限制

- **退市股不强制清算**：面板里没有退市流程，股票停牌到区间末尾就按最后收盘价估值，这会**高估**策略收益。
- **只有固定滑点，没有冲击成本**：小市值股票的容量问题体现不出来，回测结果对大资金偏乐观。
- **样本内偏差**：显式规则（"估值低、ROE 高"）比自然语言调参好得多，但反复改条件直到曲线变好看，仍然是在过拟合。
- **空仓期不计收益**：闲置现金按 0 收益处理，没有货币基金假设。
- **分红按除权价再投资**：复权因子已把现金分红计入价格回报，但分红不会作为现金留在账上，等于假设它按除权价立刻再投。

### 实测

2025-11 至 2026-11 的一年期，`ROE≥15% 且 0<PE≤30`、按 ROE 排序取前 10、20 日调仓：

| | 策略 | 沪深300 |
|---|---:|---:|
| 区间收益 | **-36.43%** | -13.37% |
| 最大回撤 | -46.08% | — |
| 胜率 | 25.0% | — |
| 成交 / 费用 | 228 笔 / 10,875 元 | — |

这个结果**不好看，但它是有信息量的**：同一套条件在 2021-11 至 2026-11 的五年里也是 -42.25% vs 基准 -19.50%，超额年化 -6.38%。回测的价值不在于证明策略有效，而在于在往里投钱之前就知道它可能无效。

## 因子挖掘

回测验证的是"一套条件"，因子挖掘验证的是"**一个信号**"。它还复用同一张 `bt_feat` 截面——所以这里跑出结论的因子，可以原样写进选股条件和回测，不存在"研究一套、实盘另一套"。

```bash
# 最近三年、月度截面、20 日前瞻，评估全部 48 个面板因子
python scripts/factor.py

# 只评估几个因子，看分层明细
python scripts/factor.py --factor ret20,mf_net_20d,amount --detail --top 10

# 换个前瞻期 / 更密的截面
python scripts/factor.py --horizon 60 --rebalance 10 --start 2021-01-01

# 历史评估记录
python scripts/factor.py --list
```

前端在「主题研究」下方有「因子挖掘」面板：选好区间和前瞻期，一张表按 |ICIR| 排序，点任意一行看该因子的 IC 时序和分层收益。

### 三个指标怎么算

| 指标 | 算法 | 读法 |
|---|---|---|
| **IC** | 每个截面上「因子值」与「未来 h 日收益」的 **Spearman 秩相关**，并列值用平均秩 | 0.05 以上算有点用，0.02 以下基本是噪音 |
| **ICIR** | IC 均值 / IC 标准差 | 衡量稳定性；\|ICIR\| < 0.1 的因子换个区间就翻转 |
| **t 值** | ICIR × √期数 | \|t\| < 2 时不要下结论，37 个因子同时看更该用 \|t\| > 3 当门槛 |
| **分层价差** | 按因子值分 5 组，第 5 组减第 1 组的平均前瞻收益 | 单调性比价差大小更重要 |

前瞻收益用 `close × adj_factor` 计算，和回测、模拟盘同一口径；每期截面少于 30 只、或有值天数少于 8 期的因子直接不出结论，而不是给一个虚的 IC。

### 实测（2023-11 → 2026-11，36 期有效，20 日前瞻）

| 因子 | 类别 | IC | ICIR | t 值 | 正 IC 占比 | 分层价差 |
|---|---|---:|---:|---:|---:|---:|
| `limit_up_5d` 近5日涨停次数 | 情绪 | -0.078 | -1.24 | -7.45 | 6% | — |
| `limit_up_90d` 近90日涨停次数 | 情绪 | -0.081 | -0.89 | -5.34 | 19% | -1.37% |
| `lhb_90d` 龙虎榜次数 | 情绪 | -0.087 | -0.80 | -4.81 | 19% | -1.16% |
| `amount` 成交额 | 流动性 | -0.133 | -0.80 | -4.79 | 19% | -2.54% |
| `mf_net_20d` 20日主力净流入 | 资金 | +0.059 | +0.60 | +3.62 | 81% | +1.26% |
| `roe` ROE | 质量 | +0.003 | +0.02 | +0.10 | 50% | -0.52% |

`amount` 的分层最能说明问题——在这段区间里，**成交额最小的 20% 每 20 天平均 +2.39%，最大的 20% 只有 -0.15%**，五组严格单调：

```
Q1 +2.39%  Q2 +1.85%  Q3 +1.27%  Q4 +0.60%  Q5 -0.15%
```

反过来，`roe` 的 IC 是 0.003、`gross_margin` 是 -0.011——质量因子在 20 日维度上几乎没有区分度。这说明的不是"ROE 没用"，而是**三个月这个尺度上，A 股主要由流动性和情绪定价**。

### 样本外与晋级门槛

样本内 IC 好看不等于有效。加 `--oos-months` 后，区间被切成两段，因子必须在**两段同号且都达到门槛**才算通过：

```bash
python scripts/factor.py --start 2021-11-22 --end 2026-11-21 --oos-months 12
```

| 条件 | 门槛 |
|---|---|
| 样本内 | \|ICIR\| ≥ 0.3 且 \|t\| ≥ 3 |
| 样本外 | 与样本内同号，且 \|ICIR\| ≥ 0.3 |

实测（2021-11 → 2026-11，最后 12 个月留作样本外，49 + 12 个截面）：

| 因子 | 样本内 ICIR | 样本外 ICIR | 结论 |
|---|---:|---:|---|
| `limit_up_5d` | -1.57 | -0.92 | 通过 |
| `amount` | -0.96 | -0.48 | 通过 |
| `ret20` | -0.61 | -0.37 | 通过 |
| `mf_net_20d` | +0.51 | +0.36 | 通过 |
| `roe` | -0.21 | +0.08 | 未通过（样本内就不够强） |

5 个因子里 4 个通过——但要注意这是**同一段历史里切出来的**样本外，比真正的向前验证弱一档；等数据再走几个月，应该用「过去 4 年训练 + 最近 1 年验证」滚动复核。

### 外部因子导入（Alpha158 打样）

Qlib 的 Alpha158 是**机器学习特征集**，不是因子库：`OPEN0` / `CLOSE0` 这种价量水平做横截面排序没有意义（把所有股票按股价排名？）。所以只取其中**无量纲**的部分：

- K 线形态 9 个：实体位置/长度、上下影线、收盘偏移，以及它们相对振幅的版本；
- 16 个算子 × 3 个窗口（5/20/60）＝ 48 个：区间涨幅、均线偏离、波动率、区间高低点距离、RSV、分位偏离、趋势斜率与拟合度、量价相关、涨跌天数差、收益不对称、量能偏离/波动、量价冲击。

合计 **57 个**（其中 7 个已晋级进面板，见下），公式按 Qlib 定义（MIT）实现，只依赖日线 OHLCV + 复权因子，不需要新数据源。

```bash
# 原始 IC + 样本外 + 残差 IC，三道门一起过
python scripts/factor.py --start 2021-11-22 --end 2026-11-21 \
    --source alpha158 --oos-months 12 --incremental
```

API 等价写法：`POST /api/factors/run {"source":"alpha158","incremental":true,"oos_months":12}`。

**第三道门：残差 IC。** 一个因子的原始 IC 很可能只是「市值 / 换手 / 动量」的代理。做法是对因子值和三个控制变量（log 市值、换手率、20 日动量）逐日做截面回归（先去行业均值），再用残差算 IC。只有残差 |ICIR| ≥ 0.15 且方向不变，才算带来了新信息。

实测（2021-11 → 2026-11，20 日前瞻，最后 12 个月为样本外）：

| 阶段 | 通过数 |
|---|---:|
| 导入因子 | 57 |
| 原始 ICIR ≥ 0.3 且 \|t\| ≥ 3 | 32 |
| 样本外同号且 ICIR ≥ 0.3 | 13 |
| 残差 ICIR ≥ 0.15 且同号 | 21 |
| **同时通过样本外与增量** | **7** |

七个"新东西"：`a_vstd_20`、`a_qtl20_60`、`a_corr_20`、`a_corr_5`、`a_mingap_60`、`a_vstd_5`、`a_kbar_kup`。

反例比正例更能说明为什么要三道门：

| 因子 | 原始 ICIR | 样本外 ICIR | 残差 ICIR | 真相 |
|---|---:|---:|---:|---|
| `a_roc_20` | -0.61 | -0.37 | +0.03 | 就是 20 日动量，面板里已有 `ret20` |
| `a_vma_60` | -0.57 | -0.43 | **+0.43** | 原始负 IC 是"放量 = 大市值/高换手"的代理，扣掉风格后方向反转 |
| `a_vstd_20` | -0.66 | **-1.69** | -0.56 | 量能波动，两段都强，扣掉风格后仍有信息 |
| `a_qtl20_60` | +0.64 | +0.42 | +0.22 | 60 日 20 分位偏离，正向 |

成本：5 年 × 57 因子 × 两段（样本内/外）≈ 100 秒，其中 `bt_ext` 的窗口计算占大头。

**已晋级：7 个双通过因子已物化进面板**（`bt_feat` 与 `screen_snapshot` 都有列），可以在选股器里直接写条件、在回测里直接当排序字段：

| 因子 | 面板列名 | 选股器里的标签 |
|---|---|---|
| `a_vstd_20` / `a_vstd_5` | 量能波动 | 量能波动20日 / 量能波动5日 |
| `a_corr_20` / `a_corr_5` | 量价相关 | 量价相关20日 / 量价相关5日 |
| `a_qtl20_60` | 60 日 20 分位偏离 | 60日20分位偏离 |
| `a_mingap_60` | 距 60 日低点 | 距60日低点 |
| `a_kbar_kup` | 上影线占比 | 上影线占比 |
| `fd_pead` | 公告后漂移 | 公告后漂移（最近公告日至今相对沪深300，≤120 交易日） |

同时把 Tushare 预计算但一直没被当因子用过的 **KDJ（K/D/J）与 CCI** 也纳入了面板字段。选股器现在共 **53 个字段**。

### 第二批：学术因子

Alpha158 是价量几何，学术因子补的是另一条轴（彩票偏好、特质波动、流动性）。9 个定义全部来自公开文献，公式写在因子里：

```bash
python scripts/factor.py --source academic --oos-months 12 --incremental
```

实测（同区间、同门槛）：

| 因子 | 原始 ICIR | 样本外 | 残差 ICIR | 结论 |
|---|---:|---:|---:|---|
| `ac_max_20` 20 日最大单日涨幅 | -0.70 | -0.48 | -0.57 | **双通过** |
| `ac_max_60` | -0.54 | -0.39 | -0.60 | **双通过** |
| `ac_skew_60` 收益偏度 | -0.53 | -0.48 | -0.48 | **双通过** |
| `ac_ivol_60` 特质波动 | -0.46 | -0.37 | -0.68 | **双通过** |
| `ac_amihud_20` 非流动性 | +0.59 | +0.36 | -0.17 | 样本外过、残差反转（本质是市值代理） |
| `ac_turn_trend` 换手率 20/120 | -0.57 | -0.34 | -0.04 | 同上，是拥挤度代理 |
| `ac_downvol_60` 下行波动 | -0.31 | -0.29 | -0.55 | 样本内不够强，未过 |
| `ac_mom_12_1` 12-1 动量 | -0.31 | -0.21 | -0.14 | 未过 |
| `ac_beta_60` | -0.07 | +0.00 | +0.04 | 无信息 |

9 个里 **4 个双通过**：MAX 效应（20/60 日）、收益偏度、特质波动。方向全部为负——近期"彩票型"特征越强，未来 20 日越差，这与 Bali (2011) 和 Ang (2006) 的结论一致。`ac_amihud_20` 是个好例子：原始 IC 为正、样本外也过，但扣掉市值后方向反转，说明它测的是规模而不是流动性。

**下一批候选**：国泰君安 Alpha191（研报公式，需逐条实现）、更多学术因子（盈余动量、应计、换手波动）。导入流程不变：公式 → 三道门 → 晋级才进面板。

### 第三批：GTJA191 子集（31 个）

国泰君安 2017 年那篇研报的 191 个短周期价量因子。实现时做了两处**明说的简化**：

- **丢掉 RANK 外壳**：研报里很多因子写的是 `RANK(...)`，而 IC 本身就是秩相关，先排名再相关等于没变；丢掉它才能按股票逐日计算。乘了两个 RANK 的公式（如 Alpha7、Alpha118 系列的一部分）这一批没收。
- **不收 SMA(…,n,m) 与 TSRANK**：前者是递归均值，后者需要再走一遍窗口排名；都留到下一批。VWAP 用 `成交额 / 成交量` 近似。

结果（2021-11 → 2026-11，最后 12 个月样本外）：

| 门槛 | 通过数 |
|---|---:|
| 导入 | 31 |
| 样本外 | 2 |
| 残差 IC | 13 |
| **双通过** | **0** |

这批里最"像因子"的几个：`gtja17`（开收跳动平方和，IC 0.111、ICIR 0.62）、`gtja96`（振幅，0.108/0.56）、`gtja20`（5 日最小绝对波动，0.080/0.56）。它们**样本内强、增量也过，但样本外 ICIR 只有 0.22–0.26**，没过 0.3 的门槛；而过了样本外的 `gtja39/41`（放量）又完全没有增量信息。

结论很直接：**这 31 个价量因子在"20 日 / 月度"这个尺度上，要么是已有面板因子的变形，要么样本外衰减。** 0/31 不是失败，是过滤器在工作——如果照单全收，这 31 个都会被当成"新因子"写进策略。

### 第四批：基本面与事件因子（4 个）

| 因子 | 定义 | 样本内 ICIR | 样本外 | 残差 ICIR | 结论 |
|---|---|---:|---:|---:|---|
| `fd_pead` 公告后漂移 | 公告日至今日相对沪深300 的超额（≤120 天） | -0.53 | -0.62 | -0.20 | **双通过** |
| `fd_accrual` 应计利润 | (EPS − 每股经营现金流) / BPS | -0.34 | -0.21 | -0.34 | 样本外不够强 |
| `fd_turn_vol` 换手波动 | 60 日换手率标准差 / 均值 | -0.19 | -0.95 | -0.66 | 样本内不够强 |
| `fd_sue` 盈利惊喜 | 同比 EPS 变化 / 过去 8 次变化标准差 | -0.06 | +0.06 | +0.30 | 无信息 |

`fd_pead` 是唯一双通过的，但**符号为负**：公告后的相对赢家在随后 20 日反而跑输，这是反转而不是经典的 PEAD 延续。这和 A 股"利好出尽"的经验一致，也提醒一件事——文献结论搬到 A 股要先过数据，不能直接照抄方向。

它已经晋级进面板（`bt_feat` 与 `screen_snapshot`），选股器可以直接写条件：

```bash
python scripts/screen.py --json '{"filters":[{"field":"fd_pead","op":">=","value":5}],"sort":[{"field":"fd_pead","desc":true}]}'
```

实现上有一个坑：财报公告日常常落在周末（例如 2026-10-31 是周六），按 `ann_date` 精确取价会把这些公告全部丢成 NULL。现在的做法是取**公告日之后第一个交易日**的开盘价作为入场价（最多向后找 10 天），漂移窗口也从那个交易日算起。修正前后覆盖率从 63% 提到 86%。

### 已知限制（比结果更重要）

- **IC 是样本内统计**（除非显式跑了 `--oos-months`）。同一个因子在 2019-2021 和 2023-2026 的符号经常相反，所以这里的表是"这段区间发生了什么"，不是"未来什么有效"。
- **多重检验**：140+ 个因子一起看，总有几个 t 值漂亮。要么把门槛提到 |t| > 3，要么在另一段区间做样本外复核。
- **没有行业/市值中性化**：`amount` 的强度里有相当一部分是小市值和行业暴露，分层价差不能当成纯 alpha。
- **前瞻收益不扣费用**：IC 衡量的是排序能力，不是可交易收益；能不能赚钱得看回测。
- **稀疏因子不做分层**：像"近 5 日涨停次数"这种 99% 为 0 的因子，五分组本身没有意义，只报告秩相关。


## 模拟持仓与复盘

研究给出结论，但没人验证结论对不对。这一层把结论落成虚拟持仓，再用后续行情回头打分。

```bash
python scripts/paper.py summary                    # 账户概览
python scripts/paper.py buy 600519 --amount 200000 # 按金额买入（取整到 100 股）
python scripts/paper.py sell 600519 --shares 100
python scripts/paper.py from-run 3                 # 按推演的最终裁定建仓
python scripts/paper.py nav
python scripts/paper.py review                     # T+5 / T+20 复盘与命中率
python scripts/paper.py reset --cash 1000000
```

前端在页面底部有「模拟持仓」面板，推演结果里也带「按此裁定建仓」按钮。

### 三条规则，为了让复盘有意义

1. **按面板收盘价成交**，不用实时报价——否则复盘时比较的是一个谁都拿不到的价格。
2. **收费用**：佣金 0.025%（最低 5 元）+ 卖出印花税 0.05% + 过户费 0.001%。频繁交易不可能靠"忘了手续费"看起来赚钱。
3. **净值由交易重放得出**，不是增量累加，所以同一份交易记录每次都得到同一条曲线。停牌期间沿用上一个收盘价，不会误算成 -100%。

### 复盘怎么判定

每条推演冻结它自己 as-of 日的收盘价作为入场价，然后算 T+5 / T+20 收益和相对沪深300 的超额：

| 裁定 | 含义 | 判定 |
|---|---|---|
| 批准 / 调整后批准 + 仓位>0 | 看多 | T+20 上涨算正确 |
| 0% 仓位或否决 | 空仓 | T+20 下跌算正确（躲过了下跌） |

用**风控负责人的最终裁定**而不是交易员的意愿——账户跟随的是裁定。|T+20| 小于 1% 记为中性区间。

`/api/paper/review/stats` 汇总命中率、平均收益、平均超额，并按立场拆分。

### 顺带修的一个数据缺口

`index_daily` 是逐代码拉取的，不在日更范围内，导致净值基准取不到当天的指数。现在 `day` 会额外刷新 6 个基准指数（6 次调用），净值曲线的沪深300 不会再断档。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/paper/summary` | 现金 / 市值 / 盈亏 / 持仓明细 |
| POST | `/api/paper/trade` | 手工买卖 |
| POST | `/api/paper/from-run/{id}` | 按推演裁定建仓 |
| GET | `/api/paper/positions` | 持仓列表 |
| GET | `/api/paper/nav` | 净值序列（含基准） |
| GET | `/api/paper/trades` | 成交流水 |
| POST | `/api/paper/review/refresh` | 重算 T+5 / T+20 |
| GET | `/api/paper/reviews` · `/review/stats` | 复盘明细与统计 |
| POST | `/api/paper/account/reset` | 重置账户 |

## 运维与账本

四个模块各自写自己的记录（推演、主题、回测、成交），运维视图只**读**它们，不再造第五份真相。前端页面最下方有「运维」面板，三块：**账本 / 成本 / 数据健康**；同一个信息也有 CLI 与 API。

### 统一决策账本

```bash
python scripts/ops.py ledger --limit 20        # 所有结论，按时间倒序
python scripts/ops.py ledger --kind 推演        # 只看推演
python scripts/ops.py summary                  # 计数 + 复盘命中率 + token 总量
```

账本把四类记录归一成一行：`类型 / 时间 / 标题 / 数据日 / 结论 / 仓位或分数 / 复盘 / tokens`。推演会带上 `agent_reviews` 的 T+20 判定（正确 / 错误 / 待验证），这是"这条建议后来怎样"的第一个版本。对应 `GET /api/ledger`、`/api/ledger/summary`。

### 复盘刷新

```bash
python scripts/ops.py review            # 只看状态：覆盖、待验证、命中率、最近刷新时间
python scripts/ops.py review --refresh  # 先重算 T+5/T+20，再汇报
```

`--refresh` 会扫描最近 200 次推演，按各自的 as-of 日冻结入场价，算 T+5 / T+20 收益与相对沪深300 超额，写入 `agent_reviews`。判定标准不是"涨了就算对"，而是**看当时的立场**：看多需上涨、否决/空仓需下跌，|收益| < 1% 算中性区间。

工作日 **19:30**（日更之后）有定时任务自动跑一次；没有新判定时只回一行。对应 `GET /api/ledger/review` 与 `POST /api/ledger/review/refresh`。

`ops.py summary` 现在会一并显示：推演总数、已建复盘数、覆盖率、待验证数、命中率、最近刷新时间。

### 一条决策的来龙去脉

账本里的每条记录都能指出自己从哪来：回测和主题可以把 `source_run_id` 挂到一次推演上，成交本来就带 `run_id`。

```bash
python scripts/ops.py chain 10           # 推演 #10 及其派生的一切
python scripts/backtest.py "ROE>15" --source-run 10
python scripts/topic.py "白酒" --source-run 10
```

`chain` 输出：推演（裁定 / 仓位 / tokens）→ 复盘（T+20 判定）→ 回测（收益 / 超额 / 回撤）→ 主题 → 成交。API 对应 `GET /api/ledger/chain/{run_id}`；`POST /api/backtest/run` 与 `POST /api/topics/research` 接受可选的 `source_run_id`。

数据库升级：`source_run_id` 是后加列，`init_db()` 会用 `PRAGMA table_info` 检查并 `ALTER TABLE` 补齐，老库不需要手工迁移，重复执行也不会出错。

### 成本台账

```bash
python scripts/ops.py usage --days 30
```

按日列出推演次数、主题报告数、回测次数、因子评估次数、tokens 与推演耗时。对应 `GET /api/usage`。有了它才能回答"一次推演 8 万 tokens 值不值"。

### 数据健康与指纹

```bash
python scripts/ops.py health
```

输出每张关键表的最新日期、行数、滞后交易日与状态，并单独标出**残缺日**。实测就抓到一条：`margin_detail` 在 2026-11-20 只有 2019 行（正常约 4489），说明那天的两融同步中断了——主题研究的两融护栏会自动跳过它，但数据本身需要补。

`GET /api/data/fingerprint` 返回面板指纹（最大日期 + `daily` 行数 + 各表最新日期 → 12 位 digest），同一份面板重算结果一致，报告与回测可以据此核对"是不是同一版数据"。

### 数据源降级演练（Tushare 到期）

```bash
python scripts/check_sources.py          # 完整演练（会真实请求）
python scripts/check_sources.py --quick  # 只看能力矩阵
```

2026-11-23 的实测结论（Tushare 到期 2026-12-20 23:35，剩 27 天）：

| 能力 | 降级路径 | 演练结果 |
|---|---|---|
| 日更快照 | Tushare → 新浪 | ✅ 6477 行 |
| 单股历史 | Tushare → 新浪 → 东财 | ✅ 新浪 29 根（无复权因子） |
| 复权因子 | Tushare → 东财 | ❌ 东财在本机被拦截，取到 0 根 |
| 交易日历 | Tushare → AKShare | ✅ 51 天 |
| 全市场估值 | Tushare → 新浪（部分） | ⚠️ PE/PB/市值可能缺失 |

**结论：到期后日线与历史查询照常，但复权因子没有降级源。** 到期前的动作：

1. 确认 `adj_factor` 已回填到今天（当前已覆盖到 2026-11-23）；
2. ~~决定续费，或接受"到期后新数据的复权口径退化为原始价"~~ → **2026-11-24 决定续费**，2026-12-19 09:00 有定时提醒；
3. 续费后验证：`check_sources.py` 里 `expires_at` 已延后、`sync_market.py status` 显示 Tushare 可用；
4. 无论是否续费，每天看一眼 `ops.py health`：`daily` 应为 0 滞后，`stk_factor` / `cyq_perf` 可能变空（它们依赖 Tushare）。

## 界面

前端是一套单页应用，左侧固定导航，右侧按模块切换（切换时保留各模块状态）：

| 导航 | 内容 |
|---|---|
| 大盘 | 四大指数、涨跌家数（A股/ETF 分开）、**涨跌分布直方图**、涨跌停、两市成交额、**主力/北向资金（同一张卡）**、**行业资金流向（细分）**、**申万一级/二级行业切换**的领涨领跌、**涨停梯队与封板率** |
| 自选 | 自选股列表，点击进入详情，右键可移除 |
| 推演 | 直接搜代码/名称选股 → 7 位分析师 → 辩论 → 交易员 → 风控 |
| 主题 | 主题研究 + 常用问题下拉 |
| 选股 | 自然语言条件 → 全市场筛选（53 字段） |
| 回测 | 条件回测、净值曲线、历史留档 |
| 因子 | 因子评估（panel / Alpha158 / 学术 / GTJA / 基本面） |
| 运维 | 账本 / 成本 / 数据健康，账本每行可展开「决策链」 |

几个交互约定：

- **主题**：右上角切换白天/黑夜，选择存在 `localStorage` 的 `pt-theme`，由 `data-theme` 驱动 CSS 变量；首次加载在挂载前应用，避免闪白。
- **右键**：任意股票行右键 → 查看详情 / 加入（移出）自选 / 复制代码；加入后列表与详情状态同步。
- **个股详情**：点击任意股票（热榜、自选、选股结果）进入；顶部是报价与涨跌幅，下面有 **分时 / 日K / 周K / 月K** 四个图（周月线由日线聚合：开=首日开、收=末日收、高低=区间极值、量=求和），K 线支持 **3月 / 6月 / 1年 / 3年 / 全部**，点「全部」可回到 2016 年上市首日（日线约 2400 根、周线 513 根、月线 121 根），再往下是估值与盈利指标、因子快照、近期推演记录；「开始推演」会带着这只股票跳到推演模块。
- **一键今日复盘**：大盘模块右上角一个按钮，生成「结论 / 盘面 / 情绪 / 资金 / 板块 / 操作建议 / 昨日对照 / 明日观察 / 风险 / 数据缺口」十段式复盘，结果**当天缓存并显示在大盘页**（默认收起，生成后自动展开）。个人账户用途，**允许给明确的买卖方向与仓位区间**（标为推算并写依据）。复盘会引用情绪分位（近一年）、成交额分位、涨跌分布、涨停梯队与封板率、主力/北向/两融、行业资金、板块集中度与昨日领涨延续性；**所有数字都要与快照比对**，编造就自动降级为规则版。对应 `GET /api/topics/today`（只读缓存 + 情绪序列 + 历史）与 `POST /api/topics/today`（生成，body `{"force":true}` 可强制重算）。点一次约 4–6k tokens、30–60 秒。
- **涨跌家数分市场**：正股与 ETF 分开统计（顶部小标签切换，默认 A 股）；本地面板不含港股，港股标签会禁用并注明。各区块右上角会标出**自己的数据日**，早于面板数据日时打「滞后」标，避免把 11-20 的板块当成今天。
- **分时**：走 `/api/stocks/{symbol}/intraday`，主源东财、兜底新浪，结果缓存在 SQLite 的 `intraday_cache`（盘中 60 秒、收盘后 12 小时）。它**不进市场面板**，所以不会影响回测口径；取不到时显示「暂无分时数据」，不会画空图。

## 配置

所有配置使用 `PERSONALTRADING_` 前缀，读取项目根目录 `.env` 或 `backend/.env`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PERSONALTRADING_HOST` | `127.0.0.1` | 监听地址 |
| `PERSONALTRADING_PORT` | `8010` | 后端端口 |
| `PERSONALTRADING_DATABASE_PATH` | `data/personaltrading.db` | SQLite 路径（自选/笔记） |
| `PERSONALTRADING_MARKET_DB_PATH` | `data/market.duckdb` | DuckDB 路径（市场面板） |
| `PERSONALTRADING_TUSHARE_TOKEN` | 空 | Tushare Pro / 网关 token |
| `PERSONALTRADING_TUSHARE_BASE_URL` | `https://api.tushare.pro` | 服务地址，可指向第三方网关 |
| `PERSONALTRADING_TUSHARE_RATE_PER_MINUTE` | `180` | 全局限速（次/分钟） |
| `PERSONALTRADING_SYNC_CONCURRENCY` | `4` | 历史同步并发线程数 |
| `PERSONALTRADING_SYNC_YEARS` | `10` | 历史同步默认回溯年数 |
| `PERSONALTRADING_DEEPSEEK_API_KEY` | 空 | 未配置时 AI 笔记不可用 |
| `PERSONALTRADING_DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容地址 |
| `PERSONALTRADING_DEEPSEEK_MODEL` | `deepseek-flash` | 模型 ID |
| `PERSONALTRADING_QUOTE_TTL` | `5` | 行情缓存秒数 |
| `PERSONALTRADING_KLINE_TTL` | `300` | K 线缓存秒数 |
| `PERSONALTRADING_FUNDAMENTALS_TTL` | `3600` | 基本面缓存秒数 |

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/meta` | 应用/模型/数据源信息 |
| GET | `/api/watchlist` | 自选列表 |
| POST | `/api/watchlist` | 添加自选，body `{"symbol":"000001"}` |
| DELETE | `/api/watchlist/{symbol}` | 移除自选 |
| GET | `/api/search?q=...` | 搜索 A 股 / ETF |
| GET | `/api/stocks/{symbol}/quote` | 实时行情 |
| GET | `/api/stocks/{symbol}/detail` | 个股详情：报价 / 120 日 K / 因子 / 财务 / 分时 / 近期推演 / 自选状态 |
| GET | `/api/stocks/{symbol}/intraday` | 最近交易日分钟线（东财 + 新浪兜底，SQLite 缓存） |
| GET | `/api/stocks/{symbol}/chart?period=day&limit=250` | K 线 + 指标 |
| GET | `/api/stocks/{symbol}/fundamentals` | 基本面快照 |
| POST | `/api/stocks/{symbol}/research` | 生成 AI 研究笔记 |
| GET | `/api/research-notes?symbol=...` | 历史笔记 |
| GET | `/api/market/status` | 面板覆盖情况 + 数据源可用性 |
| GET | `/api/market/overview` | 当日大盘：指数、涨跌家数、涨跌停、主力净流入、板块涨跌、热榜 |
| GET | `/api/market/calendar?start=&end=` | 交易日历 |
| GET | `/api/market/trade-days?start=&end=&limit=` | 交易日列表 |
| GET | `/api/market/universe?asset_type=&exchange=&board=` | 全市场标的 |
| GET | `/api/market/snapshot?trade_date=&asset_type=` | 某日全市场截面（含估值） |
| GET | `/api/market/symbols/{symbol}` | 标的基础信息 |
| GET | `/api/market/daily/{symbol}?adjust=qfq` | 面板日线（none/qfq/hfq） |
| GET | `/api/backtest/meta` | 回测参数、面板区间、费用模型、可用字段 |
| POST | `/api/backtest/run` | 按选股条件跑一次回测并入库 |
| GET | `/api/backtest/runs?limit=20` | 回测历史（指标摘要） |
| GET | `/api/backtest/runs/{id}` | 单次回测的完整结果（净值/每期/成交） |
| GET | `/api/topics/meta` | 面板日期、模型状态、示例主题、行业清单 |
| GET | `/api/topics/context?topic=` | 某主题的数据快照（不调用模型） |
| POST | `/api/topics/research` | 生成主题研究报告并入库 |
| GET | `/api/topics/reports?limit=20` | 历史主题报告 |
| GET | `/api/topics/reports/{id}` | 单份报告（报告 JSON + markdown） |
| GET | `/api/factors/library` | 因子清单、面板区间、指标口径 |
| POST | `/api/factors/run` | 评估因子并入库（`source` / `incremental` / `oos_months`） |
| GET | `/api/factors/runs?limit=20` | 因子评估历史 |
| GET | `/api/factors/runs/{id}` | 单次评估的完整结果（含 IC 时序与分层） |
| GET | `/api/ledger?limit=20&kind=` | 统一决策账本（推演/主题/回测/成交） |
| GET | `/api/ledger/summary` | 账本计数、复盘命中率、token 总量 |
| GET | `/api/usage?days=30` | 按日 token 与任务量 |
| GET | `/api/data/health` | 关键表最新日期、滞后、残缺日 |
| GET | `/api/data/fingerprint` | 面板指纹（日期 + 行数 digest） |

完整交互文档：http://127.0.0.1:8010/docs 。

## 数据源说明

- **新浪财经**：`hq.sinajs.cn` 实时行情、`quotes.sina.cn` 日线历史。接口需要 `Referer`，返回 GBK。
- **东方财富**：`push2.eastmoney.com` 实时行情、`searchapi.eastmoney.com` 搜索、`datacenter-web.eastmoney.com` 财务指标。
- 两者都是公开网页接口，没有 SLA，可能限流或临时不可用。项目已做缓存、请求间隔和失败兜底。
- 东方财富的历史 K 线接口在部分网络环境会被反爬拦截；本项目历史 K 线统一走新浪。
- 东方财富股票列表接口每页最多 100 条并会限流，因此本项目不使用它拉全市场列表，搜索走 suggest 接口。
- **分时线**（个股详情里的「分时」）走东财 `trends2`，失败时兜底新浪 `getKLineData`；结果缓存在 SQLite `intraday_cache`（盘中 60 秒、收盘后 12 小时），不写入市场面板。
- **上游发布时间不一致**：`margin_detail`、`ths_hot` 常常在 19:00 日更之后才发布，`sw_daily` / `limit_list_d` 以前根本不在日更列表里（2026-11-24 已修）。现在 19:00 与 21:30 各跑一次 `sync_market.py day`，前端每个区块也会标出自己的数据日，早于面板日就打「滞后」。
- **北向资金**：新增 `moneyflow_hsgt`（沪深港通资金，单位万元，已回填近一年并纳入日更），大盘「主力 / 北向」卡片展示北向净流入及沪股通/深股通拆分；两融余额来自 `margin_detail`，通常比日线晚 1 个交易日，卡片与复盘都会显式标注。

## 开发

```bash
# 后端测试
pytest backend/tests -q

# 前端类型检查 + 构建
cd frontend
npm run build
```

## 免责声明

- 本项目是个人本地研究工具，**会给出目标价、期望收益与买卖动作**；这些都是模型基于本地面板的推算，不是保证，也不构成对任何第三方的投资建议。
- 目标价与止损价由交易员给出，期望收益与赔率由代码按收盘价计算（见「目标与赔率」一节），两者都可以被事后复盘证伪。
- 所有行情、财务数据与 AI 生成内容都可能存在延迟、错误或缺失；投资有风险，决策与后果由使用者自行承担。

## License

这是个人私有项目，尚未选择开源许可证。在公开发布或分发前，请自行选择并添加合适的许可证。