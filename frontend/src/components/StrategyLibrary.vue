<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { StrategyRow, StrategyStatus } from '../types'

/**
 * 策略库：策略是实体，有状态、有成绩、有留档。
 *
 * 状态机刻意做窄（后端校验）：candidate → watch → champion 是可升级路径，
 * 反向可降级，任何状态可淘汰（要写理由），淘汰后可恢复为观察。
 * 每周排名只决定表格里的顺序，不自动改状态——改状态必须点按钮并留理由。
 */
const rows = ref<StrategyRow[]>([])
const loading = ref(false)
const error = ref('')
const note = ref('')
const busy = ref('')
const group = ref<'all' | 'short' | 'mid' | 'long'>('all')

const HORIZON_TEXT: Record<string, string> = { short: '短线', mid: '中线', long: '长线' }
const HORIZON_TABS: Array<{ key: 'all' | 'short' | 'mid' | 'long'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'short', label: '短线' },
  { key: 'mid', label: '中线' },
  { key: 'long', label: '长线' },
]

const visible = computed(() =>
  group.value === 'all' ? rows.value : rows.value.filter((row) => (row.horizon || 'long') === group.value),
)

const STATUS_TEXT: Record<StrategyStatus, string> = {
  template: '模板',
  champion: '正式',
  watch: '观察',
  candidate: '候选',
  retired: '淘汰',
  paused: '暂停',
}

const counts = computed(() => {
  const map: Record<string, number> = {}
  for (const row of rows.value) map[row.status] = (map[row.status] ?? 0) + 1
  return map
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.strategies()
    rows.value = data.strategies
  } catch (exc) {
    error.value = `策略库加载失败：${exc instanceof Error ? exc.message : String(exc)}`
  } finally {
    loading.value = false
  }
}

function canUpgrade(row: StrategyRow): boolean {
  return (
    row.status === 'template' ||
    row.status === 'candidate' ||
    row.status === 'watch' ||
    row.status === 'retired'
  )
}

function canDowngrade(row: StrategyRow): boolean {
  return row.status === 'champion' || row.status === 'watch'
}

function canRetire(row: StrategyRow): boolean {
  return row.status !== 'retired'
}

async function move(row: StrategyRow, target: StrategyStatus, askReason = false) {
  let reason = ''
  if (askReason || target === 'retired') {
    reason = window.prompt(`把「${row.name}」改为 ${STATUS_TEXT[target]}，理由（会留档）：`, '') ?? ''
    if (reason === null) return
  }
  busy.value = row.id
  note.value = ''
  try {
    await api.strategyStatus(row.id, target, reason)
    note.value = `${row.name} → ${STATUS_TEXT[target]}`
    await load()
  } catch (exc) {
    note.value = `操作失败：${exc instanceof Error ? exc.message : String(exc)}`
  } finally {
    busy.value = ''
  }
}

async function upgrade(row: StrategyRow) {
  const target: StrategyStatus =
    row.status === 'template' ? 'candidate' : row.status === 'candidate' ? 'watch' : 'champion'
  await move(row, target, true)
}

async function downgrade(row: StrategyRow) {
  const target: StrategyStatus = row.status === 'champion' ? 'watch' : 'candidate'
  await move(row, target, true)
}

async function clone(row: StrategyRow) {
  busy.value = row.id
  note.value = ''
  try {
    const copy = await api.strategyClone(row.id)
    note.value = `已克隆为 ${copy.id}`
    await load()
  } catch (exc) {
    note.value = `克隆失败：${exc instanceof Error ? exc.message : String(exc)}`
  } finally {
    busy.value = ''
  }
}

async function rerun(row: StrategyRow) {
  busy.value = row.id
  note.value = `正在重跑「${row.name}」（近一年）…`
  try {
    const result = await api.strategyRun(row.id)
    note.value = `已重跑：超额年化 ${fmt(result.metrics.excess_cagr_pct)}`
    await load()
  } catch (exc) {
    note.value = `重跑失败：${exc instanceof Error ? exc.message : String(exc)}`
  } finally {
    busy.value = ''
  }
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function trendText(items: number[] | undefined): string {
  if (!items?.length) return '—'
  return items.map((item) => fmt(item, 1)).join(' → ')
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'muted'
  return value >= 0 ? 'up' : 'down'
}

onMounted(load)
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">策略库</h3>
        <div class="sub-meta" style="margin-top: 4px">
          正式 {{ counts.champion ?? 0 }} · 观察 {{ counts.watch ?? 0 }} ·
          候选 {{ counts.candidate ?? 0 }} · 淘汰 {{ counts.retired ?? 0 }}
          <span v-if="loading">｜加载中…</span>
        </div>
      </div>
      <div style="display: flex; gap: 8px; align-items: center">
        <span v-if="note" class="sub-meta">{{ note }}</span>
        <button type="button" class="btn ghost" :disabled="loading" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div class="strategy-tabs" style="margin-top: 10px">
      <button
        v-for="item in HORIZON_TABS"
        :key="item.key"
        type="button"
        class="strategy-tab"
        :class="{ active: group === item.key }"
        @click="group = item.key"
      >
        {{ item.label }}
      </button>
      <span class="sub-meta" style="margin-left: 8px">
        短线的参数是 3 日调仓 / 5 只，长线是 20 日 / 10 只——周期不同，门槛也不同
      </span>
    </div>

    <div class="screen-table-wrap" style="margin-top: 12px">
      <table class="screen-table">
        <thead>
          <tr>
            <th>策略</th>
            <th>状态</th>
            <th>近一年</th>
            <th>近二年</th>
            <th>最近 4 周趋势</th>
            <th>最后复核</th>
            <th style="min-width: 230px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visible" :key="row.id">
            <td>
              <div>{{ row.name }}</div>
              <div class="sub-meta mono" style="font-size: 11px">
                {{ row.id }}<span v-if="row.retire_reason">｜{{ row.retire_reason }}</span>
              </div>
            </td>
            <td>
              <span class="strategy-status" :class="`status-${row.status}`">
                {{ STATUS_TEXT[row.status] }}
              </span>
              <div>
                <span class="strategy-horizon">{{ HORIZON_TEXT[row.horizon || 'long'] }}</span>
              </div>
            </td>
            <td class="mono" :class="tone(row.year1?.excess_cagr_pct)">
              {{ fmt(row.year1?.excess_cagr_pct) }}
              <div class="sub-meta" style="font-size: 11px">
                收益 {{ fmt(row.year1?.total_return_pct) }}
              </div>
            </td>
            <td class="mono" :class="tone(row.year2?.excess_cagr_pct)">
              {{ fmt(row.year2?.excess_cagr_pct) }}
              <div class="sub-meta" style="font-size: 11px">
                回撤 {{ fmt(row.year2?.max_drawdown_pct) }}
              </div>
            </td>
            <td class="mono" style="font-size: 11px">{{ trendText(row.trend) }}</td>
            <td class="mono" style="font-size: 11px">{{ (row.last_checked || '').slice(0, 16) || '—' }}</td>
            <td>
              <button
                type="button"
                class="chip"
                :disabled="busy === row.id || !canUpgrade(row)"
                @click="upgrade(row)"
              >
                升级
              </button>
              <button
                type="button"
                class="chip"
                :disabled="busy === row.id || !canDowngrade(row)"
                @click="downgrade(row)"
              >
                降级
              </button>
              <button
                type="button"
                class="chip"
                :disabled="busy === row.id || !canRetire(row)"
                @click="move(row, 'retired')"
              >
                淘汰
              </button>
              <button type="button" class="chip" :disabled="busy === row.id" @click="clone(row)">
                克隆
              </button>
              <button type="button" class="chip" :disabled="busy === row.id" @click="rerun(row)">
                重跑
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="7" class="loading">策略库为空</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="sub-meta" style="margin-top: 8px">
      近一年 / 近二年都以沪深300 为基准比超额；最近 4 周趋势是每周全量回测的超额年化（旧 → 新）。
      排名不自动改状态：升级要观察期、淘汰要留理由。
    </div>
  </section>
</template>
