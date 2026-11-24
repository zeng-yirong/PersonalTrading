<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import type {
  BacktestMeta,
  BacktestRequest,
  BacktestResult,
  BacktestRunSummary,
  ScreenQueryPayload,
} from '../types'

const root = ref<HTMLElement | null>(null)
const chartEl = ref<HTMLDivElement | null>(null)
const resultHead = ref<HTMLElement | null>(null)

const meta = ref<BacktestMeta | null>(null)
const pendingQuery = ref<ScreenQueryPayload | null>(null)
const title = ref('')

const startDate = ref('')
const endDate = ref('')
const rebalance = ref(20)
const topN = ref(10)
const slippage = ref(5)
const initialCash = ref<number | null>(null)
const benchmark = ref('')

const loading = ref(false)
const loadingText = ref('')
const error = ref('')
const result = ref<BacktestResult | null>(null)
const runs = ref<BacktestRunSummary[]>([])
const selectedPeriod = ref<number | null>(null)
const showSpec = ref(false)

let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null
let metaReady: Promise<void> | null = null

const rebalanceChoices = computed<number[]>(
  () => meta.value?.rebalance_choices ?? [5, 10, 20, 60],
)
const maxTopN = computed(() => meta.value?.limits.top_n ?? 50)
const maxRebalance = computed(() => meta.value?.limits.rebalance ?? 250)
const querySummary = computed(() => {
  const query = pendingQuery.value
  if (!query) return ''
  return `${query.title || '未命名条件'} · ${query.filters.length} 个筛选条件`
})

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

function shiftYears(iso: string, years: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!match) return iso
  const shifted = new Date(
    Date.UTC(Number(match[1]) - years, Number(match[2]) - 1, Number(match[3])),
  )
  return shifted.toISOString().slice(0, 10)
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function money(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function weightPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(2)}%`
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false })
}

function clamp(value: number, low: number, high: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback
  return Math.min(high, Math.max(low, Math.round(value)))
}

function buildRequest(query: ScreenQueryPayload): BacktestRequest {
  const payload: BacktestRequest = {
    query,
    rebalance: clamp(rebalance.value, 1, maxRebalance.value, 20),
    top_n: clamp(topN.value, 1, maxTopN.value, 10),
    slippage_bps: Number.isFinite(slippage.value)
      ? Math.min(200, Math.max(0, slippage.value))
      : 5,
  }
  if (startDate.value) payload.start = startDate.value
  if (endDate.value) payload.end = endDate.value
  if (title.value) payload.title = title.value
  if (initialCash.value !== null) payload.initial_cash = initialCash.value
  if (benchmark.value) payload.benchmark = benchmark.value
  return payload
}

function disposeChart() {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
}

function clearResult() {
  result.value = null
  selectedPeriod.value = null
  disposeChart()
}

function renderChart() {
  const points = result.value?.nav ?? []
  if (!chartEl.value || points.length < 2) return
  // A cleared result removes the container: never draw into a detached node.
  if (chart && chart.getDom() !== chartEl.value) disposeChart()
  if (!chart) chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  const hasBenchmark = points.some((item) => item.benchmark !== null)
  chart.setOption(
    {
      backgroundColor: 'transparent',
      animation: false,
      legend: {
        data: hasBenchmark ? ['策略净值', '基准净值'] : ['策略净值'],
        top: 4,
        right: 12,
        textStyle: { color: '#8fa0b5', fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', lineStyle: { color: '#3a4b5f' } },
        backgroundColor: 'rgba(15,21,29,0.96)',
        borderColor: '#223041',
        textStyle: { color: '#e8eef6', fontSize: 12 },
        valueFormatter: (value: unknown) =>
          typeof value === 'number' ? value.toFixed(4) : '—',
      },
      grid: { left: 52, right: 18, top: 36, bottom: 38 },
      xAxis: {
        type: 'category',
        data: points.map((item) => item.trade_date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#223041' } },
        axisLabel: { color: '#8fa0b5', fontSize: 11 },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        position: 'right',
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#8fa0b5', fontSize: 11 },
        splitLine: { lineStyle: { color: 'rgba(34,48,65,0.55)' } },
      },
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        {
          type: 'slider',
          bottom: 2,
          height: 16,
          borderColor: '#223041',
          fillerColor: 'rgba(77,163,255,0.12)',
          textStyle: { color: '#8fa0b5', fontSize: 10 },
        },
      ],
      series: [
        {
          name: '策略净值',
          type: 'line' as const,
          data: points.map((item) => item.nav),
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#4da3ff', width: 2 },
          itemStyle: { color: '#4da3ff' },
        },
        ...(hasBenchmark
          ? [
              {
                name: '基准净值',
                type: 'line' as const,
                data: points.map((item) => item.benchmark),
                smooth: true,
                showSymbol: false,
                lineStyle: { color: '#8fa0b5', width: 1.5, type: 'dashed' as const },
                itemStyle: { color: '#8fa0b5' },
              },
            ]
          : []),
      ],
    },
    true,
  )
  if (!observer && chartEl.value) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartEl.value)
  }
}

async function applyResult(data: BacktestResult) {
  result.value = data
  selectedPeriod.value = null
  await nextTick()
  renderChart()
}

async function loadMeta() {
  try {
    const data = await api.backtestMeta()
    meta.value = data
    // A history run opened while meta was still loading owns the controls.
    if (result.value) return
    rebalance.value = data.defaults.rebalance
    topN.value = data.defaults.top_n
    slippage.value = data.defaults.slippage_bps
    initialCash.value = data.defaults.initial_cash
    benchmark.value = data.defaults.benchmark
    if (data.panel.end) {
      endDate.value = data.panel.end
      startDate.value = shiftYears(data.panel.end, data.defaults.lookback_years || 2)
    }
  } catch (exc) {
    error.value = `回测配置加载失败：${messageOf(exc)}`
  }
}

async function loadRuns() {
  try {
    const data = await api.backtestRuns(20)
    runs.value = data.runs
  } catch {
    /* history is optional: the form still works without it */
  }
}

function syncControls(data: BacktestResult) {
  pendingQuery.value = data.spec.query
  title.value = data.spec.title || data.title
  startDate.value = data.spec.start
  endDate.value = data.spec.end
  rebalance.value = data.spec.rebalance
  topN.value = data.spec.top_n
  slippage.value = data.spec.slippage_bps
  initialCash.value = data.spec.initial_cash
  benchmark.value = data.spec.benchmark
}

async function run() {
  const query = pendingQuery.value
  if (!query) {
    error.value = '还没有可回测的筛选条件：请先在「选股器」里筛一次'
    return
  }
  if (loading.value) return
  loading.value = true
  loadingText.value = '回测运行中：读取历史面板，逐日模拟成交、费用与涨跌停约束…'
  error.value = ''
  clearResult()
  try {
    const data = await api.backtestRun(buildRequest(query))
    await applyResult(data)
    await loadRuns()
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    loading.value = false
    loadingText.value = ''
  }
}

async function runWith(query: ScreenQueryPayload, runTitle?: string) {
  pendingQuery.value = query
  title.value = runTitle || query.title || ''
  error.value = ''
  root.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  if (metaReady) await metaReady
  await run()
}

async function loadRun(id: number) {
  if (loading.value) return
  loading.value = true
  loadingText.value = `读取历史回测 #${id}…`
  error.value = ''
  try {
    const data = await api.backtestRunDetail(id)
    syncControls(data)
    await applyResult(data)
    resultHead.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  } catch (exc) {
    error.value = `读取历史回测失败：${messageOf(exc)}`
  } finally {
    loading.value = false
    loadingText.value = ''
  }
}

function togglePeriod(index: number) {
  selectedPeriod.value = selectedPeriod.value === index ? null : index
}

onMounted(() => {
  metaReady = loadMeta()
  void loadRuns()
})

onBeforeUnmount(() => {
  disposeChart()
})

defineExpose({ runWith })
</script>

<template>
  <section ref="root" class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">策略回测</h3>
        <div class="sub-meta" style="margin-top: 4px">
          信号日收盘筛选 → 次日开盘成交，含费用与涨跌停约束
          <span v-if="meta?.panel.end">
            （数据区间 {{ meta.panel.start }} 至 {{ meta.panel.end }}，{{ meta.panel.symbols }} 只）
          </span>
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px">
        <button class="btn ghost" :disabled="loading" @click="loadRuns">刷新历史</button>
        <button class="btn" :disabled="loading || !pendingQuery" @click="run">
          {{ loading ? '回测中…' : '开始回测' }}
        </button>
      </div>
    </div>

    <div class="backtest-controls">
      <div class="backtest-field">
        <label for="backtest-start">开始日期</label>
        <input id="backtest-start" v-model="startDate" type="date" />
      </div>
      <div class="backtest-field">
        <label for="backtest-end">结束日期</label>
        <input id="backtest-end" v-model="endDate" type="date" />
      </div>
      <div class="backtest-field">
        <label for="backtest-rebalance">调仓周期</label>
        <select id="backtest-rebalance" v-model.number="rebalance">
          <option v-for="choice in rebalanceChoices" :key="choice" :value="choice">
            {{ choice }}日
          </option>
        </select>
      </div>
      <div class="backtest-field">
        <label for="backtest-topn">持仓数量</label>
        <input
          id="backtest-topn"
          v-model.number="topN"
          type="number"
          min="1"
          :max="maxTopN"
          step="1"
        />
      </div>
      <div class="backtest-field">
        <label for="backtest-slippage">滑点（bp）</label>
        <input
          id="backtest-slippage"
          v-model.number="slippage"
          type="number"
          min="0"
          max="200"
          step="1"
        />
      </div>
    </div>

    <div class="sub-meta" style="margin-top: 8px">
      <template v-if="pendingQuery">
        当前条件：{{ querySummary }}｜初始资金 {{ money(initialCash, 0) }} 元｜基准
        {{ benchmark || '000300.SH' }}
      </template>
      <template v-else>先在「选股器」里筛一次，再点「用这些条件回测」。</template>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="loadingText" class="loading" style="margin-top: 10px">{{ loadingText }}</div>

    <template v-if="result">
      <div ref="resultHead" class="backtest-result-head">
        <div>
          <strong>{{ result.title }}</strong>
          <span v-if="result.id" class="badge" style="margin-left: 8px">#{{ result.id }}</span>
          <div class="sub-meta" style="margin: 4px 0 0">
            {{ result.metrics.start }} → {{ result.metrics.end }}｜{{
              result.metrics.trading_days
            }}
            个交易日｜{{ result.metrics.periods }} 期（空仓 {{ result.metrics.empty_periods }} 期）
            <span v-if="result.coverage">
              ｜覆盖 {{ result.coverage.dates }} 个截面 / {{ result.coverage.rows }} 行
            </span>
          </div>
        </div>
        <span class="sub-meta" style="margin: 0">
          {{ result.spec.rebalance }}日调仓 · {{ result.spec.top_n }}只 · 滑点
          {{ result.spec.slippage_bps }}bp
        </span>
      </div>

      <div class="backtest-metrics">
        <div class="backtest-stat">
          <span class="backtest-stat-label">策略收益</span>
          <strong :class="tone(result.metrics.total_return_pct)">
            {{ pct(result.metrics.total_return_pct) }}
          </strong>
          <span class="backtest-stat-note">年化 {{ pct(result.metrics.cagr_pct) }}</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">基准收益</span>
          <strong :class="tone(result.metrics.benchmark_return_pct)">
            {{ pct(result.metrics.benchmark_return_pct) }}
          </strong>
          <span class="backtest-stat-note">年化 {{ pct(result.metrics.benchmark_cagr_pct) }}</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">超额年化</span>
          <strong :class="tone(result.metrics.excess_cagr_pct)">
            {{ pct(result.metrics.excess_cagr_pct) }}
          </strong>
          <span class="backtest-stat-note">平均每期超额 {{ pct(result.metrics.avg_excess_pct) }}</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">最大回撤</span>
          <strong :class="tone(result.metrics.max_drawdown_pct)">
            {{ pct(result.metrics.max_drawdown_pct) }}
          </strong>
          <span class="backtest-stat-note">年化波动 {{ pct(result.metrics.volatility_pct) }}</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">夏普比率</span>
          <strong>{{ num(result.metrics.sharpe) }}</strong>
          <span class="backtest-stat-note">平均每期 {{ pct(result.metrics.avg_period_pct) }}</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">胜率</span>
          <strong>{{ pct(result.metrics.hit_rate_pct, 1).replace('+', '') }}</strong>
          <span class="backtest-stat-note">{{ result.metrics.periods }} 期中跑赢基准</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">仓位</span>
          <strong>{{ pct(result.metrics.exposure_pct, 1).replace('+', '') }}</strong>
          <span class="backtest-stat-note">平均持仓 {{ num(result.metrics.avg_positions) }} 只</span>
        </div>
        <div class="backtest-stat">
          <span class="backtest-stat-label">成交</span>
          <strong>{{ result.metrics.trades }} 笔</strong>
          <span class="backtest-stat-note">费用 {{ money(result.metrics.fees, 2) }} 元</span>
        </div>
      </div>

      <div v-if="result.warnings.length" class="warning-bar" style="margin-top: 10px">
        <ul>
          <li v-for="(warning, index) in result.warnings" :key="index">{{ warning }}</li>
        </ul>
      </div>

      <div v-if="result.nav.length > 1" ref="chartEl" class="backtest-nav"></div>
      <div v-else class="loading" style="margin-top: 12px">净值数据不足，无法绘制曲线。</div>

      <div class="backtest-periods">
        <div class="sub-meta" style="margin: 0 0 8px">
          分期表现（点击任意一行展开当期持仓）
          <span v-if="result.metrics.best_period">
            ｜最佳第 {{ result.metrics.best_period.index }} 期
            {{ pct(result.metrics.best_period.return_pct) }}
          </span>
          <span v-if="result.metrics.worst_period">
            ｜最差第 {{ result.metrics.worst_period.index }} 期
            {{ pct(result.metrics.worst_period.return_pct) }}
          </span>
        </div>
        <div class="screen-table-wrap">
          <table class="screen-table">
            <thead>
              <tr>
                <th>#</th>
                <th>调仓日</th>
                <th>建仓日</th>
                <th>平仓日</th>
                <th>持仓</th>
                <th>本期</th>
                <th>基准</th>
                <th>超额</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="period in result.periods" :key="period.index">
                <tr
                  class="screen-row"
                  :class="{ 'backtest-row-active': selectedPeriod === period.index }"
                  @click="togglePeriod(period.index)"
                >
                  <td class="mono">{{ period.index }}</td>
                  <td>{{ period.signal_date }}</td>
                  <td>{{ period.entry_date }}</td>
                  <td>{{ period.exit_date }}</td>
                  <td :title="`候选 ${period.picks} 只`">{{ period.positions }}</td>
                  <td :class="tone(period.return_pct)">{{ pct(period.return_pct) }}</td>
                  <td :class="tone(period.benchmark_pct)">{{ pct(period.benchmark_pct) }}</td>
                  <td :class="tone(period.excess_pct)">{{ pct(period.excess_pct) }}</td>
                  <td class="backtest-note">{{ period.note || '—' }}</td>
                </tr>
                <tr v-if="selectedPeriod === period.index" class="backtest-holdings">
                  <td colspan="9">
                    <div class="sub-meta" style="margin: 0 0 6px">
                      第 {{ period.index }} 期持仓 {{ period.positions }} 只（候选
                      {{ period.picks }} 只）｜期末现金 {{ weightPct(period.cash_weight) }}｜费用
                      {{ money(period.fees, 2) }} 元
                    </div>
                    <table v-if="period.holdings.length" class="screen-table">
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>名称</th>
                          <th>权重</th>
                          <th>买入价</th>
                          <th>卖出价</th>
                          <th>个股收益</th>
                          <th>平仓日</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="holding in period.holdings" :key="holding.symbol">
                          <td class="mono">{{ holding.symbol }}</td>
                          <td>{{ holding.name }}</td>
                          <td>{{ weightPct(holding.weight) }}</td>
                          <td>{{ num(holding.entry_price, 3) }}</td>
                          <td>{{ num(holding.exit_price, 3) }}</td>
                          <td :class="tone(holding.return_pct)">{{ pct(holding.return_pct) }}</td>
                          <td>{{ holding.exit_date }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="loading">本期没有可成交的持仓。</div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>

      <details class="screen-query" :open="showSpec" @toggle="showSpec = !showSpec">
        <summary>查看回测参数</summary>
        <pre>{{ JSON.stringify(result.spec, null, 2) }}</pre>
      </details>
    </template>

    <div v-if="runs.length" class="backtest-history">
      <div class="sub-meta" style="margin: 0 0 6px">历史回测（{{ runs.length }}）——点击载入</div>
      <div class="backtest-run-list">
        <div class="backtest-run-row head">
          <span>#</span>
          <span>策略 / 区间</span>
          <span>收益</span>
          <span>超额年化</span>
          <span>最大回撤</span>
        </div>
        <div
          v-for="run in runs"
          :key="run.id"
          class="backtest-run-row"
          :class="{ active: result?.id === run.id }"
          @click="loadRun(run.id)"
        >
          <span class="mono">#{{ run.id }}</span>
          <span class="backtest-run-title">
            <span>{{ run.title || '未命名回测' }}</span>
            <span class="sub-meta" style="margin: 0">
              {{ run.spec.start || '—' }} → {{ run.spec.end || '—' }} ·
              {{ run.spec.rebalance ?? '—' }}日 · {{ run.spec.top_n ?? '—' }}只 ·
              {{ fmtTime(run.created_at) }}
            </span>
          </span>
          <span :class="tone(run.metrics.total_return_pct)">
            {{ pct(run.metrics.total_return_pct) }}
          </span>
          <span :class="tone(run.metrics.excess_cagr_pct)">
            {{ pct(run.metrics.excess_cagr_pct) }}
          </span>
          <span :class="tone(run.metrics.max_drawdown_pct)">
            {{ pct(run.metrics.max_drawdown_pct) }}
          </span>
        </div>
      </div>
    </div>
  </section>
</template>
