import type {
  AgentRunSummary,
  BacktestMeta,
  BacktestRequest,
  BacktestResult,
  BacktestRunSummary,
  ChartData,
  FactorMeta,
  FactorRequest,
  FactorRunDetail,
  FactorRunResult,
  FactorRunSummary,
  Fundamentals,
  IntradayData,
  MarketOverview,
  MemoryItem,
  MemoryStats,
  Meta,
  OpsFingerprint,
  OpsHealth,
  PresetBatch,
  StrategyList,
  StrategyMetrics,
  StrategyRow,
  PresetPayload,
  OpsRepair,
  OpsKind,
  OpsLedger,
  OpsReviewRefresh,
  OpsChain,
  OpsReviews,
  OpsSummary,
  OpsUsage,
  Quote,
  ResearchNote,
  ResearchResponse,
  ScreenField,
  ScreenQueryPayload,
  ScreenResult,
  SearchResult,
  StockDetailData,
  StockHistory,
  StockPeriod,
  StockRange,
  TopicContext,
  TopicMeta,
  TopicReport,
  TopicReportDetail,
  TopicReportSummary,
  TopicToday,
  UniverseAsset,
  UniverseResponse,
  WatchlistItem,
  WatchlistMetric,
} from './types'
const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* ignore non-JSON errors */
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  meta: () => request<Meta>('/meta'),

  watchlist: () => request<WatchlistItem[]>('/watchlist'),
  watchlistMetrics: () => request<WatchlistMetric[]>('/watchlist/metrics'),
  addWatchlist: (symbol: string, name?: string) =>
    request<WatchlistItem>('/watchlist', {
      method: 'POST',
      body: JSON.stringify({ symbol, name }),
    }),
  removeWatchlist: (symbol: string) =>
    request<{ ok: boolean; symbol: string }>(`/watchlist/${symbol}`, { method: 'DELETE' }),

  search: (query: string) =>
    request<SearchResult[]>(`/search?q=${encodeURIComponent(query)}`),

  quote: (symbol: string) => request<Quote>(`/stocks/${symbol}/quote`),
  chart: (symbol: string, period: string, limit = 250) =>
    request<ChartData>(`/stocks/${symbol}/chart?period=${period}&limit=${limit}`),
  fundamentals: (symbol: string) => request<Fundamentals>(`/stocks/${symbol}/fundamentals`),

  marketOverview: () => request<MarketOverview>('/market/overview'),
  stockDetail: (symbol: string) =>
    request<StockDetailData>(`/stocks/${encodeURIComponent(symbol)}/detail`),
  stockIntraday: (symbol: string) =>
    request<IntradayData>(`/stocks/${encodeURIComponent(symbol)}/intraday`),
  stockHistory: (symbol: string, range: StockRange, period: StockPeriod = 'day') =>
    request<StockHistory>(
      `/stocks/${encodeURIComponent(symbol)}/history?range=${encodeURIComponent(range)}&period=${encodeURIComponent(period)}`,
    ),

  research: (symbol: string) =>
    request<ResearchResponse>(`/stocks/${symbol}/research`, { method: 'POST', body: '{}' }),
  notes: (symbol: string) => request<ResearchNote[]>(`/research-notes?symbol=${symbol}`),

  agentStatus: () =>
    request<{
      llm_available: boolean
      reason: string
      model: string
      debate_rounds: number
      panel_ready: boolean
    }>('/agents/status'),
  agentRuns: (symbol: string) =>
    request<{ count: number; runs: AgentRunSummary[] }>(
      `/agents/runs?symbol=${encodeURIComponent(symbol)}&limit=10`,
    ),
  agentRun: (id: number) => request<AgentRunSummary>(`/agents/runs/${id}`),
  agentStreamUrl: (symbol: string, rounds: number) =>
    `${BASE}/agents/analyze/${encodeURIComponent(symbol)}?rounds=${rounds}`,

  screenerFields: () =>
    request<{ count: number; fields: ScreenField[] }>('/screener/fields'),
  screenerMeta: () =>
    request<{ as_of: string; symbols: number; cached: boolean }>('/screener/meta'),
  screenerPresets: () => request<PresetPayload>('/backtest/presets'),
  strategies: (status = '') =>
    request<StrategyList>(`/strategies${status ? `?status=${status}` : ''}`),
  strategyStatus: (id: string, status: string, reason = '') =>
    request<StrategyRow>(`/strategies/${encodeURIComponent(id)}/status`, {
      method: 'POST',
      body: JSON.stringify({ status, reason }),
    }),
  strategyClone: (id: string) =>
    request<StrategyRow>(`/strategies/${encodeURIComponent(id)}/clone`, {
      method: 'POST',
      body: '{}',
    }),
  strategyRun: (id: string) =>
    request<{ strategy: StrategyRow; metrics: StrategyMetrics }>(
      `/strategies/${encodeURIComponent(id)}/run`,
      { method: 'POST', body: '{}' },
    ),
  presetsRun: (rebalance = 20, topN = 10) =>
    request<PresetBatch>(`/backtest/presets/run?rebalance=${rebalance}&top_n=${topN}`, {
      method: 'POST',
      body: '{}',
    }),
  screenerAsk: (text: string, limit = 30) =>
    request<ScreenResult>('/screener/ask', {
      method: 'POST',
      body: JSON.stringify({ text, limit }),
    }),
  screenerRun: (query: ScreenQueryPayload) =>
    request<ScreenResult>('/screener/run', {
      method: 'POST',
      body: JSON.stringify(query),
    }),

  backtestMeta: () => request<BacktestMeta>('/backtest/meta'),
  backtestRun: (payload: BacktestRequest) =>
    request<BacktestResult>('/backtest/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  backtestRuns: (limit = 20) =>
    request<{ count: number; runs: BacktestRunSummary[] }>(`/backtest/runs?limit=${limit}`),
  backtestRunDetail: (id: number) => request<BacktestResult>(`/backtest/runs/${id}`),

  factorLibrary: () => request<FactorMeta>('/factors/library'),
  factorRun: (payload: FactorRequest) =>
    request<FactorRunResult>('/factors/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  factorRuns: (limit = 20) =>
    request<{ count: number; runs: FactorRunSummary[] }>(`/factors/runs?limit=${limit}`),
  factorRunDetail: (id: number) => request<FactorRunDetail>(`/factors/runs/${id}`),

  topicMeta: () => request<TopicMeta>('/topics/meta'),
  topicToday: () => request<TopicToday>('/topics/today'),
  topicTodayRun: (force = false) =>
    request<TopicToday>('/topics/today', {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),
  topicContext: (topic: string) =>
    request<TopicContext>(`/topics/context?topic=${encodeURIComponent(topic)}`),
  topicResearch: (topic: string) =>
    request<TopicReport>('/topics/research', {
      method: 'POST',
      body: JSON.stringify({ topic }),
    }),
  topicReports: (limit = 20) =>
    request<{ count: number; reports: TopicReportSummary[] }>(
      `/topics/reports?limit=${limit}`,
    ),
  topicReport: (id: number) => request<TopicReportDetail>(`/topics/reports/${id}`),

  paperSummary: () => request<any>('/paper/summary'),
  paperPositions: () => request<{ count: number; positions: any[] }>('/paper/positions'),
  paperNav: () => request<{ count: number; series: any[] }>('/paper/nav'),
  paperTrades: (limit = 50) => request<{ count: number; trades: any[] }>(`/paper/trades?limit=${limit}`),
  paperFromRun: (runId: number, fraction = 1) =>
    request<any>(`/paper/from-run/${runId}`, {
      method: 'POST',
      body: JSON.stringify({ fraction }),
    }),
  paperReviews: (limit = 20) =>
    request<{ count: number; reviews: any[] }>(`/paper/reviews?limit=${limit}`),
  paperReviewStats: () => request<any>('/paper/review/stats'),
  paperRefreshReviews: () =>
    request<any>('/paper/review/refresh', { method: 'POST', body: '{}' }),

  opsLedger: (limit = 20, kind: OpsKind | '' = '') =>
    request<OpsLedger>(
      `/ledger?limit=${limit}${kind ? `&kind=${encodeURIComponent(kind)}` : ''}`,
    ),
  opsSummary: () => request<OpsSummary>('/ledger/summary'),
  opsChain: (runId: number) =>
    request<OpsChain>(`/ledger/chain/${encodeURIComponent(String(runId))}`),
  opsReview: () => request<OpsReviews>('/ledger/review'),
  opsReviewRefresh: (limit = 200) =>
    request<OpsReviewRefresh>(`/ledger/review/refresh?limit=${limit}`, {
      method: 'POST',
      body: '{}',
    }),
  opsUsage: (days = 14) => request<OpsUsage>(`/usage?days=${days}`),
  opsHealth: () => request<OpsHealth>('/data/health'),
  memory: (params: { symbol?: string; q?: string; asOf?: string; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.symbol) query.set('symbol', params.symbol)
    if (params.q) query.set('q', params.q)
    if (params.asOf) query.set('as_of', params.asOf)
    if (params.limit) query.set('limit', String(params.limit))
    return request<MemoryItem[]>(`/memory?${query.toString()}`)
  },
  memoryStats: () => request<MemoryStats>('/memory/stats'),
  memoryRebuild: () => request<{ indexed: number }>('/memory/rebuild', { method: 'POST' }),
  opsRepair: (dataset: string, days = 5) =>
    request<OpsRepair>(
      `/data/repair?dataset=${encodeURIComponent(dataset)}&days=${days}`,
      { method: 'POST', body: '{}' },
    ),
  indexDetail: (code: string) =>
    request<StockDetailData>(`/index/${encodeURIComponent(code)}/detail`),
  indexHistory: (code: string, range: StockRange, period: StockPeriod = 'day') =>
    request<StockHistory>(
      `/index/${encodeURIComponent(code)}/history?range=${encodeURIComponent(range)}&period=${encodeURIComponent(period)}`,
    ),
  universeList: (
    asset: UniverseAsset,
    params: { q?: string; sort?: string; order?: string; page?: number; page_size?: number } = {},
  ) => {
    const search = new URLSearchParams({ asset })
    if (params.q) search.set('q', params.q)
    if (params.sort) search.set('sort', params.sort)
    if (params.order) search.set('order', params.order)
    if (params.page) search.set('page', String(params.page))
    if (params.page_size) search.set('page_size', String(params.page_size))
    return request<UniverseResponse>(`/market/universe/list?${search.toString()}`)
  },
  opsFingerprint: () => request<OpsFingerprint>('/data/fingerprint'),
}