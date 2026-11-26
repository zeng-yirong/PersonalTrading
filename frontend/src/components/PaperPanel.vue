<script setup lang="ts">
import * as echarts from 'echarts'
import { nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { api } from '../api'
import type { StockContextPayload } from '../types'

interface Position {
  id: number
  symbol: string
  name: string | null
  shares: number
  avg_cost: number
  price: number | null
  market_value: number | null
  pnl: number | null
  pnl_pct: number | null
  opened_at: string
  run_id: number | null
  note: string | null
}

interface NavPoint {
  trade_date: string
  nav: number
  benchmark: number | null
  total: number
}

interface ReviewRow {
  run_id: number
  symbol: string
  name: string | null
  action: string | null
  position_pct: number | null
  entry_date: string
  entry_price: number
  d5_return: number | null
  d20_return: number | null
  d20_excess: number | null
  outcome: string | null
}

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'context', payload: StockContextPayload): void
}>()

const summary = ref<any>(null)
const positions = ref<Position[]>([])
const nav = ref<NavPoint[]>([])
const reviews = ref<ReviewRow[]>([])
const stats = ref<any>(null)
const loading = ref(false)
const error = ref('')
const busy = ref(false)

const container = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function renderNav() {
  if (!container.value || nav.value.length < 2) return
  if (!chart) chart = echarts.init(container.value)
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 48, right: 20, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['策略净值', '沪深300'], right: 10, top: 0, textStyle: { color: '#8fa0b5' } },
    xAxis: {
      type: 'category',
      data: nav.value.map((item) => item.trade_date),
      axisLine: { lineStyle: { color: '#223041' } },
      axisLabel: { color: '#8fa0b5' },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#8fa0b5' },
      splitLine: { lineStyle: { color: 'rgba(127,127,127,0.15)' } },
    },
    series: [
      {
        name: '策略净值',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: nav.value.map((item) => item.nav),
        lineStyle: { color: '#4da3ff', width: 2 },
        itemStyle: { color: '#4da3ff' },
      },
      {
        name: '沪深300',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: nav.value.map((item) => item.benchmark),
        lineStyle: { color: '#8fa0b5', width: 1.5, type: 'dashed' },
        itemStyle: { color: '#8fa0b5' },
      },
    ],
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [s, p, n, r, st] = await Promise.all([
      api.paperSummary(),
      api.paperPositions(),
      api.paperNav(),
      api.paperReviews(20),
      api.paperReviewStats(),
    ])
    summary.value = s
    positions.value = p.positions
    nav.value = n.series
    reviews.value = r.reviews
    stats.value = st
    await nextTick()
    renderNav()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    loading.value = false
  }
}

async function fromRun() {
  // 从账本里找最近一条「批准且仓位>0」的推演，而不是写死某只股票。
  const ledger = await api.opsLedger(50, '推演')
  const candidate = ledger.entries.find(
    (entry) =>
      ['批准', '调整后批准'].includes(entry.stance) &&
      typeof entry.score === 'number' &&
      entry.score > 0,
  )
  if (!candidate) {
    error.value = '没有可建仓的推演（需要裁定为批准且仓位大于 0）'
    return
  }
  busy.value = true
  try {
    await api.paperFromRun(candidate.id)
    await load()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}

function fmt(value: number | null, digits = 2): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function pct(value: number | null): string {
  if (value === null || value === undefined) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function openContext(item: Position, event: MouseEvent) {
  emit('context', {
    symbol: item.symbol,
    name: item.name || '',
    x: event.clientX,
    y: event.clientY,
  })
}

function onResize() {
  chart?.resize()
}

onMounted(() => {
  void load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
onDeactivated(() => {
  chart?.dispose()
  chart = null
})
onActivated(() => {
  void nextTick(renderNav)
})
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">模拟持仓</h3>
        <div class="sub-meta" style="margin-top: 4px">
          按面板收盘价成交，含佣金/印花税/过户费；净值与沪深300 对比
        </div>
      </div>
      <div style="display: flex; gap: 8px">
        <button class="btn ghost" :disabled="loading || busy" @click="load">刷新</button>
        <button class="btn" :disabled="loading || busy" @click="fromRun">按推演建仓</button>
      </div>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="summary" class="paper-summary">
      <div class="paper-stat">
        <span class="paper-stat-label">总资产</span>
        <strong>{{ fmt(summary.total, 0) }}</strong>
      </div>
      <div class="paper-stat">
        <span class="paper-stat-label">现金</span>
        <strong>{{ fmt(summary.cash, 0) }}</strong>
      </div>
      <div class="paper-stat">
        <span class="paper-stat-label">持仓市值</span>
        <strong>{{ fmt(summary.market_value, 0) }}</strong>
      </div>
      <div class="paper-stat">
        <span class="paper-stat-label">浮动盈亏</span>
        <strong :class="summary.pnl >= 0 ? 'up' : 'down'">
          {{ fmt(summary.pnl, 0) }}（{{ pct(summary.pnl_pct) }}）
        </strong>
      </div>
    </div>

    <div v-if="positions.length" class="screen-table-wrap" style="margin-top: 12px">
      <table class="screen-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>股数</th>
            <th>成本</th>
            <th>现价</th>
            <th>市值</th>
            <th>盈亏</th>
            <th>来源</th>
          </tr>
        </thead>
        <tbody>
          <tr
            class="screen-row"
            v-for="item in positions"
            :key="item.id"
            @click="emit('select', item.symbol)"
            @contextmenu.prevent="openContext(item, $event)"
          >
            <td class="mono">{{ item.symbol }}</td>
            <td>{{ item.name }}</td>
            <td>{{ fmt(item.shares, 0) }}</td>
            <td>{{ fmt(item.avg_cost) }}</td>
            <td>{{ fmt(item.price) }}</td>
            <td>{{ fmt(item.market_value, 0) }}</td>
            <td :class="(item.pnl_pct ?? 0) >= 0 ? 'up' : 'down'">{{ pct(item.pnl_pct) }}</td>
            <td class="sub-meta" style="margin: 0">{{ item.run_id ? `推演 #${item.run_id}` : '手工' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="nav.length > 1" ref="container" class="paper-nav"></div>
    <div v-else-if="summary" class="loading" style="margin-top: 12px">
      还没有足够的历史交易日画出净值曲线。
    </div>

    <div v-if="reviews.length" style="margin-top: 14px">
      <div class="sub-meta" style="margin: 0 0 8px">
        复盘：T+5 / T+20 表现<span v-if="stats?.total">
          ｜已满 20 日 {{ stats.total }} 条，命中率 {{ stats.hit_rate }}%，平均超额
          {{ pct(stats.avg_excess_20d) }}</span>
      </div>
      <div class="screen-table-wrap">
        <table class="screen-table">
          <thead>
            <tr>
              <th>推演</th>
              <th>标的</th>
              <th>入场</th>
              <th>T+5</th>
              <th>T+20</th>
              <th>超额</th>
              <th>结论</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in reviews" :key="row.run_id">
              <td class="mono">#{{ row.run_id }}</td>
              <td>{{ row.symbol }}</td>
              <td>{{ row.entry_date }}</td>
              <td>{{ pct(row.d5_return) }}</td>
              <td>{{ pct(row.d20_return) }}</td>
              <td>{{ pct(row.d20_excess) }}</td>
              <td>{{ row.outcome }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
