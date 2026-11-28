<script setup lang="ts">
import { onActivated, onMounted, ref } from 'vue'
import { api } from '../api'
import type { ExperienceDetail, ExperienceItem, ExperienceStats } from '../types'

const stats = ref<ExperienceStats | null>(null)
const items = ref<ExperienceItem[]>([])
const statusFilter = ref('')
const detail = ref<ExperienceDetail | null>(null)
const busy = ref('')
const error = ref('')
const note = ref('')

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

function int(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

const STATUS_LABELS: Record<string, string> = {
  candidate: '候选',
  active: '生效',
  retired: '退役',
}

function statusText(value: string): string {
  return STATUS_LABELS[value] ?? value
}

function scopeText(item: ExperienceItem): string {
  return item.scope_key ? `${item.scope}/${item.scope_key}` : item.scope
}

function triggerText(item: ExperienceItem): string {
  return Object.entries(item.trigger ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join(' · ')
}

async function load() {
  error.value = ''
  try {
    const [nextStats, nextItems] = await Promise.all([
      api.experienceStats(),
      api.experiences({ status: statusFilter.value, limit: 100 }),
    ])
    stats.value = nextStats
    items.value = nextItems
  } catch (exc) {
    error.value = messageOf(exc)
  }
}

async function extract() {
  if (busy.value) return
  busy.value = 'extract'
  error.value = ''
  note.value = ''
  try {
    const result = await api.experienceExtract()
    note.value = `抽取完成：新建 ${result.created} · 合并 ${result.merged} · 跳过 ${result.skipped}`
    await load()
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    busy.value = ''
  }
}

async function openDetail(item: ExperienceItem) {
  busy.value = `detail-${item.id}`
  error.value = ''
  try {
    detail.value = await api.experienceDetail(item.id)
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    busy.value = ''
  }
}

async function change(item: ExperienceItem, status: string, action: string) {
  const reason = window.prompt(`${action}「${item.lesson}」的理由（必填）`, '')
  if (reason === null) return
  if (!reason.trim()) {
    error.value = '状态变更必须写理由'
    return
  }
  busy.value = `status-${item.id}`
  error.value = ''
  try {
    await api.experienceStatus(item.id, status, reason.trim())
    note.value = `#${item.id} 已${action}`
    await load()
    if (detail.value?.id === item.id) {
      detail.value = await api.experienceDetail(item.id)
    }
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
onActivated(load)
</script>

<template>
  <div class="exp-panel">
    <div v-if="stats" class="ops-metrics">
      <div class="ops-stat">
        <span class="ops-stat-label">经验卡</span>
        <strong>{{ int(stats.total) }}</strong>
        <span class="ops-stat-note">
          候选 {{ int(stats.by_status.candidate) }} · 生效 {{ int(stats.by_status.active) }} ·
          退役 {{ int(stats.by_status.retired) }}
        </span>
      </div>
      <div class="ops-stat">
        <span class="ops-stat-label">影子召回</span>
        <strong>{{ int(stats.shadow_recalls) }}</strong>
        <span class="ops-stat-note">只记录，未注入</span>
      </div>
      <div class="ops-stat">
        <span class="ops-stat-label">已注入</span>
        <strong>{{ int(stats.injected_recalls) }}</strong>
        <span class="ops-stat-note">P8-2 才打开</span>
      </div>
      <div class="ops-stat">
        <span class="ops-stat-label">更新</span>
        <strong>{{ stats.updated_at?.slice(0, 10) || '—' }}</strong>
        <span class="ops-stat-note">证据来自已复盘记录</span>
      </div>
    </div>

    <div class="ops-memory-tools">
      <select v-model="statusFilter" @change="load">
        <option value="">全部状态</option>
        <option value="candidate">候选</option>
        <option value="active">生效</option>
        <option value="retired">退役</option>
      </select>
      <button type="button" class="btn ghost" :disabled="busy !== ''" @click="load">刷新</button>
      <button type="button" class="btn" :disabled="busy !== ''" @click="extract">
        {{ busy === 'extract' ? '抽取中…' : '从复盘抽取' }}
      </button>
    </div>

    <div v-if="error" class="error-bar">{{ error }}</div>
    <div v-if="note" class="market-card-note">{{ note }}</div>

    <div v-if="items.length" class="screen-table-wrap ops-table-wrap">
      <table class="screen-table ops-table">
        <thead>
          <tr>
            <th>状态</th>
            <th>范围</th>
            <th>经验</th>
            <th>触发</th>
            <th>证据</th>
            <th>置信</th>
            <th>学习日</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td>{{ statusText(item.status) }}</td>
            <td class="mono">{{ scopeText(item) }}</td>
            <td>{{ item.lesson }}</td>
            <td class="mono">{{ triggerText(item) }}</td>
            <td class="mono">{{ item.evidence_count }}</td>
            <td class="mono">{{ item.confidence }}</td>
            <td class="mono">{{ item.as_of || '—' }}</td>
            <td class="exp-actions">
              <button type="button" class="btn ghost" :disabled="busy !== ''" @click="openDetail(item)">
                详情
              </button>
              <button
                v-if="item.status === 'candidate' && item.can_activate"
                type="button"
                class="btn"
                :disabled="busy !== ''"
                @click="change(item, 'active', '确认生效')"
              >
                生效
              </button>
              <button
                v-if="item.status === 'candidate' || item.status === 'active'"
                type="button"
                class="btn ghost"
                :disabled="busy !== ''"
                @click="change(item, 'retired', '退役')"
              >
                退役
              </button>
              <button
                v-if="item.status === 'retired'"
                type="button"
                class="btn ghost"
                :disabled="busy !== ''"
                @click="change(item, 'candidate', '恢复候选')"
              >
                恢复
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="loading" style="margin-top: 12px">
      没有经验卡。等 T+20 复盘后点「从复盘抽取」，或先在测试库造样本。
    </div>

    <div v-if="detail" class="exp-detail">
      <div class="exp-detail-head">
        <strong>#{{ detail.id }} · {{ detail.lesson }}</strong>
        <button type="button" class="icon-btn" title="收起" @click="detail = null">×</button>
      </div>
      <div class="exp-detail-meta">
        <span>{{ scopeText(detail) }}</span>
        <span>状态 {{ statusText(detail.status) }}</span>
        <span>置信 {{ detail.confidence }}</span>
        <span>来源 {{ detail.source }}</span>
      </div>
      <div v-if="detail.status_reason" class="market-card-note">
        状态理由：{{ detail.status_reason }}
      </div>
      <div class="exp-detail-block">
        <div class="ops-block-title">触发条件</div>
        <code>{{ JSON.stringify(detail.trigger) }}</code>
      </div>
      <div class="exp-detail-block">
        <div class="ops-block-title">建议动作</div>
        <code>{{ JSON.stringify(detail.action) }}</code>
      </div>
      <div class="exp-detail-block">
        <div class="ops-block-title">证据（{{ detail.evidence.length }}）</div>
        <table class="screen-table ops-table">
          <thead>
            <tr>
              <th>来源</th>
              <th>标的</th>
              <th>日期</th>
              <th>T+20</th>
              <th>超额</th>
              <th>判定</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in detail.evidence" :key="`${row.kind}-${row.ref_id}-${index}`">
              <td class="mono">{{ row.kind }}#{{ row.ref_id }}</td>
              <td class="mono">{{ row.symbol || '—' }}</td>
              <td class="mono">{{ row.date || '—' }}</td>
              <td class="mono">{{ row.metric == null ? '—' : `${row.metric.toFixed(2)}%` }}</td>
              <td class="mono">{{ row.excess == null ? '—' : `${row.excess.toFixed(2)}%` }}</td>
              <td>{{ row.outcome || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="exp-detail-block">
        <div class="ops-block-title">影子召回（{{ detail.recalls.length }}）</div>
        <div v-if="detail.recalls.length" class="exp-detail-meta">
          <span v-for="recall in detail.recalls.slice(0, 10)" :key="`${recall.run_id}-${recall.created_at}`">
            run #{{ recall.run_id }} · score {{ recall.score.toFixed(3) }} · injected {{ recall.injected }}
          </span>
        </div>
        <div v-else class="sub">还没有召回记录。</div>
      </div>
    </div>
  </div>
</template>
