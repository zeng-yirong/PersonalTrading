export type AssetType = 'stock' | 'etf'

export interface WatchlistItem {
  symbol: string
  name: string
  asset_type: AssetType
  created_at?: string
}

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