<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import type {
  StockContextPayload,
  StockRef,
  UniverseAsset,
  UniverseResponse,
  UniverseRow,
} from '../types'

const emit = defineEmits<{
  (e: 'select', payload: StockRef): void
  (e: 'context', payload: StockContextPayload): void
}>()

const TABS: Array<{ key: UniverseAsset; label: string }> = [
  { key: 'stock', label: 'A股' },
  { key: 'etf', label: 'ETF' },
  { key: 'index', label: '指数' },
  { key: 'hk', label: '港股' },
]

const asset = ref<UniverseAsset>('stock')
const query = ref('')
const sort = ref('pct_chg')
const order = ref<'desc' | 'asc'>('desc')
const page = ref(1)
const pageSize = 50
const data = ref<UniverseResponse | null>(null)
const loading = ref(false)
const error = ref('')
let timer: ReturnType<typeof setTimeout> | null = null
let token = 0

const columns = computed(() => {
  if (asset.value === 'index') {
    return [
      { key: 'code', label: '代码', sort: 'code' },
      { key: 'name', label: '名称', sort: 'name' },
      { key: 'category', label: '类别', sort: '' },
      { key: 'close', label: '最新', sort: 'close' },
      { key: 'pct_chg', label: '涨跌幅', sort: 'pct_chg' },
      { key: 'date', label: '数据日', sort: '' },
    ]
  }
  if (asset.value === 'hk') {
    return [
      { key: 'code', label: '代码', sort: '' },
      { key: 'name', label: '名称', sort: '' },
      { key: 'close', label: '最新', sort: '' },
      { key: 'pct_chg', label: '涨跌幅', sort: '' },
    ]
  }
  return [
    { key: 'symbol', label: '代码', sort: 'symbol' },
    { key: 'name', label: '名称', sort: 'name' },
    { key: 'industry', label: '行业', sort: '' },
    { key: 'close', label: '最新', sort: 'close' },
    { key: 'pct_chg', label: '涨跌幅', sort: 'pct_chg' },
    { key: 'amount', label: '成交额', sort: 'amount' },
    { key: 'total_mv', label: '总市值', sort: 'total_mv' },
    { key: 'pe_ttm', label: 'PE', sort: 'pe_ttm' },
    { key: 'turnover_rate', label: '换手', sort: 'turnover_rate' },
    { key: 'ret20', label: '20日涨幅', sort: 'ret20' },
  ]
})

const totalPages = computed(() =>
  data.value ? Math.max(1, Math.ceil(data.value.total / data.value.page_size)) : 1,
)

function messageOf(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc)
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function amountYi(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value / 1e8).toFixed(1)} 亿`
}

function mvYi(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value / 1e4).toFixed(1)} 亿`
}

async function load() {
  const current = ++token
  loading.value = true
  error.value = ''
  try {
    const payload = await api.universeList(asset.value, {
      q: query.value.trim(),
      sort: sort.value,
      order: order.value,
      page: page.value,
      page_size: pageSize,
    })
    if (current !== token) return
    data.value = payload
  } catch (exc) {
    if (current === token) {
      error.value = `加载失败：${messageOf(exc)}`
      data.value = null
    }
  } finally {
    if (current === token) loading.value = false
  }
}

function switchAsset(next: UniverseAsset) {
  if (asset.value === next) return
  asset.value = next
  page.value = 1
  query.value = ''
  sort.value = 'pct_chg'
  order.value = 'desc'
  if (next === 'index' || next === 'hk') sort.value = 'pct_chg'
  void load()
}

function onInput() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    page.value = 1
    void load()
  }, 300)
}

function sortBy(key: string) {
  if (!key) return
  if (sort.value === key) order.value = order.value === 'desc' ? 'asc' : 'desc'
  else {
    sort.value = key
    order.value = 'desc'
  }
  page.value = 1
  void load()
}

function goPage(delta: number) {
  const next = page.value + delta
  if (next < 1 || next > totalPages.value) return
  page.value = next
  void load()
}

function rowKey(row: UniverseRow): string {
  return row.symbol || row.code || row.name
}

function openRow(row: UniverseRow) {
  if (asset.value === 'index') {
    if (!row.code) return
    emit('select', { symbol: row.code, name: row.name, kind: 'index' })
    return
  }
  if (asset.value !== 'stock' && asset.value !== 'etf') return
  const symbol = row.symbol
  if (!symbol) return
  emit('select', { symbol, name: row.name })
}

function openContext(row: UniverseRow, event: MouseEvent) {
  if (asset.value !== 'stock' && asset.value !== 'etf') return
  const symbol = row.symbol
  if (!symbol) return
  emit('context', { symbol, name: row.name, x: event.clientX, y: event.clientY })
}

function cell(row: UniverseRow, key: string): string {
  const value = (row as unknown as Record<string, unknown>)[key]
  if (key === 'pct_chg' || key === 'ret20') return pct(value as number | null)
  if (key === 'amount') return amountYi(value as number | null)
  if (key === 'total_mv') return mvYi(value as number | null)
  if (key === 'pe_ttm' || key === 'turnover_rate' || key === 'close') return num(value as number | null)
  if (key === 'fd_pead') return pct(value as number | null)
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

onMounted(() => {
  void load()
})

watch(asset, () => {
  /* switchAsset 已经负责刷新 */
})
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">全市场</h3>
        <div class="sub-meta" style="margin-top: 4px">
          A 股、ETF、指数、港股（港股仅恒指）
          <span v-if="data?.as_of"> · 数据日 {{ data.as_of }}</span>
          <span v-if="data"> · 共 {{ data.total.toLocaleString('zh-CN') }} 条</span>
        </div>
      </div>
      <div class="market-tabs" style="margin: 0">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          type="button"
          class="market-tab"
          :class="{ active: asset === tab.key }"
          @click="switchAsset(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <div class="universe-search">
      <input
        v-model="query"
        class="agent-pick-input"
        :placeholder="asset === 'index' ? '按代码或名称搜索指数' : '按代码或名称搜索'"
        @input="onInput"
        @keydown.enter="page = 1; load()"
      />
      <button class="btn" :disabled="loading" @click="page = 1; load()">
        {{ loading ? '加载中…' : '搜索' }}
      </button>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>
    <div v-if="data?.note" class="warning-bar" style="margin-top: 10px">{{ data.note }}</div>

    <div class="screen-table-wrap" style="margin-top: 12px">
      <table class="screen-table universe-table">
        <thead>
          <tr>
            <th
              v-for="column in columns"
              :key="column.key"
              :class="{ sortable: column.sort }"
              @click="sortBy(column.sort)"
            >
              {{ column.label }}
              <span v-if="column.sort && sort === column.sort">{{ order === 'desc' ? ' ↓' : ' ↑' }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in data?.rows ?? []"
            :key="rowKey(row)"
            :class="{ clickable: asset !== 'hk' }"
            @click="openRow(row)"
            @contextmenu.prevent="openContext(row, $event)"
          >
            <td v-for="column in columns" :key="column.key" :class="column.key === 'pct_chg' || column.key === 'ret20' ? tone(row.pct_chg) : ''">
              <span v-if="column.key === 'pct_chg' || column.key === 'ret20'" :class="tone(column.key === 'pct_chg' ? row.pct_chg : row.ret20)">
                {{ cell(row, column.key) }}
              </span>
              <span v-else>{{ cell(row, column.key) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && !(data?.rows ?? []).length" class="loading">没有匹配的标的</div>
    </div>

    <div class="universe-foot">
      <span class="sub-meta" style="margin: 0">
        第 {{ data?.page ?? 1 }} / {{ totalPages }} 页
      </span>
      <button class="btn ghost" :disabled="loading || (data?.page ?? 1) <= 1" @click="goPage(-1)">上一页</button>
      <button class="btn ghost" :disabled="loading || (data?.page ?? 1) >= totalPages" @click="goPage(1)">下一页</button>
      <span class="sub-meta" style="margin: 0">点击行看详情 · 右键加入自选</span>
    </div>
  </section>
</template>
