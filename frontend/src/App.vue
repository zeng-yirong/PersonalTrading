<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from './api'
import { applyTheme, readTheme } from './theme'
import type {
  MarketOverview,
  Meta,
  ResearchNote,
  SearchResult,
  SectionKey,
  StockContextPayload,
  StockRef,
  ThemeMode,
  WatchlistItem,
} from './types'
import AppSidebar from './components/AppSidebar.vue'
import AgentSection from './components/AgentSection.vue'
import StrategyPanel from './components/StrategyPanel.vue'
import PaperPanel from './components/PaperPanel.vue'
import FactorPanel from './components/FactorPanel.vue'
import MarketOverviewPanel from './components/MarketOverview.vue'
import OpsPanel from './components/OpsPanel.vue'
import SectionBoundary from './components/SectionBoundary.vue'
import StockContextMenu from './components/StockContextMenu.vue'
import StockDetail from './components/StockDetail.vue'
import TopicPanel from './components/TopicPanel.vue'
import UniversePanel from './components/UniversePanel.vue'
import WatchlistPanel from './components/WatchlistPanel.vue'

const theme = ref<ThemeMode>(readTheme())
const section = ref<SectionKey>('market')
const detailSymbol = ref<string | null>(null)
const detailKind = ref<'stock' | 'index'>('stock')
const currentSymbol = ref<string | null>(null)
const currentName = ref('')

const meta = ref<Meta | null>(null)
const watchlist = ref<WatchlistItem[]>([])
const overview = ref<MarketOverview | null>(null)
const overviewLoading = ref(false)
const overviewError = ref('')
const error = ref('')

const notes = ref<ResearchNote[]>([])
const notesError = ref('')
const researchLoading = ref(false)

const watchBusy = ref(false)
const toast = ref('')

const contentScroll = ref<HTMLDivElement | null>(null)
let savedSectionScroll = 0

const menu = reactive({
  open: false,
  x: 0,
  y: 0,
  symbol: '',
  name: '',
})

let toastTimer: ReturnType<typeof setTimeout> | null = null
let notesToken = 0

function messageOf(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc)
}

function showToast(text: string) {
  toast.value = text
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
    toastTimer = null
  }, 2200)
}

applyTheme(theme.value)
watch(theme, (mode) => applyTheme(mode))

function setTheme(mode: ThemeMode) {
  theme.value = mode
}

function bareSymbol(symbol: string): string {
  const match = /^(\d{6})\.(SH|SZ|BJ)$/i.exec(symbol)
  return match ? match[1] : symbol
}

function watchlistName(symbol: string): string {
  const target = bareSymbol(symbol)
  return watchlist.value.find((item) => bareSymbol(item.symbol) === target)?.name ?? ''
}

const detailWatching = computed(() => {
  const symbol = detailSymbol.value
  if (!symbol) return false
  return watchlist.value.some((item) => bareSymbol(item.symbol) === symbol)
})

const menuWatching = computed(() => {
  if (!menu.open) return false
  return watchlist.value.some((item) => bareSymbol(item.symbol) === menu.symbol)
})

async function loadMeta() {
  try {
    meta.value = await api.meta()
  } catch {
    meta.value = null
  }
}

async function loadWatchlist() {
  try {
    watchlist.value = await api.watchlist()
  } catch (exc) {
    error.value = `自选列表加载失败：${messageOf(exc)}`
  }
}

async function loadOverview() {
  overviewLoading.value = true
  overviewError.value = ''
  try {
    overview.value = await api.marketOverview()
  } catch (exc) {
    overviewError.value = `大盘数据加载失败：${messageOf(exc)}`
  } finally {
    overviewLoading.value = false
  }
}

async function loadNotes(symbol: string) {
  const token = ++notesToken
  notesError.value = ''
  notes.value = []
  try {
    const data = await api.notes(symbol)
    if (token === notesToken) notes.value = data
  } catch (exc) {
    if (token === notesToken) notesError.value = `笔记加载失败：${messageOf(exc)}`
  }
}

watch(currentSymbol, (symbol) => {
  if (symbol) void loadNotes(symbol)
})

function openDetail(symbol: string, name = '', kind: 'stock' | 'index' = 'stock') {
  // 指数代码带交易所后缀（000300.SH），不能像个股那样去掉
  const target = kind === 'index' ? symbol : bareSymbol(symbol)
  if (!target) return
  detailKind.value = kind
  savedSectionScroll = contentScroll.value?.scrollTop ?? 0
  currentSymbol.value = target
  currentName.value = name || watchlistName(target)
  detailSymbol.value = target
  menu.open = false
  error.value = ''
  void nextTick(() => {
    contentScroll.value?.scrollTo({ top: 0 })
  })
}

function onSelectSymbol(symbol: string) {
  openDetail(symbol)
}

function onSelectRef(payload: StockRef) {
  openDetail(payload.symbol, payload.name, payload.kind ?? 'stock')
}

function closeDetail() {
  detailSymbol.value = null
  menu.open = false
  void nextTick(() => {
    if (contentScroll.value) contentScroll.value.scrollTop = savedSectionScroll
  })
}

function toggleCurrentWatch() {
  const symbol = detailSymbol.value ?? currentSymbol.value
  if (!symbol) return
  void toggleWatch(symbol, currentName.value)
}

function startResearch() {
  if (!currentSymbol.value || detailKind.value === 'index') return
  detailSymbol.value = null
  menu.open = false
  section.value = 'agent'
  if (!notes.value.length) void loadNotes(currentSymbol.value)
  void nextTick(() => {
    contentScroll.value?.scrollTo({ top: 0 })
  })
}

function onAgentSelect(payload: StockRef) {
  const target = bareSymbol(payload.symbol)
  if (!target) return
  currentSymbol.value = target
  currentName.value = payload.name || watchlistName(target)
  detailSymbol.value = null
  section.value = 'agent'
  void nextTick(() => {
    contentScroll.value?.scrollTo({ top: 0 })
  })
}

function navigate(next: SectionKey) {
  section.value = next
  detailSymbol.value = null
  menu.open = false
  void nextTick(() => {
    contentScroll.value?.scrollTo({ top: 0 })
  })
}

async function addToWatchlist(payload: StockRef) {
  if (watchBusy.value) return
  watchBusy.value = true
  const target = bareSymbol(payload.symbol)
  try {
    await api.addWatchlist(target, payload.name)
    await loadWatchlist()
    showToast(`${payload.name || watchlistName(target) || target} 已加入自选`)
  } catch (exc) {
    showToast(`加入自选失败：${messageOf(exc)}`)
  } finally {
    watchBusy.value = false
  }
}

async function removeFromWatchlist(symbol: string) {
  if (watchBusy.value) return
  watchBusy.value = true
  const target = bareSymbol(symbol)
  const name = watchlistName(target)
  try {
    await api.removeWatchlist(target)
    await loadWatchlist()
    showToast(`${name || target} 已移出自选`)
  } catch (exc) {
    showToast(`移出自选失败：${messageOf(exc)}`)
  } finally {
    watchBusy.value = false
  }
}

async function toggleWatch(symbol: string, name: string) {
  const target = bareSymbol(symbol)
  const exists = watchlist.value.some((item) => bareSymbol(item.symbol) === target)
  if (exists) await removeFromWatchlist(target)
  else await addToWatchlist({ symbol: target, name })
}

async function addFromSearch(item: SearchResult) {
  await addToWatchlist({ symbol: item.symbol, name: item.name })
  openDetail(item.symbol, item.name)
}

function openContext(payload: StockContextPayload) {
  menu.open = true
  menu.x = payload.x
  menu.y = payload.y
  menu.symbol = bareSymbol(payload.symbol)
  menu.name = payload.name
}

function menuDetail() {
  const { symbol, name } = menu
  menu.open = false
  openDetail(symbol, name)
}

function menuToggleWatch() {
  const { symbol, name } = menu
  menu.open = false
  void toggleWatch(symbol, name)
}

async function copyCode(symbol: string) {
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(symbol)
    showToast(`已复制 ${symbol}`)
  } catch {
    try {
      const area = document.createElement('textarea')
      area.value = symbol
      area.style.position = 'fixed'
      area.style.top = '-1000px'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      document.body.removeChild(area)
      showToast(`已复制 ${symbol}`)
    } catch {
      showToast('复制失败，请手动选择代码')
    }
  }
}

function menuCopy() {
  const symbol = menu.symbol
  menu.open = false
  void copyCode(symbol)
}

async function generateResearch() {
  const symbol = currentSymbol.value
  if (!symbol || researchLoading.value) return
  const token = notesToken
  researchLoading.value = true
  notesError.value = ''
  try {
    const result = await api.research(symbol)
    // 研究要跑几十秒；期间用户可能切到别的股票，结果不能再塞进当前列表。
    if (token !== notesToken || symbol !== currentSymbol.value) return
    notes.value = [result.note, ...notes.value.filter((item) => item.id !== result.note.id)]
  } catch (exc) {
    if (token === notesToken) notesError.value = messageOf(exc)
  } finally {
    if (token === notesToken) researchLoading.value = false
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (menu.open) {
    menu.open = false
    return
  }
  if (detailSymbol.value) closeDetail()
}

function onPointerDown(event: PointerEvent) {
  if (!menu.open) return
  const target = event.target as Node | null
  const el = document.querySelector('.ctx-menu')
  if (target && el?.contains(target)) return
  menu.open = false
}

function onScroll() {
  if (menu.open) menu.open = false
}

function onResize() {
  if (menu.open) menu.open = false
}

onMounted(() => {
  void loadMeta()
  void loadWatchlist()
  void loadOverview()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('pointerdown', onPointerDown)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('pointerdown', onPointerDown)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', onResize)
  if (toastTimer) clearTimeout(toastTimer)
})
</script>

<template>
  <div class="app-frame">
    <AppSidebar
      :section="section"
      :theme="theme"
      :as-of="overview?.as_of || ''"
      :watch-count="watchlist.length"
      @navigate="navigate"
      @theme="setTheme"
    />

    <div ref="contentScroll" class="content-scroll">
      <main class="content">
        <div v-if="error" class="error-bar">{{ error }}</div>

        <div v-show="!detailSymbol" class="section-host">
          <SectionBoundary :section-key="section">
            <KeepAlive :max="8">
            <MarketOverviewPanel
              v-if="section === 'market'"
              :data="overview"
              :loading="overviewLoading"
              :error="overviewError"
              @refresh="loadOverview"
              @select="onSelectRef"
              @context="openContext"
            />
            <WatchlistPanel
              v-else-if="section === 'watchlist'"
              :items="watchlist"
              :selected="currentSymbol"
              :busy="watchBusy"
              @select="onSelectSymbol"
              @add="addFromSearch"
              @remove="removeFromWatchlist"
              @context="openContext"
            />
            <UniversePanel
              v-else-if="section === 'universe'"
              @select="onSelectRef"
              @context="openContext"
            />
            <StrategyPanel
              v-else-if="section === 'strategy'"
              @select="onSelectSymbol"
              @context="openContext"
            />
            <PaperPanel
              v-else-if="section === 'paper'"
              @select="onSelectSymbol"
              @context="openContext"
            />
            <TopicPanel v-else-if="section === 'topic'" @context="openContext" />
            <FactorPanel v-else-if="section === 'factor'" />
            <AgentSection
              v-else-if="section === 'agent'"
              :symbol="currentSymbol"
              :name="currentName"
              :notes="notes"
              :loading="researchLoading"
              :error="notesError"
              @generate="generateResearch"
              @select="onAgentSelect"
            />
            <OpsPanel v-else />
            </KeepAlive>
          </SectionBoundary>
        </div>

        <StockDetail
          v-if="detailSymbol"
          :symbol="detailSymbol"
          :kind="detailKind"
          :name="currentName"
          :watching="detailWatching"
          :busy="watchBusy"
          @close="closeDetail"
          @toggle-watch="toggleCurrentWatch"
          @research="startResearch"
        />

        <footer class="footer">
          {{ meta?.disclaimer || '目标价与操作建议为模型推算，不保证准确；仅供个人研究使用。' }}
          数据来自新浪财经 / 东方财富公开接口，可能存在延迟或缺失。
          <span v-if="meta"> · 模型 {{ meta.model }} · {{ meta.llm_configured ? '已配置' : '未配置' }}</span>
        </footer>
      </main>
    </div>

    <StockContextMenu
      v-if="menu.open"
      :x="menu.x"
      :y="menu.y"
      :symbol="menu.symbol"
      :name="menu.name"
      :watching="menuWatching"
      @close="menu.open = false"
      @detail="menuDetail"
      @toggle-watch="menuToggleWatch"
      @copy="menuCopy"
    />

    <Transition name="toast">
      <div v-if="toast" class="toast" role="status">{{ toast }}</div>
    </Transition>
  </div>
</template>
