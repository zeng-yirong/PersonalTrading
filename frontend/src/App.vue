<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from './api'
import type {
  ChartData,
  Fundamentals,
  Meta,
  Quote,
  ResearchNote,
  ScreenQueryPayload,
  SearchResult,
  WatchlistItem,
} from './types'
import WatchlistPanel from './components/WatchlistPanel.vue'
import StockHeader from './components/StockHeader.vue'
import PriceChart from './components/PriceChart.vue'
import IndicatorPanel from './components/IndicatorPanel.vue'
import FundamentalsCard from './components/FundamentalsCard.vue'
import ResearchPanel from './components/ResearchPanel.vue'
import AgentPanel from './components/AgentPanel.vue'
import ScreenPanel from './components/ScreenPanel.vue'
import BacktestPanel from './components/BacktestPanel.vue'
import TopicPanel from './components/TopicPanel.vue'
import FactorPanel from './components/FactorPanel.vue'
import PaperPanel from './components/PaperPanel.vue'
import OpsPanel from './components/OpsPanel.vue'

const meta = ref<Meta | null>(null)
const watchlist = ref<WatchlistItem[]>([])
const selected = ref<string | null>(null)
const quote = ref<Quote | null>(null)
const chart = ref<ChartData | null>(null)
const fundamentals = ref<Fundamentals | null>(null)
const notes = ref<ResearchNote[]>([])
const period = ref<'day' | 'week' | 'month'>('day')
const error = ref('')
const researchError = ref('')
const backtestRef = ref<InstanceType<typeof BacktestPanel> | null>(null)

const loading = reactive({
  watchlist: false,
  quote: false,
  chart: false,
  fundamentals: false,
  notes: false,
  research: false,
})

const selectedName = computed(
  () => quote.value?.name || watchlist.value.find((item) => item.symbol === selected.value)?.name || '',
)

function messageOf(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

async function loadWatchlist() {
  loading.watchlist = true
  try {
    watchlist.value = await api.watchlist()
  } catch (exc) {
    error.value = messageOf(exc)
  } finally {
    loading.watchlist = false
  }
}

async function loadQuote(symbol: string) {
  loading.quote = true
  try {
    quote.value = await api.quote(symbol)
  } catch (exc) {
    error.value = `行情加载失败：${messageOf(exc)}`
  } finally {
    loading.quote = false
  }
}

async function loadChart(symbol: string) {
  loading.chart = true
  try {
    chart.value = await api.chart(symbol, period.value, 250)
  } catch (exc) {
    error.value = `K线加载失败：${messageOf(exc)}`
  } finally {
    loading.chart = false
  }
}

async function loadFundamentals(symbol: string) {
  loading.fundamentals = true
  try {
    fundamentals.value = await api.fundamentals(symbol)
  } catch (exc) {
    error.value = `基本面加载失败：${messageOf(exc)}`
  } finally {
    loading.fundamentals = false
  }
}

async function loadNotes(symbol: string) {
  loading.notes = true
  try {
    notes.value = await api.notes(symbol)
  } catch (exc) {
    error.value = `笔记加载失败：${messageOf(exc)}`
  } finally {
    loading.notes = false
  }
}

async function selectStock(symbol: string) {
  if (!symbol) return
  selected.value = symbol
  error.value = ''
  researchError.value = ''
  quote.value = null
  chart.value = null
  fundamentals.value = null
  notes.value = []
  await Promise.all([
    loadQuote(symbol),
    loadChart(symbol),
    loadFundamentals(symbol),
    loadNotes(symbol),
  ])
}

async function addStock(item: SearchResult) {
  try {
    await api.addWatchlist(item.symbol, item.name)
    await loadWatchlist()
    await selectStock(item.symbol)
  } catch (exc) {
    error.value = `添加失败：${messageOf(exc)}`
  }
}

async function removeStock(symbol: string) {
  try {
    await api.removeWatchlist(symbol)
    if (selected.value === symbol) {
      selected.value = null
      quote.value = null
      chart.value = null
      fundamentals.value = null
      notes.value = []
    }
    await loadWatchlist()
    if (!selected.value && watchlist.value.length) {
      await selectStock(watchlist.value[0].symbol)
    }
  } catch (exc) {
    error.value = `移除失败：${messageOf(exc)}`
  }
}

async function setPeriod(next: 'day' | 'week' | 'month') {
  if (period.value === next || !selected.value) return
  period.value = next
  error.value = ''
  await loadChart(selected.value)
}

async function refresh() {
  if (!selected.value) return
  error.value = ''
  await selectStock(selected.value)
}

async function generateResearch() {
  if (!selected.value) return
  loading.research = true
  researchError.value = ''
  try {
    const result = await api.research(selected.value)
    notes.value = [result.note, ...notes.value.filter((item) => item.id !== result.note.id)]
  } catch (exc) {
    researchError.value = messageOf(exc)
  } finally {
    loading.research = false
  }
}

function onBacktest(query: ScreenQueryPayload) {
  void backtestRef.value?.runWith(query)
}

onMounted(async () => {
  try {
    meta.value = await api.meta()
  } catch {
    meta.value = null
  }
  await loadWatchlist()
  if (watchlist.value.length) {
    await selectStock(watchlist.value[0].symbol)
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <h1>PersonalTrading</h1>
        <span>本地 A 股 / ETF 研究助手</span>
      </div>
      <div class="top-meta">
        <span class="status-dot"></span>
        <span v-if="meta">{{ meta.model }}</span>
        <span v-if="meta">· {{ meta.llm_configured ? '模型已配置' : '未配置模型' }}</span>
        <span v-if="meta">· {{ meta.data_sources.join(' / ') }}</span>
      </div>
    </header>

    <div class="layout">
      <WatchlistPanel
        :items="watchlist"
        :selected="selected"
        :busy="loading.watchlist"
        @select="selectStock"
        @add="addStock"
        @remove="removeStock"
      />

      <main class="main">
        <div v-if="error" class="error-bar">{{ error }}</div>

        <ScreenPanel @select="selectStock" @backtest="onBacktest" />

        <BacktestPanel ref="backtestRef" />

        <TopicPanel />

        <FactorPanel />

        <div v-if="!selected" class="panel empty">
          <div class="big">从左侧添加一只股票或 ETF</div>
          <div>支持 A 股 6 位代码、ETF 代码，也可以输入名称或拼音搜索。</div>
        </div>

        <template v-else>
          <StockHeader :quote="quote" />

          <section class="panel toolbar">
            <div class="tabs">
              <button class="tab" :class="{ active: period === 'day' }" @click="setPeriod('day')">日线</button>
              <button class="tab" :class="{ active: period === 'week' }" @click="setPeriod('week')">周线</button>
              <button class="tab" :class="{ active: period === 'month' }" @click="setPeriod('month')">月线</button>
            </div>
            <div style="display: flex; align-items: center; gap: 12px">
              <span v-if="loading.chart" class="loading">加载中…</span>
              <span v-else class="sub-meta" style="margin: 0">{{ selectedName }} · {{ selected }}</span>
              <button class="btn ghost" :disabled="loading.chart" @click="refresh">刷新</button>
            </div>
          </section>

          <section class="panel chart-wrap">
            <PriceChart :chart="chart" />
          </section>

          <div class="cards">
            <IndicatorPanel :latest="chart?.indicators.latest ?? null" />
            <FundamentalsCard :data="fundamentals" />
          </div>

          <AgentPanel :symbol="selected" :name="selectedName" />

          <ResearchPanel
            :symbol="selected"
            :notes="notes"
            :loading="loading.research"
            :error="researchError"
            @generate="generateResearch"
          />
        </template>

        <PaperPanel @select="selectStock" />

        <OpsPanel />

        <div class="footer">
          {{ meta?.disclaimer || '目标价与操作建议为模型推算，不保证准确；仅供个人研究使用。' }}
          数据来自新浪财经 / 东方财富公开接口，可能存在延迟或缺失。
        </div>
      </main>
    </div>
  </div>
</template>