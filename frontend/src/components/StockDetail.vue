<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import type {
  ChartData,
  IntradayData,
  StockBar,
  StockDetailData,
  StockFinancial,
  StockRange,
} from '../types'
import IntradayChart from './IntradayChart.vue'
import PriceChart from './PriceChart.vue'

const props = defineProps<{
  symbol: string
  name: string
  watching: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'toggle-watch'): void
  (e: 'research'): void
}>()

type ChartTab = 'intraday' | 'day'

const RANGES: { key: StockRange; label: string }[] = [
  { key: '3m', label: '3月' },
  { key: '6m', label: '6月' },
  { key: '1y', label: '1年' },
  { key: '3y', label: '3年' },
  { key: 'all', label: '全部' },
]

interface FactorRow {
  k: string
  v: string
  tone: string
}

const detail = ref<StockDetailData | null>(null)
const intraday = ref<IntradayData | null>(null)
const loading = ref(false)
const error = ref('')
const tab = ref<ChartTab>('intraday')
const span = ref<StockRange>('1y')
const historyBars = ref<StockBar[]>([])
const historyLoading = ref(false)
let token = 0

function messageOf(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc)
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
}

function pct(value: number | null | undefined, sign = true): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const prefix = sign && value > 0 ? '+' : ''
  return `${prefix}${value.toFixed(2)}%`
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)} 万亿`
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`
  return value.toFixed(0)
}

/** 资金流字段是万元口径，固定折算成亿元，避免长数字撑破因子格。 */
function wanToYi(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value / 1e4).toFixed(2)} 亿`
}

function volumeText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const hands = value / 100
  if (hands >= 1e4) return `${(hands / 1e4).toFixed(2)} 万手`
  return `${hands.toFixed(0)} 手`
}

function fmtTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function movingAverage(values: number[], window: number): (number | null)[] {
  const output: (number | null)[] = []
  let sum = 0
  for (let index = 0; index < values.length; index += 1) {
    sum += values[index]
    if (index >= window) sum -= values[index - window]
    output.push(index >= window - 1 ? sum / window : null)
  }
  return output
}

const isEtf = computed(() => detail.value?.info.asset_type === 'etf')

const quote = computed(() => detail.value?.quote ?? null)

const change = computed(() => {
  const row = quote.value
  if (!row) return null
  return row.close - row.pre_close
})

const dayChart = computed<ChartData | null>(() => {
  const rows = historyBars.value.length ? historyBars.value : detail.value?.chart ?? []
  if (!rows.length) return null
  const closes = rows.map((row) => row.close)
  return {
    symbol: detail.value?.info.symbol ?? props.symbol,
    period: 'day',
    candles: rows.map((row) => ({
      trade_date: row.date,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: row.volume,
      amount: null,
    })),
    indicators: {
      ma5: movingAverage(closes, 5),
      ma10: movingAverage(closes, 10),
      ma20: movingAverage(closes, 20),
      ma60: movingAverage(closes, 60),
      macd: { dif: [], dea: [], hist: [] },
      rsi14: [],
      boll: { mid: [], upper: [], lower: [] },
      latest: {},
    },
  }
})

const metrics = computed(() => {
  const row = quote.value
  if (!row) return []
  return [
    { k: '今开', v: num(row.open), tone: tone(row.open - row.pre_close) },
    { k: '最高', v: num(row.high), tone: tone(row.high - row.pre_close) },
    { k: '最低', v: num(row.low), tone: tone(row.low - row.pre_close) },
    { k: '昨收', v: num(row.pre_close), tone: 'flat' },
    { k: '成交量', v: volumeText(row.volume), tone: 'flat' },
    { k: '成交额', v: money(row.amount), tone: 'flat' },
    { k: '换手率', v: pct(row.turnover_rate, false), tone: 'flat' },
    { k: '量比', v: num(row.volume_ratio), tone: 'flat' },
    { k: '总市值', v: money(row.total_mv), tone: 'flat' },
    { k: '流通市值', v: money(row.circ_mv), tone: 'flat' },
    { k: 'PE(TTM)', v: num(row.pe_ttm), tone: 'flat' },
    { k: 'PB', v: num(row.pb), tone: 'flat' },
    { k: 'PS(TTM)', v: num(row.ps_ttm), tone: 'flat' },
    { k: '股息率', v: pct(row.dv_ttm, false), tone: 'flat' },
  ]
})

function factorNum(key: string): number | null {
  const raw = detail.value?.factors?.[key]
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null
}

const factorRows = computed<FactorRow[]>(() => {
  if (!detail.value?.factors) return []
  return [
    { k: '5日收益', v: pct(factorNum('ret5')), tone: tone(factorNum('ret5')) },
    { k: '20日收益', v: pct(factorNum('ret20')), tone: tone(factorNum('ret20')) },
    { k: '60日收益', v: pct(factorNum('ret60')), tone: tone(factorNum('ret60')) },
    { k: '距 MA20', v: pct(factorNum('gap_ma20')), tone: tone(factorNum('gap_ma20')) },
    { k: '距 MA60', v: pct(factorNum('gap_ma60')), tone: tone(factorNum('gap_ma60')) },
    {
      k: '52周回撤',
      v: pct(factorNum('drawdown_52w')),
      tone: tone(factorNum('drawdown_52w')),
    },
    { k: 'RSI12', v: num(factorNum('rsi12'), 1), tone: 'flat' },
    { k: 'MACD 柱', v: num(factorNum('macd_hist'), 3), tone: tone(factorNum('macd_hist')) },
    { k: '布林位置', v: num(factorNum('boll_pos')), tone: 'flat' },
    { k: '换手率', v: pct(factorNum('turnover_rate'), false), tone: 'flat' },
    { k: '量比', v: num(factorNum('volume_ratio')), tone: 'flat' },
    {
      k: '20日主力净流入',
      v: wanToYi(factorNum('mf_net_20d')),
      tone: tone(factorNum('mf_net_20d')),
    },
    { k: 'PE(TTM)', v: num(factorNum('pe_ttm')), tone: 'flat' },
    { k: 'PB', v: num(factorNum('pb')), tone: 'flat' },
    { k: 'ROE', v: pct(factorNum('roe'), false), tone: 'flat' },
    { k: '股息率', v: pct(factorNum('dv_ttm'), false), tone: 'flat' },
  ]
})

const financial = computed<StockFinancial | null>(() => detail.value?.financial ?? null)

const finRows = computed(() => {
  const row = financial.value
  if (!row) return []
  return [
    { k: 'ROE', v: pct(row.roe, false), tone: 'flat' },
    { k: '净利率', v: pct(row.net_margin, false), tone: 'flat' },
    { k: '毛利率', v: pct(row.gross_margin, false), tone: 'flat' },
    { k: '资产负债率', v: pct(row.debt_ratio, false), tone: 'flat' },
    { k: '营收同比', v: pct(row.revenue_yoy), tone: tone(row.revenue_yoy) },
    { k: '净利同比', v: pct(row.profit_yoy), tone: tone(row.profit_yoy) },
  ]
})

const runs = computed(() => detail.value?.runs ?? [])

async function loadHistory() {
  if (!props.symbol) return
  historyLoading.value = true
  try {
    const payload = await api.stockHistory(props.symbol, span.value)
    historyBars.value = payload.bars
  } catch {
    historyBars.value = []
  } finally {
    historyLoading.value = false
  }
}

watch(span, () => {
  void loadHistory()
})

watch(tab, (value) => {
  if (value === 'day' && !historyBars.value.length) void loadHistory()
})

async function load(symbol: string) {
  const current = ++token
  loading.value = true
  error.value = ''
  detail.value = null
  intraday.value = null
  historyBars.value = []
  try {
    const data = await api.stockDetail(symbol)
    if (current !== token) return
    detail.value = data
    intraday.value = data.intraday
    loading.value = false
    if (tab.value === 'day') void loadHistory()
    try {
      const minute = await api.stockIntraday(symbol)
      if (current === token && minute.points.length) intraday.value = minute
    } catch {
      /* 详情里的 intraday 已经够用，独立端点失败不阻断详情 */
    }
  } catch (exc) {
    if (current === token) error.value = `个股详情加载失败：${messageOf(exc)}`
  } finally {
    if (current === token) loading.value = false
  }
}

watch(
  () => props.symbol,
  (symbol) => {
    tab.value = 'intraday'
    void load(symbol)
  },
  { immediate: true },
)
</script>

<template>
  <div class="detail-stack">
    <section class="panel detail-head">
      <div class="detail-top">
        <button type="button" class="back-btn" @click="emit('close')">
          <span aria-hidden="true">←</span>
          返回
        </button>
        <span v-if="loading" class="loading">加载中…</span>
        <span v-else-if="detail" class="badge">详情已载入</span>
        <span class="sub-meta" style="margin: 0">Esc 关闭</span>
      </div>

      <div class="detail-title-row">
        <div>
          <div class="detail-name">
            <h2>{{ detail?.info.name || name }}</h2>
            <span class="detail-code mono">{{ detail?.info.symbol || symbol }}</span>
            <span class="badge" :class="{ etf: isEtf }">
              {{ isEtf ? 'ETF' : 'A股' }}
            </span>
            <span v-if="detail?.info.board" class="badge">{{ detail.info.board }}</span>
            <span v-if="detail?.info.industry" class="badge">{{ detail.info.industry }}</span>
          </div>
          <div class="detail-tags">
            <span v-if="detail?.info.ts_code" class="badge mono">{{ detail.info.ts_code }}</span>
            <span v-if="detail?.info.list_date" class="sub-meta" style="margin: 0">
              上市 {{ detail.info.list_date }}
            </span>
            <span v-if="quote" class="sub-meta" style="margin: 0">
              数据日 {{ quote.trade_date }}
            </span>
          </div>
        </div>

        <div class="detail-side">
          <div class="detail-price">
            <div class="value" :class="tone(quote?.pct_chg)">{{ num(quote?.close) }}</div>
            <div class="change" :class="tone(quote?.pct_chg)">
              {{ pct(quote?.pct_chg) }}
              <span v-if="change !== null">
                （{{ (change ?? 0) > 0 ? '+' : '' }}{{ num(change) }}）
              </span>
            </div>
            <div class="sub-meta" style="margin: 0">
              昨收 {{ num(quote?.pre_close) }} · 开 {{ num(quote?.open) }} · 高
              {{ num(quote?.high) }} · 低 {{ num(quote?.low) }}
            </div>
          </div>
          <div class="detail-actions">
            <button class="btn" :disabled="busy || loading" @click="emit('toggle-watch')">
              {{ watching ? '移出自选' : '加入自选' }}
            </button>
            <button class="btn ghost" :disabled="!detail" @click="emit('research')">
              开始推演
            </button>
          </div>
        </div>
      </div>
    </section>

    <div v-if="error" class="error-bar">
      {{ error }}
      <button class="btn ghost" style="margin-left: 10px" @click="load(symbol)">重试</button>
    </div>

    <template v-if="detail">
      <section class="panel detail-chart-card">
        <div class="detail-toolbar">
          <div class="tabs">
            <button
              class="tab"
              :class="{ active: tab === 'intraday' }"
              @click="tab = 'intraday'"
            >
              分时
            </button>
            <button class="tab" :class="{ active: tab === 'day' }" @click="tab = 'day'">
              日K
            </button>
          </div>
          <div class="sub-meta" style="margin: 0">
            <template v-if="tab === 'intraday'">
              <span v-if="intraday?.trade_date">最新交易日 {{ intraday.trade_date }}</span>
              <span v-else>最新交易日 —</span>
              <span v-if="intraday?.source"> · {{ intraday.source }}</span>
              <span v-if="intraday?.points.length"> · {{ intraday.points.length }} 个分钟点</span>
            </template>
            <template v-else>
              <span>
                共 {{ historyBars.length || detail.chart.length }} 个交易日
                <template v-if="historyLoading"> · 读取中…</template>
              </span>
            </template>
          </div>
        </div>
        <div v-if="tab === 'day'" class="range-chips">
          <button
            v-for="range in RANGES"
            :key="range.key"
            type="button"
            class="chip"
            :class="{ active: span === range.key }"
            :disabled="historyLoading"
            @click="span = range.key"
          >
            {{ range.label }}
          </button>
        </div>
        <div class="detail-chart-body">
          <IntradayChart
            v-if="tab === 'intraday'"
            :data="intraday"
            :pre-close="detail.quote.pre_close"
          />
          <PriceChart v-else-if="dayChart" :chart="dayChart" />
          <div v-else class="chart-empty">暂无日K数据</div>
        </div>
      </section>

      <section class="panel card">
        <div class="research-head" style="margin-bottom: 10px">
          <h3 style="margin: 0">行情指标</h3>
          <span class="sub-meta" style="margin: 0">{{ detail.quote.trade_date }}</span>
        </div>
        <div class="metric-grid">
          <div v-for="item in metrics" :key="item.k" class="metric">
            <span class="k">{{ item.k }}</span>
            <span class="v" :class="item.tone">{{ item.v }}</span>
          </div>
        </div>
      </section>

      <div class="detail-cols">
        <section class="panel card">
          <div class="research-head" style="margin-bottom: 8px">
            <h3 style="margin: 0">因子快照</h3>
            <span class="sub-meta" style="margin: 0">面板因子 · 非实时</span>
          </div>
          <div v-if="factorRows.length" class="factor-grid">
            <div v-for="row in factorRows" :key="row.k" class="factor-cell">
              <span class="k">{{ row.k }}</span>
              <span class="v" :class="row.tone">{{ row.v }}</span>
            </div>
          </div>
          <div v-else class="loading">暂无因子快照。</div>
        </section>

        <section class="panel card">
          <div class="research-head" style="margin-bottom: 8px">
            <h3 style="margin: 0">财务快照</h3>
            <span v-if="financial" class="sub-meta" style="margin: 0">
              {{ financial.end_date }} · 公告 {{ financial.ann_date }}
            </span>
          </div>
          <div v-if="finRows.length" class="fin-grid">
            <div v-for="row in finRows" :key="row.k" class="kv">
              <span class="k">{{ row.k }}</span>
              <span class="v" :class="row.tone">{{ row.v }}</span>
            </div>
          </div>
          <div v-else class="loading">
            {{ isEtf ? 'ETF 没有企业财务报表。' : '暂无财务数据。' }}
          </div>
        </section>
      </div>

      <section class="panel card">
        <div class="research-head" style="margin-bottom: 8px">
          <div>
            <h3 style="margin: 0">最近推演</h3>
            <div class="sub-meta" style="margin-top: 4px">多智能体推演的风险裁定与建议仓位</div>
          </div>
          <button class="btn ghost" @click="emit('research')">打开推演</button>
        </div>
        <div v-if="runs.length" class="run-list">
          <div class="run-row head">
            <span>#</span>
            <span>时间</span>
            <span>裁定</span>
            <span>仓位</span>
          </div>
          <div v-for="run in runs" :key="run.id" class="run-row">
            <span class="mono">#{{ run.id }}</span>
            <span class="sub-meta" style="margin: 0">{{ fmtTime(run.started_at) }}</span>
            <span class="run-decision" :title="run.decision">{{ run.decision || run.status }}</span>
            <span class="mono">{{ run.position_pct }}%</span>
          </div>
        </div>
        <div v-else class="loading">还没有推演记录，点「开始推演」跑一次。</div>
      </section>
    </template>

    <section v-else-if="loading" class="panel card empty">
      <div class="big">加载 {{ name || symbol }} 的详情…</div>
      <div>正在读取行情、分时、因子、财务与历史推演。</div>
    </section>
  </div>
</template>
