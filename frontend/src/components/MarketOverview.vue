<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import type {
  MarketBreadth,
  MarketBreadthMap,
  MarketHotStock,
  MarketLimit,
  MarketMoney,
  MarketOverview,
  MarketSectors,
  StockContextPayload,
  StockRef,
  TopicReportDetail,
  TopicToday,
} from '../types'

const props = defineProps<{
  data: MarketOverview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'select', payload: StockRef): void
  (e: 'context', payload: StockContextPayload): void
}>()

const breadthKind = ref<'stock' | 'etf' | 'hk'>('stock')
const breadthMap = computed<MarketBreadthMap>(() => props.data?.breadth ?? {})
const breadth = computed<MarketBreadth | null>(() => {
  const row = breadthMap.value[breadthKind.value]
  return row ?? null
})
const breadthTabs = computed(() => [
  { key: 'stock' as const, label: 'A股', row: breadthMap.value.stock ?? null },
  { key: 'etf' as const, label: 'ETF', row: breadthMap.value.etf ?? null },
  { key: 'hk' as const, label: '港股', row: breadthMap.value.hk ?? null },
])
const limit = computed<MarketLimit | null>(() => props.data?.limit ?? null)
const money = computed<MarketMoney | null>(() => props.data?.money ?? null)
const sectors = computed<MarketSectors | null>(() => props.data?.sectors ?? null)
const hot = computed<MarketHotStock[]>(() => props.data?.hot ?? [])
const indices = computed(() => props.data?.indices ?? [])

const breadthTotal = computed(() => {
  const row = breadth.value
  if (!row) return 0
  return row.up + row.down + row.flat
})

/** 某块数据的日期早于面板数据日，就明确标「滞后」，而不是假装是今天。 */
function stale(day: string | null | undefined): boolean {
  const asOf = props.data?.as_of
  return Boolean(day && asOf && day < asOf)
}

/* ---------- 一键今日复盘（情绪 / 阶段 / 板块） ---------- */
const today = ref<TopicToday | null>(null)
const todayBusy = ref(false)
const todayError = ref('')
const todaySeconds = ref(0)
const viewing = ref<TopicReportDetail | null>(null)
const showHistory = ref(false)
let ticker: ReturnType<typeof setInterval> | null = null
let startedAt = 0

const sentiment = computed(() => today.value?.sentiment ?? null)
const history = computed(() => today.value?.history ?? [])
const shownReport = computed(() => viewing.value ?? today.value?.report ?? null)

const spark = computed(() => {
  const points = (sentiment.value?.points ?? []).slice(-20)
  if (points.length < 2) return ''
  const width = 220
  const height = 44
  const scores = points.map((point) => point.score)
  const low = Math.min(...scores)
  const high = Math.max(...scores)
  const span = high - low || 1
  return points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * width
      const y = height - ((point.score - low) / span) * (height - 6) - 3
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

async function loadToday() {
  try {
    today.value = await api.topicToday()
  } catch {
    /* 没生成过或后端旧版本：留空即可 */
  }
}

async function runToday(force: boolean) {
  if (todayBusy.value) return
  todayBusy.value = true
  todayError.value = ''
  viewing.value = null
  todaySeconds.value = 0
  startedAt = Date.now()
  ticker = setInterval(() => {
    todaySeconds.value = Math.round((Date.now() - startedAt) / 1000)
  }, 1000)
  try {
    today.value = await api.topicTodayRun(force)
  } catch (exc) {
    todayError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    todayBusy.value = false
    if (ticker) {
      clearInterval(ticker)
      ticker = null
    }
  }
}

async function openHistory(item: { id: number }) {
  try {
    viewing.value = await api.topicReport(item.id)
  } catch (exc) {
    todayError.value = exc instanceof Error ? exc.message : String(exc)
  }
}

function renderBrief(markdown: string): string {
  return DOMPurify.sanitize(marked.parse(markdown) as string)
}

onMounted(() => {
  void loadToday()
})

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
  ticker = null
})

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

/** 后端三种金额口径：指数 amount 为千元，板块/资金为万元，宽度成交额为元。 */
function yi(value: number | null | undefined, unit: 'yuan' | 'wan' | 'qian', digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const scaled = unit === 'yuan' ? value / 1e8 : unit === 'wan' ? value / 1e4 : value / 1e5
  if (Math.abs(scaled) >= 10000) return `${(scaled / 10000).toFixed(2)} 万亿`
  return `${scaled.toFixed(digits)} 亿`
}

function breadthWidth(value: number): string {
  if (!breadthTotal.value) return '0%'
  return `${(value / breadthTotal.value) * 100}%`
}

function hotSymbol(item: MarketHotStock): string | null {
  const match = /^(\d{6})\.(SH|SZ|BJ)$/.exec(item.code)
  if (match) return match[1]
  return /^\d{6}$/.test(item.code) ? item.code : null
}

function openHot(item: MarketHotStock) {
  const symbol = hotSymbol(item)
  if (!symbol) return
  emit('select', { symbol, name: item.name })
}

function openContext(item: MarketHotStock, event: MouseEvent) {
  const symbol = hotSymbol(item)
  if (!symbol) return
  emit('context', {
    symbol,
    name: item.name,
    x: event.clientX,
    y: event.clientY,
  })
}
</script>

<template>
  <section class="panel market-head">
    <div>
      <div class="market-title">
        <h2>今日大盘</h2>
        <span class="badge">数据日 {{ data?.as_of || '—' }}</span>
        <span v-if="data?.prev_date" class="badge">上一交易日 {{ data?.prev_date }}</span>
        <span v-if="loading" class="loading">读取中…</span>
      </div>
      <div class="market-sub">
        指数 · 涨跌家数 · 涨跌停 · 两市成交额 · 主力资金 · 板块轮动 · 热门股
      </div>
    </div>
    <div class="market-actions">
      <button
        class="btn"
        :disabled="todayBusy"
        :title="today?.report ? '重新调用模型生成（约 15k tokens）' : '生成今日复盘：情绪温度、所处阶段、板块结构（约 15k tokens）'"
        @click="runToday(Boolean(today?.report))"
      >
        {{ todayBusy ? `生成中 ${todaySeconds}s…` : today?.report ? '重新生成复盘' : '一键今日复盘' }}
      </button>
      <button class="btn ghost" :disabled="loading" @click="emit('refresh')">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>
  </section>

  <div v-if="error" class="error-bar">{{ error }}</div>

  <template v-if="data">
    <div class="index-grid">
      <div v-for="index in indices" :key="index.code" class="panel index-card">
        <div class="index-label">
          <span>{{ index.label }}</span>
          <span class="mono">{{ index.code }}</span>
        </div>
        <div class="index-close" :class="tone(index.pct_chg)">{{ num(index.close) }}</div>
        <div class="index-foot">
          <span :class="tone(index.pct_chg)">{{ pct(index.pct_chg) }}</span>
          <span>成交 {{ yi(index.amount, 'qian') }}</span>
        </div>
      </div>
      <div v-if="!indices.length" class="panel index-card">
        <div class="loading">暂无指数数据</div>
      </div>
    </div>

    <div class="market-grid">
      <div class="panel market-card span-2">
        <div class="market-card-head">
          <h3>涨跌家数</h3>
          <div class="market-tabs">
            <button
              v-for="tab in breadthTabs"
              :key="tab.key"
              type="button"
              class="market-tab"
              :class="{ active: breadthKind === tab.key }"
              :disabled="!tab.row"
              :title="tab.row ? `${tab.label} ${tab.row.up + tab.row.down + tab.row.flat} 只` : '本地面板未覆盖'"
              @click="breadthKind = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>
          <span class="market-card-note">
            数据日 {{ data.as_of || '—' }}
          </span>
        </div>
        <template v-if="breadth">
          <div class="market-big">
            {{ breadthTotal.toLocaleString('zh-CN') }}
            <small>{{ breadthKind === 'stock' ? '只 A 股' : breadthKind === 'etf' ? '只 ETF' : '只港股' }}</small>
          </div>
          <div class="breadth-track">
            <span class="breadth-seg up" :style="{ width: breadthWidth(breadth.up) }"></span>
            <span class="breadth-seg flat" :style="{ width: breadthWidth(breadth.flat) }"></span>
            <span class="breadth-seg down" :style="{ width: breadthWidth(breadth.down) }"></span>
          </div>
          <div class="breadth-legend">
            <span><i class="dot dot-up"></i>上涨 <b class="up">{{ breadth.up }}</b></span>
            <span><i class="dot dot-down"></i>下跌 <b class="down">{{ breadth.down }}</b></span>
            <span><i class="dot dot-flat"></i>平盘 <b>{{ breadth.flat }}</b></span>
            <span>成交 <b>{{ yi(breadth.amount, 'yuan') }}</b></span>
          </div>
        </template>
        <div v-else class="market-card-note">
          本地面板暂无{{ breadthKind === 'hk' ? '港股' : '该类' }}数据（只覆盖 A 股与 ETF）
        </div>
      </div>

      <div class="panel market-card">
        <div class="market-card-head">
          <h3>涨停 / 跌停</h3>
          <span class="market-card-note">
            {{ limit?.date || '—' }}
            <em v-if="stale(limit?.date)" class="stale-flag">滞后</em>
          </span>
        </div>
        <div class="market-big">
          <span class="up">{{ limit?.up ?? 0 }}</span>
          <small>涨停</small>
          <span class="down" style="margin-left: 12px">{{ limit?.down ?? 0 }}</span>
          <small>跌停</small>
        </div>
        <div class="market-card-note">
          {{ limit?.date ? `数据日 ${limit.date}` : '暂无涨跌停数据' }}
        </div>
      </div>

      <div class="panel market-card">
        <div class="market-card-head">
          <h3>主力净流入</h3>
          <span class="market-card-note">
            {{ money?.date || '—' }}
            <em v-if="stale(money?.date)" class="stale-flag">滞后</em>
          </span>
        </div>
        <div class="market-big" :class="tone(money?.net_mf_amount)">
          {{
            money?.net_mf_amount === null || money?.net_mf_amount === undefined
              ? '—'
              : yi(money?.net_mf_amount, 'wan')
          }}
        </div>
        <div class="market-card-note">
          {{
            money?.net_mf_amount === null || money?.net_mf_amount === undefined
              ? '资金流数据缺失'
              : '全市场当日主力资金净额'
          }}
        </div>
      </div>

      <div class="panel market-card span-2">
        <div class="market-card-head">
          <h3>板块领涨 / 领跌</h3>
          <span class="market-card-note">
            {{ sectors?.date || '—' }}
            <em v-if="stale(sectors?.date)" class="stale-flag">滞后</em>
          </span>
        </div>
        <div v-if="sectors" class="sector-columns">
          <div>
            <div class="sector-col-title"><span>领涨</span><span>涨跌幅 / 成交额</span></div>
            <div v-for="(sector, i) in sectors.top" :key="`top-${i}`" class="sector-row">
              <span class="sector-name" :title="sector.name">{{ sector.name }}</span>
              <span class="sector-pct up">{{ pct(sector.pct_chg) }}</span>
              <span class="sector-amount">{{ yi(sector.amount, 'wan') }}</span>
            </div>
            <div v-if="!sectors.top.length" class="loading">暂无领涨板块</div>
          </div>
          <div>
            <div class="sector-col-title"><span>领跌</span><span>涨跌幅 / 成交额</span></div>
            <div v-for="(sector, i) in sectors.bottom" :key="`bottom-${i}`" class="sector-row">
              <span class="sector-name" :title="sector.name">{{ sector.name }}</span>
              <span class="sector-pct down">{{ pct(sector.pct_chg) }}</span>
              <span class="sector-amount">{{ yi(sector.amount, 'wan') }}</span>
            </div>
            <div v-if="!sectors.bottom.length" class="loading">暂无领跌板块</div>
          </div>
        </div>
        <div v-else class="loading">暂无板块数据</div>
      </div>

      <div class="panel market-card span-2 market-brief">
        <div class="market-card-head">
          <h3>今日复盘 · 情绪 / 阶段 / 板块</h3>
          <span v-if="today?.report" class="market-card-note">
            {{ today.report.as_of }} · {{ today.report.verdict }} {{ today.report.confidence }} 分
            · {{ today.report.model }}
            <em v-if="today.stale" class="stale-flag">非最新交易日</em>
          </span>
          <span v-else class="market-card-note">一键生成，当天只算一次</span>
        </div>

        <div v-if="todayBusy" class="market-brief-wait">
          正在让模型写复盘（{{ todaySeconds }} 秒）——大约 3–8k tokens，请保持页面打开。
        </div>
        <div v-else-if="todayError" class="error-bar">{{ todayError }}</div>
        <template v-else>
          <div v-if="sentiment" class="brief-stats">
            <div class="brief-stat">
              <span>情绪温度</span>
              <strong>{{ sentiment.score.toFixed(1) }}</strong>
              <em :class="(sentiment.change ?? 0) >= 0 ? 'up' : 'down'">
                {{ (sentiment.change ?? 0) >= 0 ? '+' : '' }}{{ (sentiment.change ?? 0).toFixed(1) }}
              </em>
            </div>
            <div class="brief-stat">
              <span>阶段</span>
              <strong>{{ sentiment.stage }}</strong>
              <em>5日 {{ (sentiment.change_5d ?? 0) >= 0 ? '+' : '' }}{{ (sentiment.change_5d ?? 0).toFixed(1) }}</em>
            </div>
            <div class="brief-stat">
              <span>板块扩散</span>
              <strong>{{ (sentiment.components.sector_up_ratio ?? 0).toFixed(1) }}%</strong>
              <em>{{ sentiment.components.industries ?? '—' }} 行业</em>
            </div>
            <div class="brief-stat">
              <span>涨停 / 跌停</span>
              <strong>{{ sentiment.components.limit_up ?? '—' }} / {{ sentiment.components.limit_down ?? '—' }}</strong>
            </div>
            <div class="brief-stat">
              <span>上涨占比</span>
              <strong>{{ (sentiment.components.up_ratio ?? 0).toFixed(1) }}%</strong>
              <em>站上20日线 {{ (sentiment.components.above_ma20_ratio ?? 0).toFixed(1) }}%</em>
            </div>
            <div class="brief-stat">
              <span>成交额</span>
              <strong>{{ ((sentiment.components.amount_yi ?? 0) / 10000).toFixed(2) }} 万亿</strong>
              <em>{{ sentiment.components.amount_ratio ?? '—' }}× 20日均量</em>
            </div>
          </div>

          <svg v-if="spark" class="brief-spark" viewBox="0 0 220 44" preserveAspectRatio="none" role="img" aria-label="近 20 日情绪分数">
            <polyline :points="spark" fill="none" stroke="var(--accent)" stroke-width="2" />
          </svg>

          <div class="market-brief-actions">
            <template v-if="shownReport">
              <span class="market-card-note">
                {{ shownReport.as_of }} · {{ shownReport.verdict }} {{ shownReport.confidence }} 分 · {{ shownReport.model }}
                <template v-if="today?.cached && !viewing"> · 来自缓存（未重复调用模型）</template>
              </span>
            </template>
            <span style="flex: 1" />
            <button v-if="viewing" type="button" class="btn ghost" @click="viewing = null">回到今天</button>
            <button v-if="history.length" type="button" class="btn ghost" @click="showHistory = !showHistory">
              {{ showHistory ? '收起历史' : `历史复盘（${history.length}）` }}
            </button>
          </div>

          <div v-if="showHistory" class="brief-history">
            <button
              v-for="item in history"
              :key="item.id"
              type="button"
              class="brief-history-row"
              @click="openHistory(item)"
            >
              <span class="mono">{{ item.as_of }}</span>
              <span class="brief-history-title">{{ item.title }}</span>
              <span>{{ item.verdict }} · {{ item.confidence }} 分</span>
            </button>
          </div>

          <div v-if="shownReport" class="markdown market-brief-body" v-html="renderBrief(shownReport.markdown)"></div>
          <div v-else class="market-brief-wait">
            今天还没生成。点右上角「一键今日复盘」，会基于当前面板数据写一份
            <strong>情绪温度 / 所处阶段 / 板块结构</strong> 的备忘；约 30–60 秒，结果当天保留在这里。
          </div>
        </template>
      </div>

      <div class="panel market-card span-2">
        <div class="market-card-head">
          <h3>热门股</h3>
          <span class="market-card-note">
            {{ data.hot_date || '—' }}
            <em v-if="stale(data.hot_date)" class="stale-flag">滞后</em>
            <span v-if="hot.length"> · 点击详情 · 右键更多</span>
          </span>
        </div>
        <div class="hot-list">
          <button
            v-for="(item, i) in hot"
            :key="`${item.code}-${i}`"
            type="button"
            class="hot-row"
            :disabled="!hotSymbol(item)"
            :title="hotSymbol(item) ? `${item.name} ${item.code}` : '非 A 股标的，暂不支持详情'"
            @click="openHot(item)"
            @contextmenu.prevent="openContext(item, $event)"
          >
            <span class="hot-rank">{{ item.rank }}</span>
            <span class="hot-name">
              <strong>{{ item.name }}</strong>
              <span class="mono">{{ item.code }}</span>
            </span>
            <span class="hot-pct" :class="tone(item.pct_chg)">{{ pct(item.pct_chg) }}</span>
            <span class="hot-tag">{{ hotSymbol(item) ? 'A股' : '非A股' }}</span>
          </button>
          <div v-if="!hot.length" class="loading">暂无热门股数据</div>
        </div>
      </div>
    </div>
  </template>

  <section v-else-if="loading" class="panel card empty">
    <div class="big">正在读取大盘数据…</div>
    <div>指数、涨跌家数、涨跌停、资金与热门股。</div>
  </section>

  <section v-else class="panel card empty">
    <div class="big">暂无大盘数据</div>
    <div>确认后端已同步行情后点击刷新。</div>
  </section>
</template>
