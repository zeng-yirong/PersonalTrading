<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import type { SearchResult, WatchlistItem } from '../types'

defineProps<{
  items: WatchlistItem[]
  selected: string | null
  busy: boolean
}>()

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'add', item: SearchResult): void
  (e: 'remove', symbol: string): void
}>()

const query = ref('')
const results = ref<SearchResult[]>([])
const searching = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

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
  searching.value = true
  try {
    results.value = await api.search(value)
  } catch {
    results.value = []
  } finally {
    searching.value = false
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
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <h2>自选股</h2>
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
      <div
        v-for="item in items"
        :key="item.symbol"
        class="watch-item"
        :class="{ active: item.symbol === selected }"
        @click="emit('select', item.symbol)"
      >
        <div class="info">
          <div class="name">{{ item.name }}</div>
          <div class="code">{{ item.symbol }}</div>
        </div>
        <span class="badge" :class="{ etf: item.asset_type === 'etf' }">
          {{ item.asset_type === 'etf' ? 'ETF' : 'A股' }}
        </span>
        <button class="icon-btn" title="移除" @click="remove(item.symbol, $event)">×</button>
      </div>
    </div>
  </aside>
</template>