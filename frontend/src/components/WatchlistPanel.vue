<script setup lang="ts">
import { computed, onActivated, ref, watch } from 'vue'
import { api } from '../api'
import type { SearchResult, StockContextPayload, WatchlistItem, WatchlistMetric } from '../types'

const props = defineProps<{
  items: WatchlistItem[]
  selected: string | null
  busy: boolean
}>()

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'add', item: SearchResult): void
  (e: 'remove', symbol: string): void
  (e: 'context', payload: StockContextPayload): void
}>()

const query = ref('')
const results = ref<SearchResult[]>([])
const searching = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
let searchToken = 0

const metrics = ref<Record<string, WatchlistMetric>>({})
const metricsLoading = ref(false)
let metricsToken = 0

const rows = computed(() =>
  props.items.map((item) => ({ item, metric: metrics.value[item.symbol] ?? null })),
)

async function loadMetrics() {
  if (!props.items.length) {
    metrics.value = {}
    return
  }
  const token = ++metricsToken
  metricsLoading.value = true
  try {
    const list = await api.watchlistMetrics()
    if (token !== metricsToken) return
    metrics.value = Object.fromEntries(list.map((row) => [row.symbol, row]))
  } catch {
    /* 行情失败不挡列表，后端已用面板收盘价兜底 */
  } finally {
    if (token === metricsToken) metricsLoading.value = false
  }
}

watch(
  () => props.items.map((item) => item.symbol).join(','),
  () => void loadMetrics(),
  { immediate: true },
)

onActivated(() => {
  if (props.items.length) void loadMetrics()
})

function num(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(2)
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function onInput() {
  if (timer) clearTimeout(timer)
  const value = query.value.trim()
  if (!value) {
    results.value = []
    return
  }
  timer = setTimeout(() => runSearch(value), 280)
}

async function runSearch(value: string) {
  const current = ++searchToken
  searching.value = true
  try {
    const found = await api.search(value)
    if (current !== searchToken) return
    results.value = found
  } catch {
    if (current === searchToken) results.value = []
  } finally {
    if (current === searchToken) searching.value = false
  }
}

function choose(item: SearchResult) {
  emit('add', item)
  query.value = ''
  results.value = []
}

function onEnter() {
  if (results.value.length === 1) {
    choose(results.value[0])
  } else if (results.value.length === 0 && query.value.trim()) {
    runSearch(query.value.trim())
  }
}

function remove(symbol: string, event: MouseEvent) {
  event.stopPropagation()
  emit('remove', symbol)
}

function openContext(item: WatchlistItem, event: MouseEvent) {
  emit('context', {
    symbol: item.symbol,
    name: item.name,
    x: event.clientX,
    y: event.clientY,
  })
}
</script>

<template>
  <aside class="panel card watch-panel">
    <div class="sidebar-head">
      <h2>
        自选股<span v-if="items.length" class="sub-meta" style="margin: 0 0 0 6px">{{ items.length }}</span>
        <span v-if="metricsLoading" class="sub-meta" style="margin: 0 0 0 8px; font-weight: 400">行情…</span>
      </h2>
      <div class="search-box">
        <input
          v-model="query"
          placeholder="代码 / 名称 / 拼音"
          @input="onInput"
          @keydown.enter.prevent="onEnter"
        />
        <div v-if="results.length || searching" class="search-results">
          <div v-if="searching" class="search-result">
            <span class="sub">搜索中…</span>
          </div>
          <div
            v-for="item in results"
            :key="item.symbol"
            class="search-result"
            @click="choose(item)"
          >
            <div>
              <div class="name">{{ item.name }}</div>
              <div class="sub">{{ item.symbol }} · {{ item.market }}</div>
            </div>
            <span class="badge" :class="{ etf: item.asset_type === 'etf' }">
              {{ item.asset_type === 'etf' ? 'ETF' : 'A股' }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="watchlist">
      <div v-if="!items.length" class="empty" style="min-height: 180px">
        <div>还没有自选股</div>
        <div style="font-size: 12px">搜索代码或名称添加</div>
      </div>
      <template v-else>
        <div class="watch-table-head">
          <span>名称 / 代码</span>
          <span class="num">现价</span>
          <span class="num">今日</span>
          <span class="num">近5日</span>
          <span class="num">自添加</span>
          <span></span>
        </div>
        <div
          v-for="row in rows"
          :key="row.item.symbol"
          class="watch-item watch-row"
          :class="{ active: row.item.symbol === selected }"
          @click="emit('select', row.item.symbol)"
          @contextmenu.prevent="openContext(row.item, $event)"
        >
          <div class="info">
            <div class="name">{{ row.item.name }}</div>
            <div class="code">
              {{ row.item.symbol }}
              <span class="badge" :class="{ etf: row.item.asset_type === 'etf' }">
                {{ row.item.asset_type === 'etf' ? 'ETF' : 'A股' }}
              </span>
            </div>
          </div>
          <div class="num price" :class="tone(row.metric?.pct_chg)">
            {{ num(row.metric?.price) }}
          </div>
          <div class="num" :class="tone(row.metric?.pct_chg)">{{ pct(row.metric?.pct_chg) }}</div>
          <div class="num" :class="tone(row.metric?.pct_5d)">{{ pct(row.metric?.pct_5d) }}</div>
          <div
            class="num since-add"
            :class="tone(row.metric?.pct_since_add)"
            :title="row.metric?.added_trade_date ? `基准日 ${row.metric.added_trade_date}` : '以加入自选时的价格为基准'"
          >
            {{ pct(row.metric?.pct_since_add) }}
          </div>
          <button class="icon-btn" title="移除" @click="remove(row.item.symbol, $event)">×</button>
        </div>
      </template>
    </div>
  </aside>
</template>
