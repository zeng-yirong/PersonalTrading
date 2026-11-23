<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import type {
  OpsEntry,
  OpsFingerprint,
  OpsHealth,
  OpsKind,
  OpsSummary,
  OpsTableStatus,
  OpsUsage,
  OpsUsagePoint,
} from '../types'

type OpsTab = 'ledger' | 'cost' | 'health'

interface UsageTipParam {
  dataIndex?: number
}

const KIND_FILTERS: Array<{ label: string; value: OpsKind | '' }> = [
  { label: '全部', value: '' },
  { label: '推演', value: '推演' },
  { label: '主题', value: '主题' },
  { label: '回测', value: '回测' },
  { label: '成交', value: '成交' },
]

const TAB_ITEMS: Array<{ label: string; value: OpsTab }> = [
  { label: '账本', value: 'ledger' },
  { label: '成本', value: 'cost' },
  { label: '数据健康', value: 'health' },
]

const KIND_CLASS: Record<OpsKind, string> = {
  推演: 'run',
  主题: 'topic',
  回测: 'backtest',
  成交: 'trade',
}

const STATUS_TEXT: Record<OpsTableStatus, string> = {
  ok: '正常',
  stale: '滞后',
  empty: '空表',
  missing: '缺失',
}

const USAGE_COLORS = {
  runs: '#4da3ff',
  topics: '#f3b34c',
  backtests: '#a78bfa',
  factors: '#23c483',
}

const tab = ref<OpsTab>('ledger')
const kindFilter = ref<OpsKind | ''>('')
const usageEl = ref<HTMLDivElement | null>(null)

const summary = ref<OpsSummary | null>(null)
const entries = ref<OpsEntry[]>([])
const usage = ref<OpsUsage | null>(null)
const health = ref<OpsHealth | null>(null)
const fingerprint = ref<OpsFingerprint | null>(null)

const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const reviewBusy = ref(false)
const reviewNote = ref('')

let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

const counts = computed(() => summary.value?.counts ?? null)
const reviews = computed(() => summary.value?.reviews ?? null)
const usageSeries = computed<OpsUsagePoint[]>(() => usage.value?.series ?? [])
const usagePoints = computed<OpsUsagePoint[]>(() => [...usageSeries.value].reverse())
const fingerprintView = computed<OpsFingerprint | null>(
  () => fingerprint.value ?? health.value?.fingerprint ?? null,
)
const healthTables = computed(() => health.value?.tables ?? [])
const partialDays = computed(() => health.value?.partial_days ?? [])
const syncLogs = computed(() => health.value?.recent_sync ?? [])
const healthIssues = computed(() => health.value?.issues ?? [])
const healthTableCounts = computed(() => {
  const rows = healthTables.value
  const bad = rows.filter((row) => row.status !== 'ok').length
  return { total: rows.length, ok: rows.length - bad, bad }
})
const visibleEntries = computed<OpsEntry[]>(() => {
  if (!kindFilter.value) return entries.value
  return entries.value.filter((entry) => entry.kind === kindFilter.value)
})

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

function int(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function scoreText(value: number | null): string {
  if (value === null || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function rateText(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(1)}%`
}

function shortTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isNaN(date.getTime())) {
    const pad = (part: number) => String(part).padStart(2, '0')
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
      date.getMinutes(),
    )}`
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(value)
  return match ? `${match[2]}-${match[3]} ${match[4]}:${match[5]}` : value
}

function kindClass(kind: OpsKind): string {
  return `ops-kind-${KIND_CLASS[kind]}`
}

function statusText(status: OpsTableStatus): string {
  return STATUS_TEXT[status]
}

function chipCount(kind: OpsKind | ''): number {
  if (!kind) return entries.value.length
  return entries.value.filter((entry) => entry.kind === kind).length
}

function tokenTick(value: number): string {
  if (value >= 10000) return `${(value / 10000).toFixed(1)}万`
  return String(value)
}

function disposeChart() {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
}

function usageTip(params: unknown): string {
  const list = Array.isArray(params) ? params : [params]
  const first = list[0] as UsageTipParam | undefined
  const index = first?.dataIndex
  const point = index === undefined ? undefined : usagePoints.value[index]
  if (!point) return ''
  return [
    point.date,
    `推演 ${point.runs} 次 · 主题 ${point.topics} 次`,
    `回测 ${point.backtests} 次 · 因子 ${point.factors} 次`,
    `tokens ${int(point.tokens)} · 调用 ${point.calls} 次`,
  ].join('<br/>')
}

function renderUsageChart() {
  const el = usageEl.value
  const points = usagePoints.value
  if (!el || !points.length) return
  // The chart lives behind a tab: never draw into a detached node after a switch.
  if (chart && chart.getDom() !== el) disposeChart()
  if (!chart) chart = echarts.init(el, undefined, { renderer: 'canvas' })
  chart.setOption(
    {
      backgroundColor: 'transparent',
      animation: false,
      legend: {
        data: ['推演', '主题', '回测', '因子', 'tokens'],
        top: 2,
        right: 8,
        textStyle: { color: '#8fa0b5', fontSize: 11 },
        itemWidth: 12,
        itemHeight: 8,
        itemGap: 12,
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,255,255,0.04)' } },
        backgroundColor: 'rgba(15,21,29,0.96)',
        borderColor: '#223041',
        textStyle: { color: '#e8eef6', fontSize: 12 },
        formatter: usageTip,
      },
      grid: { left: 46, right: 56, top: 40, bottom: 32 },
      xAxis: {
        type: 'category',
        data: points.map((point) => point.date.slice(5)),
        axisLine: { lineStyle: { color: '#223041' } },
        axisLabel: { color: '#8fa0b5', fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: 'value',
          name: '任务数',
          minInterval: 1,
          nameTextStyle: { color: '#8fa0b5', fontSize: 10 },
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: '#8fa0b5', fontSize: 10 },
          splitLine: { lineStyle: { color: 'rgba(34,48,65,0.55)' } },
        },
        {
          type: 'value',
          name: 'tokens',
          nameTextStyle: { color: '#8fa0b5', fontSize: 10 },
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: {
            color: '#8fa0b5',
            fontSize: 10,
            formatter: (value: number) => tokenTick(value),
          },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '推演',
          type: 'bar',
          stack: 'tasks',
          barMaxWidth: 16,
          itemStyle: { color: USAGE_COLORS.runs },
          data: points.map((point) => point.runs),
        },
        {
          name: '主题',
          type: 'bar',
          stack: 'tasks',
          barMaxWidth: 16,
          itemStyle: { color: USAGE_COLORS.topics },
          data: points.map((point) => point.topics),
        },
        {
          name: '回测',
          type: 'bar',
          stack: 'tasks',
          barMaxWidth: 16,
          itemStyle: { color: USAGE_COLORS.backtests },
          data: points.map((point) => point.backtests),
        },
        {
          name: '因子',
          type: 'bar',
          stack: 'tasks',
          barMaxWidth: 16,
          itemStyle: { color: USAGE_COLORS.factors },
          data: points.map((point) => point.factors),
        },
        {
          name: 'tokens',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#8fa0b5', width: 1.5, type: 'dashed' },
          itemStyle: { color: '#8fa0b5' },
          data: points.map((point) => point.tokens),
        },
      ],
    },
    true,
  )
  if (!observer) observer = new ResizeObserver(() => chart?.resize())
  observer.disconnect()
  observer.observe(el)
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const [summaryData, ledgerData, usageData, healthData, fingerprintData] =
      await Promise.all([
        api.opsSummary(),
        api.opsLedger(20),
        api.opsUsage(14),
        api.opsHealth(),
        api.opsFingerprint(),
      ])
    summary.value = summaryData
    entries.value = ledgerData.entries
    usage.value = usageData
    health.value = healthData
    fingerprint.value = fingerprintData
    loaded.value = true
    await nextTick()
    if (tab.value === 'cost') renderUsageChart()
  } catch (exc) {
    error.value = `运维数据加载失败：${messageOf(exc)}`
  } finally {
    loading.value = false
  }
}

async function refreshReviews() {
  if (loading.value || reviewBusy.value) return
  reviewBusy.value = true
  reviewNote.value = ''
  error.value = ''
  try {
    const data = await api.opsReviewRefresh(200)
    reviewNote.value = `写入 ${data.refreshed.written} 条（扫描 ${data.refreshed.runs} 次）`
    await load()
  } catch (exc) {
    error.value = `复盘刷新失败：${messageOf(exc)}`
  } finally {
    reviewBusy.value = false
  }
}

watch(tab, async () => {
  if (tab.value !== 'cost') return
  await nextTick()
  renderUsageChart()
})

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  disposeChart()
})
</script>

<template>
  <section class="panel card ops-panel">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">运维</h3>
        <div class="sub-meta" style="margin-top: 4px">决策账本 / 成本 / 数据健康</div>
      </div>
      <div class="ops-head-right">
        <span
          v-if="fingerprintView"
          class="badge mono"
          :title="`面板构建于 ${shortTime(fingerprintView.built_at)}`"
        >
          指纹 {{ fingerprintView.digest }}
        </span>
        <span v-if="fingerprintView?.as_of" class="badge mono">
          数据日 {{ fingerprintView.as_of }}
        </span>
        <button class="btn ghost" :disabled="loading || reviewBusy" @click="load">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </div>

    <div class="ops-tabs">
      <button
        v-for="item in TAB_ITEMS"
        :key="item.value"
        class="chip ops-tab"
        :class="{ active: tab === item.value }"
        @click="tab = item.value"
      >
        {{ item.label }}
        <span v-if="item.value === 'ledger' && counts" class="ops-tab-note">
          {{ int(counts.runs + counts.topics + counts.backtests + counts.trades) }}
        </span>
      </button>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="loading && !loaded" class="loading" style="margin-top: 12px">
      正在读取决策账本、成本与数据健康…
    </div>

    <template v-if="loaded">
      <div v-if="tab === 'ledger'">
        <div class="ops-metrics">
          <div class="ops-stat">
            <span class="ops-stat-label">推演</span>
            <strong>{{ int(counts?.runs) }}</strong>
            <span class="ops-stat-note">裁定与仓位记录</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">主题</span>
            <strong>{{ int(counts?.topics) }}</strong>
            <span class="ops-stat-note">主题研报</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">回测</span>
            <strong>{{ int(counts?.backtests) }}</strong>
            <span class="ops-stat-note">策略记录</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">成交</span>
            <strong>{{ int(counts?.trades) }}</strong>
            <span class="ops-stat-note">模拟成交</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">复盘覆盖</span>
            <strong>{{ rateText(reviews?.covered_pct) }}</strong>
            <span class="ops-stat-note">（缺 {{ int(reviews?.runs_without_review) }} 条）</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">待验证</span>
            <strong>{{ int(reviews?.pending) }}</strong>
            <span class="ops-stat-note">已判定 {{ int(reviews?.judged) }} 条</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">命中率</span>
            <strong>{{ rateText(reviews?.hit_rate_pct) }}</strong>
            <span class="ops-stat-note">
              对 {{ int(reviews?.correct) }} · 错 {{ int(reviews?.wrong) }}
            </span>
          </div>
        </div>

        <div class="ops-toolbar">
          <div class="ops-filters">
            <button
              v-for="filter in KIND_FILTERS"
              :key="filter.value"
              class="chip"
              :class="{ active: kindFilter === filter.value }"
              :title="`最近 ${entries.length} 条中的条数`"
              @click="kindFilter = filter.value"
            >
              {{ filter.label }}
              <span class="ops-chip-note">{{ chipCount(filter.value) }}</span>
            </button>
          </div>
          <button
            class="btn ghost"
            :disabled="loading || reviewBusy"
            @click="refreshReviews"
          >
            {{ reviewBusy ? '复盘刷新中…' : '刷新复盘' }}
          </button>
        </div>

        <div v-if="reviewNote" class="ops-review-note">
          {{ reviewNote }}
          <span v-if="reviews?.last_refreshed_at">
            ｜最近刷新 {{ shortTime(reviews.last_refreshed_at) }}
          </span>
        </div>

        <div v-if="visibleEntries.length" class="screen-table-wrap ops-table-wrap">
          <table class="screen-table ops-table ops-ledger-table">
            <thead>
              <tr>
                <th>类型</th>
                <th>时间</th>
                <th>标题</th>
                <th>数据日</th>
                <th>结论</th>
                <th title="推演=最终仓位%；主题=信心；回测=区间收益%；成交=金额">
                  仓位/分数
                </th>
                <th>复盘</th>
                <th>tokens</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in visibleEntries" :key="entry.ref">
                <td>
                  <span class="ops-kind" :class="kindClass(entry.kind)">{{ entry.kind }}</span>
                </td>
                <td class="mono">{{ shortTime(entry.created_at) }}</td>
                <td class="ops-ledger-title" :title="entry.title">
                  {{ entry.title || '—' }}
                  <span v-if="entry.symbol" class="ops-symbol mono">{{ entry.symbol }}</span>
                </td>
                <td class="mono">{{ entry.as_of || '—' }}</td>
                <td class="ops-stance" :title="entry.stance">{{ entry.stance || '—' }}</td>
                <td class="mono">{{ scoreText(entry.score) }}</td>
                <td class="ops-review" :title="entry.review_detail || entry.review">
                  {{ entry.review || '—' }}
                </td>
                <td class="mono">{{ int(entry.tokens) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="loading" style="margin-top: 12px">
          {{
            entries.length
              ? '该类型暂无记录。'
              : '账本还没有记录：运行一次推演、主题研究或回测后，这里会出现一条可复盘的结论。'
          }}
        </div>

        <div v-if="entries.length" class="sub-meta" style="margin-top: 8px">
          这里展示最近 {{ entries.length }} 条；筛选在本地完成，不会重新请求。
        </div>
      </div>

      <div v-else-if="tab === 'cost'">
        <div class="ops-metrics">
          <div class="ops-stat">
            <span class="ops-stat-label">总 tokens</span>
            <strong>{{ int(usage?.total_tokens) }}</strong>
            <span class="ops-stat-note">近 {{ usage?.days ?? 14 }} 天</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">任务数</span>
            <strong>{{ int(usage?.total_tasks) }}</strong>
            <span class="ops-stat-note">推演 / 主题 / 回测 / 因子</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">推演 tokens</span>
            <strong>{{ int(summary?.tokens.agent) }}</strong>
            <span class="ops-stat-note">账本累计</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">主题 tokens</span>
            <strong>{{ int(summary?.tokens.topic) }}</strong>
            <span class="ops-stat-note">账本累计</span>
          </div>
        </div>

        <template v-if="usagePoints.length">
          <div class="ops-chart-title">
            近 {{ usage?.days ?? 14 }} 天任务量与 tokens（柱：任务数堆叠；线：tokens，右轴）
          </div>
          <div ref="usageEl" class="ops-usage-chart"></div>
          <div class="ops-block-title">逐日明细</div>
          <div class="screen-table-wrap ops-table-wrap-sm">
            <table class="screen-table ops-table ops-usage-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>推演</th>
                  <th>主题</th>
                  <th>回测</th>
                  <th>因子</th>
                  <th>tokens</th>
                  <th>调用</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="point in usageSeries" :key="point.date">
                  <td class="mono">{{ point.date }}</td>
                  <td>{{ int(point.runs) }}</td>
                  <td>{{ int(point.topics) }}</td>
                  <td>{{ int(point.backtests) }}</td>
                  <td>{{ int(point.factors) }}</td>
                  <td class="mono">{{ int(point.tokens) }}</td>
                  <td>{{ int(point.calls) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <div v-else class="loading" style="margin-top: 12px">
          近 {{ usage?.days ?? 14 }} 天还没有调用记录。
        </div>
      </div>

      <div v-else-if="tab === 'health'">
        <div v-if="health" class="ops-metrics">
          <div class="ops-stat" :class="health.ok ? 'ok' : 'bad'">
            <span class="ops-stat-label">数据状态</span>
            <strong>{{ health.ok ? '正常' : '有问题' }}</strong>
            <span class="ops-stat-note">
              {{ healthTableCounts.ok }} 张正常 · {{ healthTableCounts.bad }} 张需关注
            </span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">面板行数</span>
            <strong>{{ int(fingerprintView?.bars) }}</strong>
            <span class="ops-stat-note">自 {{ fingerprintView?.first_date || '—' }}</span>
          </div>
          <div class="ops-stat">
            <span class="ops-stat-label">检查时间</span>
            <strong>{{ shortTime(health.checked_at) }}</strong>
            <span class="ops-stat-note">数据截至 {{ health.as_of || '—' }}</span>
          </div>
        </div>

        <div v-if="healthIssues.length" class="warning-bar ops-issues">
          <ul>
            <li v-for="(issue, index) in healthIssues" :key="index">{{ issue }}</li>
          </ul>
        </div>

        <div v-if="healthTables.length" class="ops-health-block">
          <div class="ops-block-title">数据表状态（{{ healthTables.length }}）</div>
          <div class="screen-table-wrap ops-table-wrap-sm">
            <table class="screen-table ops-table ops-health-table">
              <thead>
                <tr>
                  <th>数据表</th>
                  <th>状态</th>
                  <th>最新日期</th>
                  <th>行数</th>
                  <th>延迟</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in healthTables" :key="row.table">
                  <td>
                    <span class="ops-table-label">{{ row.label }}</span>
                    <span class="ops-table-name mono">{{ row.table }}</span>
                  </td>
                  <td>
                    <span class="ops-dot" :class="`ops-dot-${row.status}`"></span>
                    {{ statusText(row.status) }}
                  </td>
                  <td class="mono">{{ row.last_date || '—' }}</td>
                  <td class="mono">{{ int(row.rows) }}</td>
                  <td class="mono" :title="`期望 ≤ ${row.expected_lag} 天`">
                    {{ row.lag_days === null ? '—' : `${row.lag_days} 天` }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="partialDays.length" class="ops-health-block">
          <div class="ops-block-title">不完整交易日（{{ partialDays.length }}）</div>
          <div class="screen-table-wrap ops-table-wrap-sm">
            <table class="screen-table ops-table ops-partial-table">
              <thead>
                <tr>
                  <th>数据表</th>
                  <th>日期</th>
                  <th>行数</th>
                  <th>应有</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in partialDays" :key="`${row.table}-${row.date}`">
                  <td class="mono">{{ row.table }}</td>
                  <td class="mono">{{ row.date }}</td>
                  <td class="mono">{{ int(row.rows) }}</td>
                  <td class="mono">{{ int(row.expected) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="syncLogs.length" class="ops-health-block">
          <div class="ops-block-title">最近同步（{{ syncLogs.length }}）</div>
          <div class="screen-table-wrap ops-table-wrap-sm">
            <table class="screen-table ops-table ops-sync-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>状态</th>
                  <th>行数</th>
                  <th>开始时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in syncLogs" :key="`${row.job}-${row.started_at}`">
                  <td class="mono">{{ row.job }}</td>
                  <td>
                    <span :class="row.status === 'ok' ? 'down' : 'up'">{{ row.status }}</span>
                  </td>
                  <td class="mono">{{ int(row.rows) }}</td>
                  <td class="mono">{{ shortTime(row.started_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="!health" class="loading" style="margin-top: 12px">暂无数据健康信息。</div>
      </div>
    </template>
  </section>
</template>
