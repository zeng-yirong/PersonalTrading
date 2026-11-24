<script setup lang="ts">
import { computed } from 'vue'
import type { SectionKey, ThemeMode } from '../types'

interface NavItem {
  key: SectionKey
  label: string
  icon: string[]
  badge?: number
}

const props = defineProps<{
  section: SectionKey
  theme: ThemeMode
  asOf: string
  watchCount: number
}>()

const emit = defineEmits<{
  (e: 'navigate', section: SectionKey): void
  (e: 'theme', theme: ThemeMode): void
}>()

const items = computed<NavItem[]>(() => [
  {
    key: 'market',
    label: '大盘',
    icon: ['M4 19V5', 'M4 19h16', 'M7 15l4-5 3 3 5-7'],
  },
  {
    key: 'watchlist',
    label: '自选',
    icon: ['M12 3.5l2.6 5.2 5.8.8-4.2 4.1 1 5.7-5.2-2.7-5.2 2.7 1-5.7-4.2-4.1 5.8-.8z'],
    badge: props.watchCount,
  },
  {
    key: 'universe',
    label: '全市场',
    icon: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  },
  {
    key: 'agent',
    label: '推演',
    icon: [
      'M12 3l1.9 4.6 4.6 1.9-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z',
      'M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z',
    ],
  },
  {
    key: 'topic',
    label: '主题',
    icon: ['M12 3l9 5-9 5-9-5 9-5z', 'M3 13l9 5 9-5'],
  },
  {
    key: 'screen',
    label: '选股',
    icon: ['M4 5h16', 'M7 10h10', 'M10 15h4', 'M12 19v.01'],
  },
  {
    key: 'backtest',
    label: '回测',
    icon: ['M4 18l5-6 4 3 6-9', 'M15 6h4v4'],
  },
  {
    key: 'factor',
    label: '因子',
    icon: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
  },
  {
    key: 'ops',
    label: '运维',
    icon: ['M12 3l7 3v5c0 4.4-2.9 7.6-7 9-4.1-1.4-7-4.6-7-9V6z', 'M9.5 12l1.8 1.8 3.4-3.6'],
  },
])
</script>

<template>
  <aside class="nav-sidebar">
    <div class="side-brand">
      <div class="brand-mark">PT</div>
      <div class="brand-text">
        <strong>PersonalTrading</strong>
        <span>本地 A 股研究助手</span>
      </div>
    </div>

    <nav class="nav-list" aria-label="主导航">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        class="nav-item"
        :class="{ active: section === item.key }"
        :aria-current="section === item.key ? 'page' : undefined"
        @click="emit('navigate', item.key)"
      >
        <span class="nav-icon" aria-hidden="true">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path v-for="(d, index) in item.icon" :key="index" :d="d" />
          </svg>
        </span>
        <span class="nav-label">{{ item.label }}</span>
        <span v-if="item.badge" class="nav-count">{{ item.badge }}</span>
      </button>
    </nav>

    <div class="side-bottom">
      <div class="theme-switch" role="group" aria-label="主题切换">
        <button
          type="button"
          class="theme-option"
          :class="{ active: theme === 'light' }"
          :aria-pressed="theme === 'light'"
          @click="emit('theme', 'light')"
        >
          <span class="theme-glyph" aria-hidden="true">☀</span>
          <span class="theme-text">白天</span>
        </button>
        <button
          type="button"
          class="theme-option"
          :class="{ active: theme === 'dark' }"
          :aria-pressed="theme === 'dark'"
          @click="emit('theme', 'dark')"
        >
          <span class="theme-glyph" aria-hidden="true">☾</span>
          <span class="theme-text">黑夜</span>
        </button>
      </div>

      <div class="side-asof">
        <span>数据截至</span>
        <strong>{{ asOf || '—' }}</strong>
      </div>
      <div class="side-foot">仅供个人研究 · 不构成投资建议</div>
    </div>
  </aside>
</template>
