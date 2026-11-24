<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import type { ResearchNote, SearchResult, StockRef } from '../types'
import AgentPanel from './AgentPanel.vue'
import ResearchPanel from './ResearchPanel.vue'

defineProps<{
  symbol: string | null
  name: string
  notes: ResearchNote[]
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'generate'): void
  (e: 'select', payload: StockRef): void
}>()

const query = ref('')
const results = ref<SearchResult[]>([])
const searching = ref(false)
const searchError = ref('')

async function search() {
  const text = query.value.trim()
  if (!text || searching.value) return
  searching.value = true
  searchError.value = ''
  try {
    results.value = await api.search(text)
  } catch (exc) {
    searchError.value = exc instanceof Error ? exc.message : String(exc)
    results.value = []
  } finally {
    searching.value = false
  }
}

function pick(item: SearchResult) {
  emit('select', { symbol: item.symbol, name: item.name })
  results.value = []
  query.value = ''
}
</script>

<template>
  <div class="section-stack">
    <section class="panel card agent-pick">
      <div class="research-head">
        <div>
          <h3 style="margin: 0">研究哪只股票</h3>
          <div class="sub-meta" style="margin-top: 4px">
            <template v-if="symbol">当前：{{ name || symbol }}（{{ symbol }}）</template>
            <template v-else>输入代码或名称，选中后下方直接开跑</template>
          </div>
        </div>
      </div>
      <div class="agent-pick-row">
        <input
          v-model="query"
          class="agent-pick-input"
          placeholder="例如 600519 / 茅台 / 沪深300ETF"
          @keydown.enter="search"
        />
        <button class="btn" :disabled="searching || !query.trim()" @click="search">
          {{ searching ? '搜索中…' : '搜索' }}
        </button>
      </div>
      <div v-if="searchError" class="error-bar" style="margin-top: 8px">{{ searchError }}</div>
      <div v-if="results.length" class="agent-pick-list">
        <button
          v-for="item in results"
          :key="item.symbol"
          type="button"
          class="agent-pick-item"
          @click="pick(item)"
        >
          <strong>{{ item.name }}</strong>
          <span class="mono">{{ item.symbol }}</span>
          <span class="muted">{{ item.exchange }} · {{ item.market }}</span>
        </button>
      </div>
    </section>

    <AgentPanel :symbol="symbol" :name="name" />
    <ResearchPanel
      :symbol="symbol"
      :notes="notes"
      :loading="loading"
      :error="error"
      @generate="emit('generate')"
    />
  </div>
</template>
