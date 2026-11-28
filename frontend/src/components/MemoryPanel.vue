<script setup lang="ts">
import { onActivated, onMounted, ref } from 'vue'
import { api } from '../api'
import type { MemoryItem, MemoryStats } from '../types'

const stats = ref<MemoryStats | null>(null)
const items = ref<MemoryItem[]>([])
const query = ref('')
const asOf = ref('')
const loading = ref(false)
const rebuilding = ref(false)
const error = ref('')
const note = ref('')

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

function int(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

async function load() {
  loading.value = true
  error.value = ''
  note.value = ''
  try {
    const [nextStats, nextItems] = await Promise.all([
      api.memoryStats(),
      api.memory({ asOf: asOf.value || undefined, limit: 20 }),
    ])
    stats.value = nextStats
    items.value = nextItems
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    loading.value = false
  }
}

async function search() {
  const text = query.value.trim()
  if (!text) {
    await load()
    return
  }
  loading.value = true
  error.value = ''
  note.value = ''
  try {
    items.value = await api.memory({ q: text, asOf: asOf.value || undefined, limit: 30 })
    note.value = `「${text}」命中 ${items.value.length} 条`
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    loading.value = false
  }
}

async function rebuild() {
  if (rebuilding.value) return
  rebuilding.value = true
  error.value = ''
  try {
    const result = await api.memoryRebuild()
    note.value = `索引已重建：${result.indexed} 条`
    await load()
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    rebuilding.value = false
  }
}

onMounted(load)
onActivated(load)
</script>

<template>
  <div class="ops-memory">
    <div v-if="stats" class="ops-metrics">
      <div class="ops-stat">
        <span class="ops-stat-label">索引</span>
        <strong>{{ int(stats.indexed) }}</strong>
        <span class="ops-stat-note">推演 {{ int(stats.runs) }} · 主题 {{ int(stats.topics) }}</span>
      </div>
      <div class="ops-stat" :class="stats.judged > 0 ? 'ok' : ''">
        <span class="ops-stat-label">已判定</span>
        <strong>{{ int(stats.judged) }}</strong>
        <span class="ops-stat-note">来自 agent_reviews</span>
      </div>
      <div class="ops-stat">
        <span class="ops-stat-label">覆盖</span>
        <strong>{{ stats.oldest || '—' }}</strong>
        <span class="ops-stat-note">至 {{ stats.newest || '—' }}</span>
      </div>
      <div class="ops-stat">
        <span class="ops-stat-label">更新</span>
        <strong>{{ stats.updated_at?.slice(0, 10) || '—' }}</strong>
        <span class="ops-stat-note">重建不会丢复盘判定</span>
      </div>
    </div>

    <div class="ops-memory-tools">
      <input
        v-model="query"
        placeholder="搜索关键词 / 代码（2 字走 LIKE，3 字以上走 FTS5）"
        @keydown.enter.prevent="search"
      />
      <input v-model="asOf" type="date" title="截止日期（as_of）" />
      <button type="button" class="btn" :disabled="loading" @click="search">搜索</button>
      <button type="button" class="btn ghost" :disabled="loading" @click="load">最近</button>
      <button type="button" class="btn ghost" :disabled="rebuilding" @click="rebuild">
        {{ rebuilding ? '重建中…' : '重建索引' }}
      </button>
    </div>

    <div v-if="error" class="error-bar">{{ error }}</div>
    <div v-if="note" class="market-card-note">{{ note }}</div>

    <div v-if="items.length" class="screen-table-wrap ops-table-wrap">
      <table class="screen-table ops-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>类型</th>
            <th>标的</th>
            <th>结论</th>
            <th>事后</th>
            <th>标题</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="`${item.kind}-${item.ref_id}`">
            <td class="mono">{{ item.as_of || '—' }}</td>
            <td>{{ item.kind }}</td>
            <td class="mono">{{ item.symbol || '—' }}</td>
            <td>{{ item.decision || '—' }}</td>
            <td :class="item.outcome?.includes('错误') ? 'up' : item.outcome?.includes('正确') ? 'down' : ''">
              {{ item.outcome || '待验证' }}
            </td>
            <td>
              <span v-if="item.warning" title="最近 3 次同标的判断均被证伪">⚠ </span>
              {{ item.title }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="loading" class="loading" style="margin-top: 12px">读取记忆中…</div>
    <div v-else class="loading" style="margin-top: 12px">没有匹配的记忆。</div>
  </div>
</template>
