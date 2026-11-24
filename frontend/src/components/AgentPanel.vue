<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import type { AgentEvent, AgentNodeState, AgentRunSummary, AgentUsage } from '../types'

const props = defineProps<{ symbol: string | null; name: string }>()

const running = ref(false)
const nodes = ref<AgentNodeState[]>([])
const report = ref('')
const error = ref('')
const usage = ref<AgentUsage | null>(null)
const verdict = ref<Record<string, any> | null>(null)
const consensus = ref<Record<string, any> | null>(null)
const durationMs = ref(0)
const rounds = ref(2)
const runId = ref<number | null>(null)
const history = ref<AgentRunSummary[]>([])
const stageText = ref('')
const trading = ref(false)
const tradeNote = ref('')

let source: EventSource | null = null

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

function render(content: string) {
  const html = marked.parse(content) as string
  return DOMPurify.sanitize(html)
}

function close() {
  if (source) {
    source.close()
    source = null
  }
}

function upsertNode(event: AgentEvent) {
  const key = event.node || event.label || ''
  if (!key) return
  const existing = nodes.value.find((item) => item.node === key)
  if (existing) {
    existing.status = event.type === 'node_end' ? (event.status === 'error' ? 'error' : 'ok') : 'running'
    if (event.message) existing.message = event.message
    return
  }
  nodes.value.push({
    node: key,
    label: event.label || key,
    status: event.type === 'node_end' ? (event.status === 'error' ? 'error' : 'ok') : 'running',
    message: event.message,
  })
}

function handle(event: AgentEvent) {
  switch (event.type) {
    case 'stage':
      stageText.value = event.message || ''
      break
    case 'run_start':
      stageText.value = `开始分析 ${event.name || ''}（截止 ${event.as_of || ''}）`
      break
    case 'node_start':
    case 'node_end':
      upsertNode(event)
      break
    case 'run_end':
      report.value = event.report || ''
      usage.value = event.usage || null
      verdict.value = event.verdict || null
      consensus.value = event.consensus || null
      durationMs.value = event.duration_ms || 0
      runId.value = event.run_id ?? null
      running.value = false
      stageText.value = ''
      close()
      void loadHistory()
      break
    case 'error':
      error.value = event.message || '运行失败'
      running.value = false
      stageText.value = ''
      close()
      break
  }
}

function start() {
  if (!props.symbol || running.value) return
  close()
  running.value = true
  error.value = ''
  report.value = ''
  usage.value = null
  verdict.value = null
  consensus.value = null
  nodes.value = []
  stageText.value = '读取本地面板数据…'

  source = new EventSource(api.agentStreamUrl(props.symbol, rounds.value))
  for (const type of ['stage', 'run_start', 'node_start', 'node_end', 'run_end', 'error']) {
    source.addEventListener(type, (raw) => {
      try {
        handle(JSON.parse((raw as MessageEvent).data) as AgentEvent)
      } catch (exc) {
        error.value = `事件解析失败：${messageOf(exc)}`
      }
    })
  }
  source.onerror = () => {
    if (running.value) {
      error.value = '连接中断：请确认后端在运行；若已生成报告，刷新历史记录查看'
      running.value = false
    }
    close()
  }
}

async function loadHistory() {
  if (!props.symbol) return
  try {
    const result = await api.agentRuns(props.symbol)
    history.value = result.runs
  } catch {
    history.value = []
  }
}

async function openRun(id: number) {
  try {
    const item = await api.agentRun(id)
    report.value = item.report || ''
    verdict.value = item.verdict || null
    usage.value = item.usage
    durationMs.value = item.duration_ms || 0
    runId.value = item.id
    nodes.value = []
    error.value = ''
  } catch (exc) {
    error.value = `读取历史失败：${messageOf(exc)}`
  }
}

async function tradeFromRun() {
  if (!runId.value || trading.value) return
  trading.value = true
  tradeNote.value = ''
  try {
    const result = await api.paperFromRun(runId.value)
    tradeNote.value = `已买入 ${result.shares} 股 @ ${result.price}`
  } catch (exc) {
    tradeNote.value = messageOf(exc)
  } finally {
    trading.value = false
  }
}

function fmtTime(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

watch(
  () => props.symbol,
  () => {
    close()
    running.value = false
    nodes.value = []
    report.value = ''
    error.value = ''
    usage.value = null
    verdict.value = null
    consensus.value = null
    void loadHistory()
  },
  { immediate: true },
)

onBeforeUnmount(close)
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">AI 研究团队（多智能体）</h3>
        <div class="sub-meta" style="margin-top: 4px">
          7 位分析师 → 多空辩论 → 交易员 → 风控三方审查，仅基于本地面板数据
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 10px">
        <select v-model.number="rounds" class="btn ghost" :disabled="running" style="padding: 6px">
          <option :value="1">1 轮辩论</option>
          <option :value="2">2 轮辩论</option>
          <option :value="3">3 轮辩论</option>
        </select>
        <button class="btn" :disabled="running || !symbol" @click="start">
          {{ running ? '推演中…' : '启动研究' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="nodes.length || stageText" class="agent-flow" style="margin-top: 12px">
      <div v-if="stageText" class="loading">{{ stageText }}</div>
      <div v-for="node in nodes" :key="node.node" class="agent-node">
        <span class="agent-dot" :class="node.status"></span>
        <span class="agent-label">{{ node.label }}</span>
        <span v-if="node.status === 'running'" class="sub-meta" style="margin: 0">进行中…</span>
        <span v-else-if="node.status === 'error'" class="agent-error">{{ node.message }}</span>
      </div>
    </div>

    <div v-if="verdict || consensus" class="agent-verdict">
      <div v-if="consensus">
        <strong>{{ consensus.verdict }}</strong>
        <span class="sub-meta" style="margin: 0">
          · 倾向分 {{ consensus.leaning_score }} · 置信 {{ consensus.confidence }}
        </span>
      </div>
      <div v-if="verdict" style="margin-top: 4px">
        风控裁定：<strong>{{ verdict.decision }}</strong> · 最终仓位
        <strong>{{ verdict.final_position_pct }}%</strong>
        <button
          v-if="runId && (verdict.final_position_pct ?? 0) > 0 && ['批准', '调整后批准'].includes(verdict.decision)"
          class="chip"
          style="margin-left: 10px"
          :disabled="trading"
          @click="tradeFromRun"
        >
          {{ trading ? '建仓中…' : '按此裁定建仓' }}
        </button>
        <span v-if="tradeNote" class="sub-meta" style="margin-left: 8px">{{ tradeNote }}</span>
      </div>
    </div>

    <div v-if="usage" class="sub-meta" style="margin-top: 8px">
      调用 {{ usage.calls }} 次 · {{ usage.total_tokens }} tokens ·
      {{ (durationMs / 1000).toFixed(1) }}s · 记录 #{{ runId }}
    </div>

    <div v-if="report" class="markdown agent-report" v-html="render(report)"></div>

    <div v-if="!running && !report" class="loading" style="margin-top: 12px">
      {{
        symbol
          ? '点击「启动研究」，团队会读取面板数据并行推演。'
          : '先在自选、大盘或选股里打开一只标的。'
      }}
    </div>

    <details v-if="history.length" class="agent-history">
      <summary>历史推演（{{ history.length }}）</summary>
      <div v-for="item in history" :key="item.id" class="agent-history-row" @click="openRun(item.id)">
        <span>#{{ item.id }}</span>
        <span>{{ fmtTime(item.started_at) }}</span>
        <span>{{ item.status }}</span>
        <span v-if="item.verdict">
          {{ item.verdict.decision }} · {{ item.verdict.final_position_pct }}%
        </span>
      </div>
    </details>
  </section>
</template>
