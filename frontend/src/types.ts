export type AssetType = 'stock' | 'etf'

export interface WatchlistItem {
  symbol: string
  name: string
  asset_type: AssetType
  created_at?: string
}

/** 自选行指标：现价、今日、近 5 日、添加后（百分比，已 ×100）。 */
export interface WatchlistMetric {
  symbol: string
  name: string
  asset_type: AssetType
  price: number | null
  pct_chg: number | null
  pct_5d: number | null
  pct_since_add: number | null
  added_trade_date: string | null
  added_close: number | null
  as_of: string | null
  quote_source: string
}

/** 一个可点开的标的引用，用于详情、右键菜单与跨模块跳转。 */
export interface StockRef {
  symbol: string
  name: string
  kind?: 'stock' | 'index'
}

/** 右键菜单事件负载：客户端坐标用于把菜单放在鼠标旁。 */
export interface StockContextPayload extends StockRef {
  x: number
  y: number
}

/** 常用问题分组，QuestionSuggest 的输入。 */
export interface QuestionGroup {
  label: string
  questions: string[]
}

/** 左侧导航分区。选股与回测合并为「策略回测」，模拟持仓独立（2026-11-27）。 */
export type SectionKey =
  | 'market'
  | 'watchlist'
  | 'universe'
  | 'strategy'
  | 'paper'
  | 'topic'
  | 'factor'
  | 'agent'
  | 'ops'

export type ThemeMode = 'dark' | 'light'

export interface SearchResult {
  symbol: string
  name: string
  asset_type: AssetType
  exchange: string
  market: string
}

export interface Quote {
  symbol: string
  name: string
  asset_type: AssetType
  exchange: string
  price: number | null
  open: number | null
  pre_close: number | null
  high: number | null
  low: number | null
  change: number | null
  pct_chg: number | null
  volume: number | null
  amount: number | null
  trade_date: string | null
  quote_time: string | null
  source: string
  turnover_rate?: number | null
  pe?: number | null
  pb?: number | null
  total_mv?: number | null
}

/* ---------- market overview ---------- */

export interface MarketIndex {
  code: string
  label: string
  close: number
  pct_chg: number
  amount: number
}

export type MarketBreadthKind = 'stock' | 'etf' | 'hk'

export interface MarketBreadth {
  up: number
  down: number
  flat: number
  amount: number
}

export interface MarketBreadthMap {
  stock?: MarketBreadth
  etf?: MarketBreadth
  hk?: MarketBreadth | null
}

export interface MarketLimit {
  up: number
  down: number
  date: string | null
  first?: number
  second?: number
  third?: number
  height?: number
  broken?: number
  seal_rate?: number | null
}

export interface MarketNorth {
  date: string | null
  net: number | null
  hgt: number | null
  sgt: number | null
}

export interface MarketMoney {
  net_mf_amount: number | null
  date: string | null
}

export interface MarketSector {
  name: string
  pct_chg: number
  amount: number
}

export interface MarketSectorBucket {
  top: MarketSector[]
  bottom: MarketSector[]
  industries: number
}

export interface MarketSectors {
  top: MarketSector[]
  bottom: MarketSector[]
  date: string | null
  industries?: number
  level1?: MarketSectorBucket
  level2?: MarketSectorBucket
}

export interface MarketHotStock {
  code: string
  name: string
  rank: number
  pct_chg: number
  hot: number
}

/** ``GET /api/market/overview``；数据空库时后端只返回 as_of + empty。 */
export interface MarketDistributionBucket {
  label: string
  count: number
}

export interface MarketIndustryFlow {
  name: string
  net: number
}

export interface MarketIndustryMoney {
  date: string | null
  top: MarketIndustryFlow[]
  bottom: MarketIndustryFlow[]
}

export interface MarketOverview {
  as_of: string | null
  prev_date?: string | null
  indices?: MarketIndex[]
  breadth?: MarketBreadthMap
  limit?: MarketLimit
  money?: MarketMoney
  north?: MarketNorth
  sectors?: MarketSectors
  distribution?: MarketDistributionBucket[]
  money_by_industry?: MarketIndustryMoney
  hot?: MarketHotStock[]
  hot_date?: string | null
  empty?: boolean
}

/* ---------- stock detail ---------- */

export interface StockInfo {
  symbol: string
  ts_code: string
  name: string
  industry: string
  board: string
  asset_type: AssetType
  list_date: string
}

export interface StockDetailQuote {
  trade_date: string
  close: number
  pre_close: number
  pct_chg: number
  high: number
  low: number
  open: number
  volume: number
  amount: number
  pe_ttm: number | null
  pb: number | null
  ps_ttm: number | null
  dv_ttm: number | null
  total_mv: number | null
  circ_mv: number | null
  turnover_rate: number | null
  volume_ratio: number | null
}

export interface StockBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  c_adj: number
}

export type StockRange = '3m' | '6m' | '1y' | '3y' | 'all'
export type StockPeriod = 'day' | 'week' | 'month'

export interface StockHistory {
  symbol: string
  name: string
  span: StockRange
  period: StockPeriod
  bars: StockBar[]
}

export interface StockFinancial {
  ann_date: string
  end_date: string
  roe: number | null
  net_margin: number | null
  gross_margin: number | null
  debt_ratio: number | null
  revenue_yoy: number | null
  profit_yoy: number | null
}

export interface IntradayPoint {
  time: string
  price: number
  avg: number | null
  volume: number
  amount: number
}

export interface IntradayData {
  symbol: string
  name: string
  trade_date: string
  pre_close: number | null
  source: string
  points: IntradayPoint[]
}

export interface StockRunBrief {
  id: number
  started_at: string
  status: string
  decision: string
  position_pct: number
}

/** ``GET /api/stocks/{symbol}/detail`` 的整包。 */
export interface StockDetailData {
  info: StockInfo
  quote: StockDetailQuote
  chart: StockBar[]
  factors: Record<string, unknown> | null
  financial: StockFinancial | null
  intraday: IntradayData | null
  runs: StockRunBrief[]
  watching: boolean
}

export interface Candle {
  trade_date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  amount: number | null
  source?: string
}

export interface Indicators {
  ma5: (number | null)[]
  ma10: (number | null)[]
  ma20: (number | null)[]
  ma60: (number | null)[]
  macd: { dif: (number | null)[]; dea: (number | null)[]; hist: (number | null)[] }
  rsi14: (number | null)[]
  boll: { mid: (number | null)[]; upper: (number | null)[]; lower: (number | null)[] }
  latest: Record<string, number | null>
}

export interface ChartData {
  symbol: string
  period: 'day' | 'week' | 'month'
  candles: Candle[]
  indicators: Indicators
}

export interface Fundamentals {
  symbol: string
  name: string | null
  asset_type: AssetType
  report_date: string | null
  revenue: number | null
  net_profit: number | null
  roe: number | null
  gross_margin: number | null
  debt_ratio: number | null
  eps: number | null
  bps: number | null
  pe: number | null
  pb: number | null
  total_mv: number | null
  source: string
}

export interface ResearchNote {
  id: number
  symbol: string
  model: string | null
  content: string
  created_at: string
}

export interface ResearchResponse {
  symbol: string
  name: string | null
  model: string
  note: ResearchNote
  snapshot: {
    quote: Quote
    indicators: Record<string, number | null>
    fundamentals: Fundamentals
  }
}

export interface Meta {
  app: string
  version: string
  model: string
  llm_configured: boolean
  markets: string[]
  data_sources: string[]
  disclaimer: string
}

export type AgentEventType =
  | 'stage'
  | 'run_start'
  | 'node_start'
  | 'node_end'
  | 'run_end'
  | 'error'

export interface AgentUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

export interface AgentEvent {
  type: AgentEventType
  node?: string
  label?: string
  status?: 'ok' | 'error'
  message?: string
  payload?: any
  report?: string
  usage?: AgentUsage
  duration_ms?: number
  run_id?: number | null
  symbol?: string
  name?: string
  as_of?: string
  model?: string
  rounds?: number
  /** run_start 事件带：本次推演加载的 `skill_id@version`。 */
  skill_version?: string
  verdict?: Record<string, any> | null
  proposal?: Record<string, any> | null
  consensus?: Record<string, any> | null
  errors?: string[]
}

export interface AgentNodeState {
  node: string
  label: string
  status: 'running' | 'ok' | 'error'
  message?: string
}

export interface AgentRunSummary {
  id: number
  symbol: string
  name: string
  status: string
  model: string
  rounds: number
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  usage: AgentUsage
  verdict: Record<string, any> | null
  error: string | null
  report?: string | null
}

export interface ScreenField {
  name: string
  label: string
  kind: 'money' | 'ratio' | 'count' | 'rank' | 'text'
  unit: string
  description: string
}

export interface ScreenQueryPayload {
  title?: string
  explanation?: string
  filters: Array<{ field: string; op: string; value: any }>
  universe?: Record<string, any>
  sort?: Array<{ field: string; desc: boolean }>
  limit?: number
}

/** 预设策略：text 给人看，后端另有一份等价 query。 */
export interface PresetItem {
  id: string
  label: string
  text: string
  horizon?: StrategyHorizon
}

export interface PresetRankRow {
  id: string
  label: string
  text: string
  rank: number
  status: string
  horizon?: StrategyHorizon
  gate?: { passed: boolean; failures: string[] }
  error?: string
  start?: string | null
  end?: string | null
  periods?: number | null
  total_return_pct?: number | null
  cagr_pct?: number | null
  excess_cagr_pct?: number | null
  max_drawdown_pct?: number | null
  hit_rate_pct?: number | null
  exposure_pct?: number | null
}

export interface PresetBatch {
  id?: number
  created_at: string
  start: string | null
  end: string | null
  rebalance: number
  top_n: number
  rows: PresetRankRow[]
}

/** 策略库一行：状态 + 1年/2年成绩 + 4 周趋势。 */
export interface StrategyMetrics {
  periods?: number | null
  total_return_pct?: number | null
  cagr_pct?: number | null
  excess_cagr_pct?: number | null
  max_drawdown_pct?: number | null
  hit_rate_pct?: number | null
  exposure_pct?: number | null
  trades?: number | null
}

export type StrategyStatus = 'template' | 'candidate' | 'watch' | 'champion' | 'retired' | 'paused'

export type StrategyHorizon = 'short' | 'mid' | 'long'

export interface StrategyRow {
  id: string
  name: string
  status: StrategyStatus
  source?: string | null
  notes?: string | null
  created_at?: string | null
  promoted_at?: string | null
  retired_at?: string | null
  retire_reason?: string | null
  invalidation?: string | null
  provenance?: { origin?: string; rationale?: string; verified?: boolean; note?: string } | null
  query: ScreenQueryPayload
  params?: Record<string, number>
  horizon?: StrategyHorizon
  year1?: StrategyMetrics | null
  year2?: StrategyMetrics | null
  trend?: number[]
  last_checked?: string | null
  rank?: number | null
}

export interface StrategyList {
  count: number
  strategies: StrategyRow[]
}

export interface PresetPayload {
  presets: PresetItem[]
  batch: PresetBatch | null
  rank_field: string
}

export interface ScreenResult {
  as_of: string
  total_matched: number
  returned: number
  columns: string[]
  rows: Array<Record<string, any>>
  query?: ScreenQueryPayload
}

export interface BacktestDefaults {
  rebalance: number
  top_n: number
  initial_cash: number
  slippage_bps: number
  benchmark: string
  lookback_years: number
}

export interface BacktestLimits {
  top_n: number
  rebalance: number
  max_years: number
  min_window_days: number
}

export interface BacktestPanelBounds {
  start: string | null
  end: string | null
  symbols: number
}

export interface BacktestFees {
  commission_rate: number
  commission_min: number
  stamp_rate: number
  transfer_rate: number
  lot: number
  settlement: string
}

export interface BacktestMeta {
  defaults: BacktestDefaults
  limits: BacktestLimits
  rebalance_choices: number[]
  unsupported_fields: Record<string, string>
  supported_fields: string[]
  panel: BacktestPanelBounds
  fees: BacktestFees
  screener_fields: number
}

export interface BacktestRequest {
  query: ScreenQueryPayload
  start?: string
  end?: string
  rebalance?: number
  top_n?: number
  initial_cash?: number
  slippage_bps?: number
  benchmark?: string
  title?: string
}

export interface BacktestCoverage {
  rows: number
  dates: number
  shares: Record<string, number>
}

export interface BacktestPeriodBrief {
  index: number
  entry_date: string
  return_pct: number
  excess_pct: number
}

export interface BacktestMetrics {
  start: string
  end: string
  trading_days: number
  total_return_pct: number
  cagr_pct: number
  benchmark_return_pct: number
  benchmark_cagr_pct: number
  excess_cagr_pct: number
  volatility_pct: number
  sharpe: number
  max_drawdown_pct: number
  hit_rate_pct: number
  avg_period_pct: number
  avg_excess_pct: number
  periods: number
  empty_periods: number
  trades: number
  fees: number
  exposure_pct: number
  avg_positions: number
  best_period: BacktestPeriodBrief | null
  worst_period: BacktestPeriodBrief | null
}

export interface BacktestNavPoint {
  trade_date: string
  nav: number
  benchmark: number | null
  invested_ratio: number
}

export interface BacktestHolding {
  symbol: string
  name: string
  weight: number
  entry_price: number | null
  exit_price: number | null
  return_pct: number
  exit_date: string
}

export interface BacktestPeriod {
  index: number
  signal_date: string
  entry_date: string
  exit_date: string
  picks: number
  positions: number
  return_pct: number
  benchmark_pct: number
  excess_pct: number
  cash_weight: number
  fees: number
  note: string
  holdings: BacktestHolding[]
}

export type BacktestTradeSide = 'buy' | 'sell' | 'mark'

export interface BacktestTrade {
  date: string
  symbol: string
  name: string
  side: BacktestTradeSide
  shares: number
  price: number | null
  fee: number
  period: number
}

export interface BacktestSpec {
  title: string
  start: string
  end: string
  rebalance: number
  top_n: number
  initial_cash: number
  slippage_bps: number
  benchmark: string
  query: ScreenQueryPayload
}

export interface BacktestResult {
  id?: number
  created_at?: string | null
  title: string
  spec: BacktestSpec
  coverage?: BacktestCoverage
  metrics: BacktestMetrics
  nav: BacktestNavPoint[]
  periods: BacktestPeriod[]
  trades: BacktestTrade[]
  warnings: string[]
}

export interface BacktestRunSummary {
  id: number
  title: string
  created_at: string
  spec: {
    start: string | null
    end: string | null
    rebalance: number | null
    top_n: number | null
  }
  metrics: {
    total_return_pct: number | null
    cagr_pct: number | null
    benchmark_return_pct: number | null
    excess_cagr_pct: number | null
    max_drawdown_pct: number | null
    sharpe: number | null
    hit_rate_pct: number | null
    periods: number | null
    trades: number | null
  }
}

export type TopicScope = '大盘' | '行业' | '题材' | '情绪' | '其他'
export type TopicVerdict = '看多' | '中性' | '看空' | '无法评估'

export interface TopicMeta {
  as_of: string | null
  model: string
  llm_available: boolean
  examples: string[]
  industries: Array<{ name: string; stocks: number }>
}

export interface TopicBreadth {
  stocks: number
  up: number
  down: number
  flat: number
  up_ratio: number
  above_ma20_ratio: number
  above_ma60_ratio: number
}

export interface TopicTurnover {
  date: string
  amount_yi: number
  amount_20d_avg_yi: number
  ratio_to_20d_avg: number | null
  last5_yi: number[]
}

export interface TopicLimits {
  date: string
  limit_up: number
  limit_down: number
  limit_up_5d_avg: number
  limit_down_5d_avg: number
}

export interface TopicMargin {
  date: string
  balance_yi: number
  chg_20d_pct: number | null
  stocks: number
  skipped_partial_days: string[]
}

export interface TopicIndex {
  name: string
  code: string
  date: string
  close: number
  ret_1d: number | null
  ret_5d: number | null
  ret_20d: number | null
  ret_60d: number | null
}

export interface TopicHotConcept {
  name: string
  limit_up_n: number
  stat: string | null
  days: number
  pct_chg: number | null
  date: string
}

export interface TopicMarket {
  indices: TopicIndex[]
  breadth: TopicBreadth
  turnover: TopicTurnover | null
  limits: TopicLimits | null
  margin: TopicMargin | null
  hot_concepts: TopicHotConcept[]
}

export interface TopicSummary {
  as_of: string
  breadth: TopicBreadth | null
  turnover: TopicTurnover | null
  limits: TopicLimits | null
  margin: TopicMargin | null
  matched_industries: string[]
  matched_concepts: string[]
  industry_count: number
  concept_count: number
}

export interface TopicIndustryRow {
  name: string
  stocks: number
  ret20: number | null
  up_ratio_5d: number | null
  mf_net_20d: number | null
}

export interface TopicSwRow {
  name: string
  code: string
  date: string
  pct_chg: number | null
  pe: number | null
  pb: number | null
}

export interface TopicStockBrief {
  symbol: string
  name: string
  close: number | null
  ret5: number | null
  ret20: number | null
  amount_yi: number | null
}

export interface TopicIndustryStats {
  stocks: number | null
  ret5: number | null
  ret20: number | null
  ret60: number | null
  up_ratio_5d: number | null
  pe_median: number | null
  mf_net_20d: number | null
}

export interface TopicIndustryDetail {
  industry: string
  stats: TopicIndustryStats
  leaders: TopicStockBrief[]
  laggards: TopicStockBrief[]
  count: number
}

export interface TopicContext {
  as_of: string
  matched: { industries: string[]; concepts: string[] }
  data_gaps: string[]
  summary: TopicSummary
  market: TopicMarket
  industry_detail?: TopicIndustryDetail[] | null
  industries: TopicIndustryRow[]
  sw_l1: TopicSwRow[]
  data_dates: Record<string, string | null>
}

export interface TopicSection {
  heading: string
  body: string
}

export interface TopicReportBody {
  title: string
  scope: string
  verdict: string
  confidence: number
  summary: string
  key_points: string[]
  drivers: string[]
  risks: string[]
  watching: string[]
  data_gaps: string[]
  sections: TopicSection[]
}

export interface TopicUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

/**
 * A saved report as returned by ``GET /topics/reports/{id}``.
 * The detail endpoint omits the generation-only fields, so they are optional.
 */
export interface TopicReportDetail {
  id: number
  topic: string
  title: string
  scope: TopicScope
  verdict: TopicVerdict
  confidence: number
  model: string
  as_of: string
  report: TopicReportBody
  markdown: string
  prompt_tokens: number
  completion_tokens: number
  created_at: string
  usage?: TopicUsage
  context_summary?: TopicSummary
  data_gaps?: string[]
}

/** The freshly generated report: the detail fields plus generation metadata. */
export interface TopicReport extends TopicReportDetail {
  usage: TopicUsage
  context_summary: TopicSummary
  data_gaps: string[]
}

export interface TopicSentimentPoint {
  date: string
  score: number
  up_ratio: number
  above_ma20_ratio: number
  limit_up: number
  limit_down: number
  amount_yi: number
  amount_ratio: number | null
  sector_up_ratio: number
}

export interface TopicSentiment {
  as_of: string
  score: number
  prev_score: number | null
  change: number | null
  change_5d: number | null
  stage: string
  components: {
    up_ratio?: number
    above_ma20_ratio?: number
    limit_up?: number
    limit_down?: number
    amount_yi?: number
    amount_ratio?: number | null
    sector_up_ratio?: number
    industries?: number
  }
  points: TopicSentimentPoint[]
}

export interface TopicTodayHistoryItem {
  id: number
  title: string
  as_of: string
  verdict: string
  confidence: number
  model: string
  created_at: string
}

export interface DailySignalReview {
  signal: string
  /** prediction 计入命中率；trigger 只看是否触发；legacy 是旧格式，两者都不计。 */
  kind?: 'prediction' | 'trigger' | 'legacy'
  metric?: string
  op?: string
  value?: number | null
  actual: number | null
  status: '兑现' | '落空' | '已触发' | '未触发' | '无法判定'
}

export interface DailyReview {
  as_of: string
  evaluated_as_of: string | null
  signals: DailySignalReview[]
  hits?: number
  total?: number
  trigger_fired?: number
  trigger_total?: number
  status: '已判定' | '待验证'
}

export interface DailyReviewPayload {
  reviews: DailyReview[]
  accuracy: {
    hits: number
    total: number
    rate: number | null
    prediction?: { hits: number; total: number; rate: number | null }
    trigger?: { fired: number; total: number; rate: number | null }
    /** 旧格式观察条数：只显示，不进任何分母。 */
    legacy?: { total: number }
  }
}

export interface TopicToday {
  report: TopicReportDetail | null
  cached: boolean
  as_of: string | null
  stale: boolean
  sentiment?: TopicSentiment | null
  history?: TopicTodayHistoryItem[]
  review?: DailyReviewPayload
}

export interface TopicReportSummary {
  id: number
  topic: string
  title: string
  scope: TopicScope
  verdict: TopicVerdict
  confidence: number
  model: string
  as_of: string
  created_at: string
}

/* ---------- factor mining ---------- */

export interface FactorPanelBounds {
  start: string | null
  end: string | null
}

export interface FactorDefaults {
  start_years: number
  rebalance: number
  horizon: number
}

export interface FactorLimits {
  max_years: number
  rebalance: number[]
  horizons: number[]
  min_sections: number
}

/** 指标的中文口径说明，原样展示。 */
export interface FactorMetricNotes {
  ic: string
  icir: string
  t_stat: string
  spread: string
}

/** 因子库里的一个因子。 */
export interface FactorLibraryItem {
  name: string
  label: string
  category: string
  direction: number
  expected: string
  note: string
}

/** ``GET /factors/library`` 的整包元数据。 */
export interface FactorMeta {
  panel: FactorPanelBounds
  defaults: FactorDefaults
  limits: FactorLimits
  factors: FactorLibraryItem[]
  metrics: FactorMetricNotes
}

export interface FactorSpec {
  start: string
  end: string
  rebalance: number
  horizon: number
  factors: string[]
}

export interface FactorRequest {
  start?: string
  end?: string
  rebalance?: number
  horizon?: number
  factors?: string[]
}

export interface FactorQuantile {
  q: number
  mean_pct: number
  median_pct: number
  avg_names: number
  periods: number
}

export interface FactorIcPoint {
  date: string
  ic: number
}

export interface FactorResultItem {
  name: string
  label: string
  category: string
  direction: number
  expected: string
  /** 因子有值的股票占比，0..1。 */
  coverage: number
  distinct: number
  /** false = 覆盖率过低，排名靠后。 */
  usable: boolean
  horizon: number
  n_dates: number
  ic_mean: number
  ic_std: number
  icir: number
  t_stat: number
  /** 正 IC 占比，百分比（0..100）。 */
  positive_ratio: number
  ic_series: FactorIcPoint[]
  /** 可能是空数组：取值过少的因子不做分层。 */
  quantiles: FactorQuantile[]
  /** Q5 − Q1 的平均前瞻收益，百分点；无法分层时为 null。 */
  spread_pct: number | null
  /** 实测方向是否与先验一致；无先验方向或样本不足时为 null。 */
  aligned: boolean | null
}

export interface FactorRunResult {
  config: FactorSpec
  /** 截面数量。 */
  sections: number
  /** 有效期数。 */
  evaluated_dates: number
  factors: FactorResultItem[]
  id: number
  created_at: string
}

export interface FactorRunSummary {
  id: number
  created_at: string
  spec: FactorSpec
  sections: number
  evaluated_dates: number
  top_factor: { name: string; label: string; icir: number; ic_mean: number }
}

/** ``GET /factors/runs/{id}`` 与 ``POST /factors/run`` 返回同一结构。 */
export type FactorRunDetail = FactorRunResult

/* ---------- operations ---------- */

export type OpsKind = '推演' | '主题' | '回测' | '成交'

/** 账本里的一行：四类记录被归一成同一组展示字段。 */
export interface OpsEntry {
  kind: OpsKind
  ref: string
  id: number
  title: string
  symbol: string
  as_of: string
  /** 推演=风控裁定；主题=范围|裁定；回测=超额年化…；成交=买入/卖出。 */
  stance: string
  /** 推演=最终仓位%；主题=信心；回测=区间收益%；成交=金额。 */
  score: number | null
  review: string
  review_detail: string
  /** 这条记录派生自哪次推演（回测/主题/成交可能有，推演填自己的 id）。 */
  source_run_id?: number | null
  tokens: number
  status: string
  created_at: string
}

export interface OpsLedger {
  count: number
  entries: OpsEntry[]
}

export interface OpsCounts {
  runs: number
  topics: number
  backtests: number
  trades: number
}

/** ``GET /ledger/review`` 与 ``summary.reviews`` 的结构。 */
export interface OpsReviews {
  runs: number
  reviews: number
  covered_pct: number
  runs_without_review: number
  pending: number
  judged: number
  correct: number
  wrong: number
  /** 没有已判定样本时为 null。 */
  hit_rate_pct: number | null
  last_refreshed_at: string | null
}

export interface OpsTokenTotals {
  agent: number
  topic: number
  total: number
}

export interface OpsChainRun {
  id: number
  symbol: string
  name: string
  status: string
  model: string
  started_at: string
  finished_at: string | null
  decision: string | null
  final_position_pct: number | null
  tokens: number
}

export interface OpsChainBacktest {
  id: number
  title: string
  total_return_pct: number | null
  excess_cagr_pct: number | null
  max_drawdown_pct: number | null
  periods: number | null
  created_at: string
}

export interface OpsChainTopic {
  id: number
  topic: string
  verdict: string
  confidence: number
  created_at: string
}

export interface OpsChainTrade {
  id: number
  symbol: string
  side: string
  shares: number
  price: number
  fee: number
  amount: number
  trade_date: string
}

export interface OpsChain {
  run: OpsChainRun
  review: {
    outcome?: string
    entry_price?: number
    d20_return?: number | null
  } | null
  backtests: OpsChainBacktest[]
  topics: OpsChainTopic[]
  trades: OpsChainTrade[]
}

export interface OpsSummary {
  counts: OpsCounts
  reviews: OpsReviews
  tokens: OpsTokenTotals
}

/** 记忆检索（goat 3.1）：一条历史结论或历史报告。 */
export interface MemoryItem {
  kind: '推演' | '主题'
  ref_id: number
  symbol: string | null
  title: string
  as_of: string | null
  decision: string
  outcome: string | null
  group: string
  warning?: boolean
}

export interface MemoryStats {
  indexed: number
  runs: number
  topics: number
  judged: number
  oldest: string | null
  newest: string | null
  updated_at: string
}

export type ExperienceStatus = 'candidate' | 'active' | 'retired'

export interface ExperienceItem {
  id: number
  fingerprint: string
  scope: string
  scope_key: string | null
  source: string
  trigger: Record<string, number | string>
  lesson: string
  action: Record<string, unknown>
  confidence: number
  status: ExperienceStatus
  status_reason: string | null
  status_changed_at: string | null
  graduated_to: string | null
  graduated_at: string | null
  as_of: string | null
  evidence_count: number
  can_activate: boolean
  created_at: string
  updated_at: string
  version: number
}

export interface ExperienceEvidence {
  kind: string
  ref_id: number
  symbol?: string
  date?: string | null
  metric?: number | null
  excess?: number | null
  outcome?: string
}

export interface ExperienceRecall {
  run_id: number
  score: number
  status: string
  injected: number
  created_at: string
}

export interface ExperienceDetail extends ExperienceItem {
  evidence: ExperienceEvidence[]
  recalls: ExperienceRecall[]
}

export interface ExperienceStats {
  total: number
  by_status: Record<string, number>
  by_scope: Record<string, number>
  shadow_recalls: number
  injected_recalls: number
  updated_at: string
}

export interface ExperienceEvaluation {
  preflight: {
    active_cards: number
    judged_runs: number
    pairable_runs: number
    shadow_recalls: number
    min_judged: number
    min_pairable: number
  }
  shadow: {
    recalls: number
    judged: number
    hits: number
    false_alarm: number
    pending: number
    unknown: number
    precision_pct: number | null
    coverage_pct: number | null
    runs_with_recall: number
    judged_runs: number
    cards: Array<{
      id: number
      lesson: string
      recalls: number
      judged: number
      hits: number
      precision_pct: number | null
    }>
  }
}

/** ``POST /ledger/review/refresh``：复盘状态加上本次写入统计。 */
export interface OpsReviewRefresh extends OpsReviews {
  refreshed: { runs: number; written: number; skipped: number }
}

export interface OpsUsagePoint {
  date: string
  runs: number
  topics: number
  backtests: number
  factors: number
  tokens: number
  calls: number
  seconds: number
}

/** ``GET /usage?days=``：series 按日期倒序。 */
export interface OpsUsage {
  days: number
  total_tokens: number
  total_tasks: number
  series: OpsUsagePoint[]
}

export type OpsTableStatus = 'ok' | 'stale' | 'empty' | 'missing'

export interface OpsHealthTable {
  table: string
  label: string
  last_date: string | null
  rows: number
  lag_days: number | null
  status: OpsTableStatus
  expected_lag: number
}

export interface OpsPartialDay {
  table: string
  date: string
  rows: number
  expected: number
}

export interface OpsSyncLog {
  job: string
  status: string
  rows: number
  started_at: string
}

export interface OpsFingerprint {
  first_date: string | null
  as_of: string | null
  bars: number
  digest: string
  built_at: string
}

export interface OpsHealth {
  as_of: string
  checked_at: string
  ok: boolean
  tables: OpsHealthTable[]
  calendar?: { last: string | null; open_days_left: number }
  partial_days: OpsPartialDay[]
  recent_sync: OpsSyncLog[]
  issues: string[]
  fingerprint: OpsFingerprint
}

/** ``POST /data/repair``：一键补某张表最近 N 个交易日的缺口。 */
export interface OpsRepair {
  dataset: string
  label: string
  dates: string[]
  parts: number
  rows: number
  status: string
  errors: string[]
}

/* ---------- 全市场浏览 ---------- */

export type UniverseAsset = 'stock' | 'etf' | 'index' | 'hk'

export interface UniverseRow {
  symbol?: string
  code?: string
  name: string
  industry?: string | null
  board?: string | null
  category?: string | null
  close?: number | null
  pct_chg?: number | null
  amount?: number | null
  total_mv?: number | null
  pe_ttm?: number | null
  turnover_rate?: number | null
  ret20?: number | null
  fd_pead?: number | null
  date?: string | null
}

export interface UniverseResponse {
  asset: UniverseAsset
  as_of: string | null
  total: number
  page: number
  page_size: number
  rows: UniverseRow[]
  note?: string
}
