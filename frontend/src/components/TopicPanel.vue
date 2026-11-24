<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../api'
import type {
  QuestionGroup,
  StockContextPayload,
  TopicContext,
  TopicMeta,
  TopicReportDetail,
  TopicReportSummary,
  TopicStockBrief,
} from '../types'
import QuestionSuggest from './QuestionSuggest.vue'

const QUESTION_GROUPS: QuestionGroup[] = [
  {
    label: '大盘',
    questions: [
      '今天大盘怎么走？',
      '两市成交额和量能处于什么水平？',
      '当前市场情绪和涨跌停结构如何？',
      '大盘指数在均线什么位置？',
    ],
  },
  {
    label: '行业',
    questions: [
      '白酒行业还能拿吗？',
      '银行板块为什么领涨？',
      '半导体行业当前景气度如何？',
      '哪些行业资金流入居前？',
    ],
  },
  {
    label: '个股',
    questions: [
      '这只股票的估值贵不贵？',
      '当前技术面处于什么位置？',
      '主力资金最近在买还是卖？',
      '基本面有没有改善迹象？',
    ],
  },
  {
    label: '风险',
    questions: [
      '现在最大的市场风险是什么？',
      '如果大盘继续下跌，哪些板块更抗跌？',
      '当前仓位应该控制在多少？',
      '两融和资金面有什么风险信号？',
    ],
  },
]

const emit = defineEmits<{
  (e: 'context', payload: StockContextPayload): void
}>()

const resultHead = ref<HTMLElement | null>(null)
const suggestRef = ref<InstanceType<typeof QuestionSuggest> | null>(null)

const meta = ref<TopicMeta | null>(null)
const topic = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<TopicReportDetail | null>(null)
const reports = ref<TopicReportSummary[]>([])
const historyLoading = ref(false)

// One snapshot per report id: reopening a result should not spend another query.
const snapshots = ref<Record<number, TopicContext>>({})
const snapshotOpen = ref(false)
const snapshotLoadingId = ref<number | null>(null)
const snapshotError = ref('')

const examples = computed(() => meta.value?.examples ?? [])
const industries = computed(() => (meta.value?.industries ?? []).slice(0, 10))
const canSubmit = computed(() => topic.value.trim().length >= 2 && !loading.value)
const snapshot = computed<TopicContext | null>(() => {
  const id = result.value?.id
  if (!id) return null
  return snapshots.value[id] ?? null
})
const snapshotLoading = computed(
  () => result.value !== null && snapshotLoadingId.value === result.value.id,
)

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

function render(content: string) {
  const html = marked.parse(content) as string
  return DOMPurify.sanitize(html)
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false })
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function ratio(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function int(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function yi(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)} 亿`
}

function yiSeries(values: number[] | null | undefined): string {
  if (!values || !values.length) return '—'
  return `${values.map((value) => value.toFixed(1)).join(' / ')} 亿`
}

function verdictClass(verdict: string): string {
  if (verdict === '看多') return 'up'
  if (verdict === '看空') return 'down'
  return 'flat'
}

async function loadMeta() {
  try {
    meta.value = await api.topicMeta()
  } catch {
    /* meta only powers examples and shortcuts: typing a theme still works */
  }
}

async function loadReports() {
  try {
    const data = await api.topicReports(20)
    reports.value = data.reports
  } catch {
    /* history is optional: generation and snapshots still work without it */
  }
}

function fillTopic(value: string) {
  if (loading.value) return
  topic.value = value
  suggestRef.value?.focus()
}

function openContext(stock: TopicStockBrief, event: MouseEvent) {
  emit('context', {
    symbol: stock.symbol,
    name: stock.name,
    x: event.clientX,
    y: event.clientY,
  })
}

async function generate() {
  const value = topic.value.trim()
  if (value.length < 2 || loading.value) return
  loading.value = true
  error.value = ''
  result.value = null
  snapshotOpen.value = false
  snapshotError.value = ''
  try {
    const data = await api.topicResearch(value)
    result.value = data
    topic.value = data.topic || value
    await loadReports()
    await nextTick()
    resultHead.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    loading.value = false
  }
}

async function loadReport(id: number) {
  if (loading.value || historyLoading.value) return
  historyLoading.value = true
  error.value = ''
  snapshotOpen.value = false
  snapshotError.value = ''
  try {
    const data = await api.topicReport(id)
    result.value = data
    topic.value = data.topic || topic.value
    await nextTick()
    resultHead.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } catch (exc) {
    error.value = `读取历史报告失败：${messageOf(exc)}`
  } finally {
    historyLoading.value = false
  }
}

async function toggleSnapshot() {
  const current = result.value
  if (!current) return
  if (snapshotOpen.value) {
    snapshotOpen.value = false
    return
  }
  snapshotOpen.value = true
  snapshotError.value = ''
  if (snapshots.value[current.id]) return
  if (snapshotLoadingId.value !== null) return
  snapshotLoadingId.value = current.id
  try {
    const data = await api.topicContext(current.topic)
    snapshots.value = { ...snapshots.value, [current.id]: data }
  } catch (exc) {
    snapshotError.value = `快照读取失败：${messageOf(exc)}`
  } finally {
    snapshotLoadingId.value = null
  }
}

onMounted(() => {
  void loadMeta()
  void loadReports()
})
</script>

<template>
  <section class="panel card topic-panel">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">主题研究</h3>
        <div class="sub-meta" style="margin-top: 4px">
          基于本地面板快照：指数、行业轮动、涨跌停、成交额、两融、资金流；缺数据会明说
          <span v-if="meta?.as_of">（数据截至 {{ meta.as_of }}）</span>
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px">
        <button class="btn ghost" :disabled="loading" @click="loadReports">刷新历史</button>
        <button class="btn" :disabled="!canSubmit" @click="generate">
          {{ loading ? '生成中…' : '生成报告' }}
        </button>
      </div>
    </div>

    <QuestionSuggest
      ref="suggestRef"
      v-model="topic"
      :groups="QUESTION_GROUPS"
      :rows="2"
      :maxlength="80"
      :disabled="loading"
      placeholder="例如：白酒行业还能拿吗 / 大盘会不会继续跌 / 市场情绪现在处于什么位置"
      @submit="generate"
    />

    <div v-if="examples.length" class="screen-presets">
      <button
        v-for="example in examples"
        :key="example"
        class="chip"
        :disabled="loading"
        @click="fillTopic(example)"
      >
        {{ example }}
      </button>
    </div>

    <div v-if="industries.length" class="topic-industry-picks">
      <span class="sub-meta" style="margin: 0">行业快选：</span>
      <button
        v-for="industry in industries"
        :key="industry.name"
        class="chip"
        :disabled="loading"
        @click="fillTopic(industry.name)"
      >
        {{ industry.name }}
        <span class="topic-chip-note">{{ industry.stocks }}</span>
      </button>
    </div>

    <div class="sub-meta" style="margin-top: 8px">
      Ctrl+Enter 生成｜报告约需 30-90 秒｜已存 {{ reports.length }} 份历史
      <span v-if="meta">｜模型 {{ meta.model }}</span>
    </div>

    <div v-if="meta && !meta.llm_available" class="warning-bar" style="margin-top: 10px">
      未配置模型：可以查看历史报告与数据快照，生成会被后端拒绝。
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="loading" class="topic-progress">
      报告约需 30-90 秒：先取快照，再写结论…
    </div>

    <template v-if="result">
      <div ref="resultHead" class="topic-result-head">
        <div class="topic-result-title">
          <strong>{{ result.title }}</strong>
          <span class="topic-verdict" :class="verdictClass(result.verdict)">
            {{ result.verdict }}
          </span>
          <span class="badge">信心 {{ result.confidence }}/100</span>
          <span class="badge">{{ result.scope }}</span>
          <span class="badge">数据截至 {{ result.as_of }}</span>
          <span class="badge mono">#{{ result.id }}</span>
        </div>
        <div class="sub-meta" style="margin: 6px 0 0">
          主题：{{ result.topic }}｜模型 {{ result.model }}｜{{ fmtTime(result.created_at) }}
          <span v-if="result.usage">
            ｜{{ result.usage.total_tokens }} tokens（{{ result.usage.calls }} 次调用）
          </span>
        </div>
      </div>

      <div class="markdown topic-markdown" v-html="render(result.markdown)"></div>

      <div class="topic-snapshot">
        <div class="topic-snapshot-head">
          <div>
            <strong class="topic-snapshot-title">数据快照</strong>
            <span class="sub-meta" style="margin: 0 0 0 8px">
              这份报告实际用到的面板数字，不调用模型
            </span>
          </div>
          <button class="btn ghost" :disabled="snapshotLoading" @click="toggleSnapshot">
            {{ snapshotLoading ? '读取快照…' : snapshotOpen ? '收起快照' : '查看快照' }}
          </button>
        </div>

        <div v-if="snapshotOpen" class="topic-snapshot-body">
          <div v-if="snapshotError" class="error-bar">{{ snapshotError }}</div>
          <div v-else-if="snapshotLoading" class="loading">读取数据快照…</div>
          <template v-else-if="snapshot">
            <div class="sub-meta" style="margin: 0 0 8px">
              快照日期 {{ snapshot.as_of }}｜覆盖 {{ snapshot.summary.industry_count }} 个行业、
              {{ snapshot.summary.concept_count }} 个题材概念
            </div>

            <div class="topic-snap-grid">
              <div v-if="snapshot.summary.breadth" class="topic-snap-block">
                <span class="topic-snap-label">市场宽度</span>
                <strong class="topic-snap-value">
                  {{ int(snapshot.summary.breadth?.stocks) }} 只
                </strong>
                <div class="topic-snap-line">
                  <span class="up">上涨 {{ int(snapshot.summary.breadth?.up) }}</span>
                  <span class="down">下跌 {{ int(snapshot.summary.breadth?.down) }}</span>
                  <span class="flat">平盘 {{ int(snapshot.summary.breadth?.flat) }}</span>
                </div>
                <div class="topic-snap-line">
                  上涨占比 {{ ratio(snapshot.summary.breadth?.up_ratio) }}｜站上 MA20
                  {{ ratio(snapshot.summary.breadth?.above_ma20_ratio) }}｜MA60
                  {{ ratio(snapshot.summary.breadth?.above_ma60_ratio) }}
                </div>
              </div>

              <div v-if="snapshot.summary.turnover?.date" class="topic-snap-block">
                <span class="topic-snap-label">成交额</span>
                <strong class="topic-snap-value">
                  {{ yi(snapshot.summary.turnover?.amount_yi) }}
                </strong>
                <div class="topic-snap-line">
                  20日均 {{ yi(snapshot.summary.turnover?.amount_20d_avg_yi) }}｜量比
                  {{ num(snapshot.summary.turnover?.ratio_to_20d_avg) }}
                </div>
                <div class="topic-snap-line">
                  近5日：{{ yiSeries(snapshot.summary.turnover?.last5_yi) }}
                </div>
              </div>

              <div v-if="snapshot.summary.limits?.date" class="topic-snap-block">
                <span class="topic-snap-label">涨跌停</span>
                <strong class="topic-snap-value">
                  <span class="up">涨停 {{ int(snapshot.summary.limits?.limit_up) }}</span>
                  /
                  <span class="down">跌停 {{ int(snapshot.summary.limits?.limit_down) }}</span>
                </strong>
                <div class="topic-snap-line">
                  5日均：涨停 {{ num(snapshot.summary.limits?.limit_up_5d_avg, 1) }}、跌停
                  {{ num(snapshot.summary.limits?.limit_down_5d_avg, 1) }}
                </div>
                <div class="topic-snap-line">日期 {{ snapshot.summary.limits?.date }}</div>
              </div>

              <div v-if="snapshot.summary.margin?.date" class="topic-snap-block">
                <span class="topic-snap-label">两融</span>
                <strong class="topic-snap-value">
                  {{ yi(snapshot.summary.margin?.balance_yi) }}
                </strong>
                <div class="topic-snap-line">
                  20日变化
                  <span :class="tone(snapshot.summary.margin?.chg_20d_pct)">
                    {{ pct(snapshot.summary.margin?.chg_20d_pct) }}
                  </span>
                  ｜覆盖 {{ int(snapshot.summary.margin?.stocks) }} 只
                </div>
                <div
                  v-if="snapshot.summary.margin?.skipped_partial_days?.length"
                  class="topic-snap-line"
                >
                  已跳过不完整日期：{{
                    snapshot.summary.margin?.skipped_partial_days?.join('、')
                  }}
                </div>
              </div>
            </div>

            <div class="topic-matched">
              <span class="topic-snap-label">匹配行业</span>
              <template v-if="snapshot.summary.matched_industries.length">
                <span
                  v-for="name in snapshot.summary.matched_industries"
                  :key="`industry-${name}`"
                  class="badge"
                >
                  {{ name }}
                </span>
              </template>
              <span v-else class="flat">无</span>
              <span class="topic-snap-label">匹配题材</span>
              <template v-if="snapshot.summary.matched_concepts.length">
                <span
                  v-for="name in snapshot.summary.matched_concepts"
                  :key="`concept-${name}`"
                  class="badge"
                >
                  {{ name }}
                </span>
              </template>
              <span v-else class="flat">无</span>
            </div>

            <div v-if="snapshot.industry_detail?.length" class="topic-industries">
              <div class="topic-snap-label" style="margin-bottom: 6px">行业明细</div>
              <div
                v-for="row in snapshot.industry_detail"
                :key="row.industry"
                class="topic-industry"
              >
                <div class="topic-industry-head">
                  <strong>{{ row.industry }}</strong>
                  <span class="sub-meta" style="margin: 0">{{ row.count }} 只</span>
                </div>
                <div class="topic-industry-stats">
                  <span class="topic-stat">
                    <span>股票数</span><b>{{ int(row.stats.stocks) }}</b>
                  </span>
                  <span class="topic-stat">
                    <span>5日</span>
                    <b :class="tone(row.stats.ret5)">{{ pct(row.stats.ret5) }}</b>
                  </span>
                  <span class="topic-stat">
                    <span>20日</span>
                    <b :class="tone(row.stats.ret20)">{{ pct(row.stats.ret20) }}</b>
                  </span>
                  <span class="topic-stat">
                    <span>60日</span>
                    <b :class="tone(row.stats.ret60)">{{ pct(row.stats.ret60) }}</b>
                  </span>
                  <span class="topic-stat">
                    <span>5日上涨占比</span>
                    <b>{{ ratio(row.stats.up_ratio_5d) }}</b>
                  </span>
                  <span class="topic-stat">
                    <span>PE 中位</span><b>{{ num(row.stats.pe_median, 1) }}</b>
                  </span>
                  <span class="topic-stat">
                    <span>20日主力净流入</span>
                    <b :class="tone(row.stats.mf_net_20d)">{{ yi(row.stats.mf_net_20d) }}</b>
                  </span>
                </div>

                <div class="topic-industry-lists">
                  <div class="topic-mini">
                    <div class="topic-snap-label">领涨</div>
                    <div class="topic-mini-wrap">
                      <table class="screen-table topic-mini-table">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>名称</th>
                            <th>收盘</th>
                            <th>5日</th>
                            <th>20日</th>
                            <th>成交额</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr
                            v-for="stock in row.leaders"
                            :key="stock.symbol"
                            @contextmenu.prevent="openContext(stock, $event)"
                          >
                            <td class="mono">{{ stock.symbol }}</td>
                            <td>{{ stock.name }}</td>
                            <td>{{ num(stock.close) }}</td>
                            <td :class="tone(stock.ret5)">{{ pct(stock.ret5) }}</td>
                            <td :class="tone(stock.ret20)">{{ pct(stock.ret20) }}</td>
                            <td>{{ yi(stock.amount_yi) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div v-if="row.laggards.length" class="topic-mini">
                    <div class="topic-snap-label">领跌</div>
                    <div class="topic-mini-wrap">
                      <table class="screen-table topic-mini-table">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>名称</th>
                            <th>收盘</th>
                            <th>5日</th>
                            <th>20日</th>
                            <th>成交额</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr
                            v-for="stock in row.laggards"
                            :key="stock.symbol"
                            @contextmenu.prevent="openContext(stock, $event)"
                          >
                            <td class="mono">{{ stock.symbol }}</td>
                            <td>{{ stock.name }}</td>
                            <td>{{ num(stock.close) }}</td>
                            <td :class="tone(stock.ret5)">{{ pct(stock.ret5) }}</td>
                            <td :class="tone(stock.ret20)">{{ pct(stock.ret20) }}</td>
                            <td>{{ yi(stock.amount_yi) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="topic-snap-line" style="margin-top: 8px">
              主题没有点名具体行业，快照里没有行业明细。
            </div>

            <div v-if="snapshot.data_gaps.length" class="warning-bar topic-gaps">
              <strong>数据缺口</strong>
              <ul>
                <li v-for="gap in snapshot.data_gaps" :key="gap">{{ gap }}</li>
              </ul>
            </div>
          </template>
        </div>
      </div>
    </template>

    <div v-if="reports.length" class="topic-history">
      <div class="sub-meta" style="margin: 0 0 6px">
        历史主题报告（{{ reports.length }}）——点击载入
        <span v-if="historyLoading" class="loading" style="margin-left: 8px">读取中…</span>
      </div>
      <div class="topic-report-list">
        <div class="topic-report-row head">
          <span>#</span>
          <span>主题 / 标题</span>
          <span>裁定</span>
          <span>信心</span>
          <span>生成时间</span>
        </div>
        <div
          v-for="item in reports"
          :key="item.id"
          class="topic-report-row"
          :class="{ active: result?.id === item.id, disabled: historyLoading }"
          @click="loadReport(item.id)"
        >
          <span class="mono">#{{ item.id }}</span>
          <span class="topic-report-title">
            <span>{{ item.title || item.topic }}</span>
            <span class="sub-meta" style="margin: 0">{{ item.topic }} · {{ item.scope }}</span>
          </span>
          <span>
            <span class="topic-verdict" :class="verdictClass(item.verdict)">
              {{ item.verdict }}
            </span>
          </span>
          <span class="mono">{{ item.confidence }}</span>
          <span class="sub-meta" style="margin: 0">{{ fmtTime(item.created_at) }}</span>
        </div>
      </div>
    </div>

    <div v-if="!result && !loading && !reports.length" class="loading" style="margin-top: 12px">
      输入一个主题，或点一个示例标签，让模型基于本地面板数据写一份备忘。
    </div>
  </section>
</template>
