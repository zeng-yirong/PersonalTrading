<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import type {
  FactorIcPoint,
  FactorMeta,
  FactorQuantile,
  FactorRequest,
  FactorResultItem,
  FactorRunResult,
  FactorRunSummary,
  FactorSpec,
} from '../types'

interface RankedFactor extends FactorResultItem {
  rank: number
}

interface TooltipParam {
  dataIndex?: number
}

interface AxisExtent {
  min: number
  max: number
}

const root = ref<HTMLElement | null>(null)
const resultHead = ref<HTMLElement | null>(null)
const icEl = ref<HTMLDivElement | null>(null)
const quantEl = ref<HTMLDivElement | null>(null)

const meta = ref<FactorMeta | null>(null)
const startDate = ref('')
const endDate = ref('')
const rebalance = ref(20)
const horizon = ref(20)

const loading = ref(false)
const loadingText = ref('')
const error = ref('')
const result = ref<FactorRunResult | null>(null)
const runs = ref<FactorRunSummary[]>([])
const selectedName = ref<string | null>(null)
const category = ref('')
const elapsedMs = ref<number | null>(null)

let icChart: echarts.ECharts | null = null
let quantChart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

const rebalanceChoices = computed<number[]>(
  () => meta.value?.limits.rebalance ?? [5, 10, 20, 60],
)
const horizons = computed<number[]>(() => meta.value?.limits.horizons ?? [5, 10, 20, 60])
const metrics = computed(() => meta.value?.metrics ?? null)
const factorTotal = computed(() => meta.value?.factors.length ?? 37)

const factorNotes = computed<Record<string, string>>(() => {
  const notes: Record<string, string> = {}
  for (const item of meta.value?.factors ?? []) notes[item.name] = item.note
  return notes
})

const rankedFactors = computed<RankedFactor[]>(() =>
  (result.value?.factors ?? []).map((item, index) => ({ ...item, rank: index + 1 })),
)

const categories = computed<Array<{ name: string; count: number }>>(() => {
  const counts = new Map<string, number>()
  for (const item of result.value?.factors ?? []) {
    counts.set(item.category, (counts.get(item.category) ?? 0) + 1)
  }
  return Array.from(counts, ([name, count]) => ({ name, count }))
})

const visibleFactors = computed<RankedFactor[]>(() => {
  if (!category.value) return rankedFactors.value
  return rankedFactors.value.filter((item) => item.category === category.value)
})

const selectedFactor = computed<FactorResultItem | null>(() => {
  const name = selectedName.value
  if (!name) return null
  return result.value?.factors.find((item) => item.name === name) ?? null
})

const elapsedText = computed<string>(() => {
  if (elapsedMs.value === null) return ''
  return (elapsedMs.value / 1000).toFixed(1)
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

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`
}

function covPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const pct = value * 100
  return `${pct >= 99.95 ? Math.round(pct) : pct.toFixed(1)}%`
}

function sharePct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

function spreadText(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)} 个百分点`
}

function avgNames(quantiles: FactorQuantile[]): string {
  if (!quantiles.length) return '—'
  const total = quantiles.reduce((sum, item) => sum + item.avg_names, 0)
  return (total / quantiles.length).toFixed(0)
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false })
}

function factorNote(name: string): string {
  return factorNotes.value[name] ?? ''
}

function rowTitle(item: FactorResultItem): string {
  if (!item.usable) return `覆盖率 ${covPct(item.coverage)}，样本过窄`
  const note = factorNote(item.name)
  return note ? `${item.expected}｜${note}` : item.expected
}

function observedText(item: FactorResultItem): string {
  if (!Number.isFinite(item.ic_mean) || item.ic_mean === 0) return '实测无明显相关'
  return item.ic_mean > 0 ? '实测正相关' : '实测负相关'
}

function alignText(item: FactorResultItem): string {
  if (item.direction === 0 || item.aligned === null) return '无先验方向'
  return item.aligned ? '与先验一致' : '与先验相反'
}

function alignTone(item: FactorResultItem): string {
  if (item.direction === 0 || item.aligned === null) return 'flat'
  return item.aligned ? 'align-ok' : 'align-bad'
}

function buildRequest(): FactorRequest {
  const payload: FactorRequest = {
    rebalance: rebalance.value,
    horizon: horizon.value,
  }
  if (startDate.value) payload.start = startDate.value
  if (endDate.value) payload.end = endDate.value
  return payload
}

function disposeCharts() {
  observer?.disconnect()
  observer = null
  icChart?.dispose()
  icChart = null
  quantChart?.dispose()
  quantChart = null
}

function observeResize(el: HTMLElement) {
  if (!observer) {
    observer = new ResizeObserver(() => {
      icChart?.resize()
      quantChart?.resize()
    })
  }
  observer.observe(el)
}

function renderIcChart(points: FactorIcPoint[]) {
  const el = icEl.value
  if (!el || !points.length) {
    icChart?.dispose()
    icChart = null
    return
  }
  // A cleared result removes the container: never draw into a detached node.
  if (icChart && icChart.getDom() !== el) {
    icChart.dispose()
    icChart = null
  }
  if (!icChart) icChart = echarts.init(el, undefined, { renderer: 'canvas' })
  icChart.setOption(
    {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,255,255,0.04)' } },
        backgroundColor: 'rgba(15,21,29,0.96)',
        borderColor: '#223041',
        textStyle: { color: '#e8eef6', fontSize: 12 },
        valueFormatter: (value: unknown) =>
          typeof value === 'number' ? value.toFixed(4) : '—',
      },
      grid: { left: 46, right: 14, top: 14, bottom: 30 },
      xAxis: {
        type: 'category',
        data: points.map((item) => item.date),
        axisLine: { lineStyle: { color: '#223041' } },
        axisLabel: {
          color: '#8fa0b5',
          fontSize: 10,
          formatter: (value: string) => String(value).slice(2),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        // Keep the zero axis in range so the markLine stays visible whichever
        // way the IC leans.
        min: (extent: AxisExtent) => Math.min(0, extent.min),
        max: (extent: AxisExtent) => Math.max(0, extent.max),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#8fa0b5', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(34,48,65,0.55)' } },
      },
      series: [
        {
          name: 'IC',
          type: 'bar',
          barMaxWidth: 22,
          data: points.map((item) => ({
            value: item.ic,
            itemStyle: { color: item.ic >= 0 ? '#ff5d5d' : '#23c483' },
          })),
          markLine: {
            silent: true,
            symbol: 'none',
            label: { show: false },
            lineStyle: { color: '#8fa0b5', type: 'dashed', width: 1 },
            data: [{ yAxis: 0 }],
          },
        },
      ],
    },
    true,
  )
  observeResize(el)
}

function quantileTip(params: unknown): string {
  const list = Array.isArray(params) ? params : [params]
  const first = list[0] as TooltipParam | undefined
  const index = first?.dataIndex
  const item = selectedFactor.value
  if (index === undefined || !item) return ''
  const quantile = item.quantiles[index]
  if (!quantile) return ''
  return [
    `Q${quantile.q}`,
    `平均前瞻收益 ${signed(quantile.mean_pct, 2)}%`,
    `中位 ${signed(quantile.median_pct, 2)}%`,
    `平均 ${quantile.avg_names.toFixed(0)} 只 · ${quantile.periods} 期`,
  ].join('<br/>')
}

function renderQuantChart(quantiles: FactorQuantile[]) {
  const el = quantEl.value
  if (!el || !quantiles.length) {
    quantChart?.dispose()
    quantChart = null
    return
  }
  if (quantChart && quantChart.getDom() !== el) {
    quantChart.dispose()
    quantChart = null
  }
  if (!quantChart) quantChart = echarts.init(el, undefined, { renderer: 'canvas' })
  quantChart.setOption(
    {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(15,21,29,0.96)',
        borderColor: '#223041',
        textStyle: { color: '#e8eef6', fontSize: 12 },
        formatter: quantileTip,
      },
      grid: { left: 52, right: 16, top: 14, bottom: 30 },
      xAxis: {
        type: 'category',
        data: quantiles.map((item) => `Q${item.q}`),
        axisLine: { lineStyle: { color: '#223041' } },
        axisLabel: { color: '#8fa0b5', fontSize: 11 },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        // Bars read from a zero baseline: never crop the axis around the data.
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#8fa0b5',
          fontSize: 10,
          formatter: (value: number) => `${value}%`,
        },
        splitLine: { lineStyle: { color: 'rgba(34,48,65,0.55)' } },
      },
      series: [
        {
          name: '平均前瞻收益',
          type: 'bar',
          barMaxWidth: 46,
          data: quantiles.map((item) => ({
            value: item.mean_pct,
            itemStyle: { color: item.mean_pct >= 0 ? '#ff5d5d' : '#23c483' },
          })),
        },
      ],
    },
    true,
  )
  observeResize(el)
}

function renderCharts() {
  const factor = selectedFactor.value
  if (!factor) {
    disposeCharts()
    return
  }
  renderIcChart(factor.ic_series)
  renderQuantChart(factor.quantiles)
}

async function loadMeta() {
  try {
    const data = await api.factorLibrary()
    meta.value = data
    // A history run opened while meta was still loading owns the controls.
    if (result.value) return
    rebalance.value = data.defaults.rebalance
    horizon.value = data.defaults.horizon
    if (data.panel.end) {
      endDate.value = data.panel.end
      startDate.value = shiftYears(data.panel.end, data.defaults.start_years || 3)
    }
  } catch (exc) {
    error.value = `因子库加载失败：${messageOf(exc)}`
  }
}

async function loadRuns() {
  try {
    const data = await api.factorRuns(20)
    runs.value = data.runs
  } catch {
    /* history is optional: the form still works without it */
  }
}

function syncControls(config: FactorSpec) {
  startDate.value = config.start
  endDate.value = config.end
  rebalance.value = config.rebalance
  horizon.value = config.horizon
}

function clearResult() {
  result.value = null
  selectedName.value = null
  category.value = ''
  disposeCharts()
}

async function applyResult(data: FactorRunResult) {
  result.value = data
  category.value = ''
  selectedName.value = data.factors[0]?.name ?? null
  await nextTick()
  renderCharts()
  resultHead.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function run() {
  if (loading.value) return
  loading.value = true
  loadingText.value = '评估中：逐截面计算秩相关与分层…'
  error.value = ''
  elapsedMs.value = null
  clearResult()
  const startedAt = Date.now()
  try {
    const data = await api.factorRun(buildRequest())
    elapsedMs.value = Date.now() - startedAt
    await applyResult(data)
    await loadRuns()
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    loading.value = false
    loadingText.value = ''
  }
}

async function loadRun(id: number) {
  if (loading.value) return
  loading.value = true
  loadingText.value = `读取历史评估 #${id}…`
  error.value = ''
  elapsedMs.value = null
  try {
    const data = await api.factorRunDetail(id)
    syncControls(data.config)
    await applyResult(data)
  } catch (exc) {
    error.value = `读取历史评估失败：${messageOf(exc)}`
  } finally {
    loading.value = false
    loadingText.value = ''
  }
}

function selectFactor(name: string) {
  selectedName.value = name
}

watch(selectedFactor, async () => {
  await nextTick()
  renderCharts()
})

onMounted(() => {
  void loadMeta()
  void loadRuns()
})

onBeforeUnmount(() => {
  disposeCharts()
})
</script>

<template>
  <section ref="root" class="panel card factor-panel">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">因子挖掘</h3>
        <div class="sub-meta" style="margin-top: 4px">
          在真实面板上算 IC/ICIR 与分层收益，看哪个信号真的能区分未来涨跌
          <span v-if="meta?.panel.end">
            （数据区间 {{ meta.panel.start }} 至 {{ meta.panel.end }}）
          </span>
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px">
        <button class="btn ghost" :disabled="loading" @click="loadRuns">刷新历史</button>
        <button class="btn" :disabled="loading" @click="run">
          {{ loading ? '评估中…' : '开始评估' }}
        </button>
      </div>
    </div>

    <div class="factor-controls">
      <div class="factor-field">
        <label for="factor-start">开始日期</label>
        <input id="factor-start" v-model="startDate" type="date" />
      </div>
      <div class="factor-field">
        <label for="factor-end">结束日期</label>
        <input id="factor-end" v-model="endDate" type="date" />
      </div>
      <div class="factor-field">
        <label for="factor-rebalance">截面间隔</label>
        <select id="factor-rebalance" v-model.number="rebalance">
          <option v-for="choice in rebalanceChoices" :key="choice" :value="choice">
            {{ choice }}日
          </option>
        </select>
      </div>
      <div class="factor-field">
        <label for="factor-horizon">前瞻期</label>
        <select id="factor-horizon" v-model.number="horizon">
          <option v-for="choice in horizons" :key="choice" :value="choice">
            {{ choice }}日
          </option>
        </select>
      </div>
    </div>

    <div class="sub-meta" style="margin-top: 8px">
      一次评估全部 {{ factorTotal }} 个因子，约需 10-20 秒
      <span v-if="meta">
        ｜单次最长 {{ meta.limits.max_years }} 年｜至少
        {{ meta.limits.min_sections }} 个有效截面
      </span>
    </div>

    <div v-if="metrics" class="factor-footnote">
      <div class="factor-footnote-row">
        <span class="factor-footnote-label">IC</span>
        <span>{{ metrics.ic }}</span>
      </div>
      <div class="factor-footnote-row">
        <span class="factor-footnote-label">ICIR</span>
        <span>{{ metrics.icir }}</span>
      </div>
      <div class="factor-footnote-row">
        <span class="factor-footnote-label">t值</span>
        <span>{{ metrics.t_stat }}</span>
      </div>
      <div class="factor-footnote-row">
        <span class="factor-footnote-label">分层价差</span>
        <span>{{ metrics.spread }}</span>
      </div>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="loadingText" class="factor-progress">{{ loadingText }}</div>

    <template v-if="result">
      <div ref="resultHead" class="factor-result-head">
        <div>
          <strong>{{ result.config.start }} → {{ result.config.end }}</strong>
          <span class="badge" style="margin-left: 8px">#{{ result.id }}</span>
          <div class="sub-meta" style="margin: 4px 0 0">
            {{ result.sections }} 个截面｜{{ result.evaluated_dates }} 个有效期｜{{
              result.factors.length
            }}
            个因子
            <span v-if="elapsedText">｜耗时 {{ elapsedText }} 秒</span>
            <span v-if="result.created_at">｜{{ fmtTime(result.created_at) }}</span>
          </div>
        </div>
        <span class="sub-meta" style="margin: 0">
          {{ result.config.rebalance }}日截面 · 前瞻 {{ result.config.horizon }}日
        </span>
      </div>

      <div class="factor-categories">
        <button
          class="chip factor-chip"
          :class="{ active: category === '' }"
          @click="category = ''"
        >
          全部
          <span class="factor-chip-note">{{ result.factors.length }}</span>
        </button>
        <button
          v-for="item in categories"
          :key="item.name"
          class="chip factor-chip"
          :class="{ active: category === item.name }"
          @click="category = item.name"
        >
          {{ item.name }}
          <span class="factor-chip-note">{{ item.count }}</span>
        </button>
      </div>

      <div class="screen-table-wrap factor-table-wrap">
        <table class="screen-table factor-table">
          <thead>
            <tr>
              <th>#</th>
              <th>因子</th>
              <th>类别</th>
              <th>覆盖</th>
              <th>IC均值</th>
              <th>ICIR</th>
              <th>t值</th>
              <th>正IC占比</th>
              <th>分层价差</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in visibleFactors"
              :key="row.name"
              class="screen-row factor-row"
              :class="{
                'factor-row-active': row.name === selectedName,
                'factor-row-dim': !row.usable,
              }"
              :title="rowTitle(row)"
              @click="selectFactor(row.name)"
            >
              <td class="mono">{{ row.rank }}</td>
              <td>
                <span class="factor-name">{{ row.label }}</span>
                <span class="factor-code mono">{{ row.name }}</span>
              </td>
              <td>{{ row.category }}</td>
              <td :title="`${row.distinct} 个不同取值`">{{ covPct(row.coverage) }}</td>
              <td :class="tone(row.ic_mean)">{{ signed(row.ic_mean, 4) }}</td>
              <td :class="tone(row.icir)">{{ signed(row.icir, 3) }}</td>
              <td>{{ signed(row.t_stat, 2) }}</td>
              <td>{{ sharePct(row.positive_ratio) }}</td>
              <td :class="tone(row.spread_pct)" title="Q5 − Q1，百分点">
                {{ num(row.spread_pct, 2) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="selectedFactor" class="factor-detail">
        <div class="factor-detail-head">
          <div>
            <div class="factor-detail-title">
              <strong>{{ selectedFactor.label }}</strong>
              <span class="factor-code mono">{{ selectedFactor.name }}</span>
              <span class="factor-tag">{{ selectedFactor.category }}</span>
            </div>
            <div class="factor-tags">
              <span class="factor-tag">先验 {{ selectedFactor.expected }}</span>
              <span class="factor-tag" :class="tone(selectedFactor.ic_mean)">
                {{ observedText(selectedFactor) }}
              </span>
              <span class="factor-tag" :class="alignTone(selectedFactor)">
                {{ alignText(selectedFactor) }}
              </span>
              <span v-if="factorNote(selectedFactor.name)" class="factor-note">
                {{ factorNote(selectedFactor.name) }}
              </span>
            </div>
          </div>
          <div class="factor-detail-meta">
            <span>t值 {{ signed(selectedFactor.t_stat, 2) }}</span>
            <span>IC均值 {{ signed(selectedFactor.ic_mean, 4) }}</span>
            <span>ICIR {{ signed(selectedFactor.icir, 3) }}</span>
            <span>{{ selectedFactor.n_dates }} 个有效期</span>
            <span>覆盖 {{ covPct(selectedFactor.coverage) }}</span>
            <span>{{ selectedFactor.distinct }} 个不同取值</span>
          </div>
        </div>

        <div class="factor-charts">
          <div class="factor-chart-block">
            <div class="factor-chart-title">IC 时序（逐截面秩相关，虚线为零轴）</div>
            <div v-if="selectedFactor.ic_series.length" ref="icEl" class="factor-chart"></div>
            <div v-else class="factor-chart-empty">该因子没有可用的 IC 序列</div>
          </div>
          <div class="factor-chart-block">
            <div class="factor-chart-title">分层收益（Q1 → Q5 平均前瞻收益）</div>
            <div v-if="selectedFactor.quantiles.length" ref="quantEl" class="factor-chart"></div>
            <div v-else class="factor-chart-empty">
              该因子取值过少，不做分组（只看秩相关）
            </div>
          </div>
        </div>

        <div class="sub-meta" style="margin: 8px 0 0">
          分层价差 Q5 − Q1：
          <span :class="tone(selectedFactor.spread_pct)">
            {{ spreadText(selectedFactor.spread_pct) }}
          </span>
          <span v-if="selectedFactor.quantiles.length">
            ｜每层每期约 {{ avgNames(selectedFactor.quantiles) }} 只
          </span>
        </div>
      </div>
    </template>

    <div v-if="!result && !loading" class="loading" style="margin-top: 12px">
      选好区间后点「开始评估」：会逐截面算秩相关，并按因子值分五层看未来收益。
    </div>

    <div v-if="runs.length" class="factor-history">
      <div class="sub-meta" style="margin: 0 0 6px">历史评估（{{ runs.length }}）——点击载入</div>
      <div class="factor-run-list">
        <div class="factor-run-row head">
          <span>#</span>
          <span>区间 / 参数</span>
          <span>截面 / 有效期</span>
          <span>最优因子</span>
          <span>ICIR</span>
        </div>
        <div
          v-for="run in runs"
          :key="run.id"
          class="factor-run-row"
          :class="{ active: result?.id === run.id }"
          @click="loadRun(run.id)"
        >
          <span class="mono">#{{ run.id }}</span>
          <span class="factor-run-title">
            <span>{{ run.spec.start ?? '—' }} → {{ run.spec.end ?? '—' }}</span>
            <span class="sub-meta" style="margin: 0">
              {{ run.spec.rebalance ?? '—' }}日截面 · 前瞻
              {{ run.spec.horizon ?? '—' }}日 · {{ run.spec.factors.length }} 个因子 ·
              {{ fmtTime(run.created_at) }}
            </span>
          </span>
          <span>{{ run.sections }} / {{ run.evaluated_dates }}</span>
          <span>
            {{ run.top_factor.label }}
            <span class="factor-code mono">{{ run.top_factor.name }}</span>
          </span>
          <span :class="tone(run.top_factor.icir)">{{ signed(run.top_factor.icir, 3) }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
