<script setup lang="ts">
import { computed } from 'vue'
import type { Quote } from '../types'

const props = defineProps<{ quote: Quote | null }>()

const changeClass = computed(() => {
  const value = props.quote?.pct_chg
  if (value === null || value === undefined) return 'flat'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
})

function fmt(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}

function fmtPct(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}
</script>

<template>
  <section v-if="quote" class="panel stock-head">
    <div>
      <div class="stock-title">
        <h2>{{ quote.name }}</h2>
        <span class="code">{{ quote.symbol }}</span>
        <span class="badge" :class="{ etf: quote.asset_type === 'etf' }">
          {{ quote.asset_type === 'etf' ? 'ETF' : 'A股' }}
        </span>
        <span class="badge">{{ quote.exchange }}</span>
      </div>
      <div class="sub-meta">
        数据源：{{ quote.source === 'sina' ? '新浪财经' : '东方财富' }}
        <span v-if="quote.trade_date"> · {{ quote.trade_date }} {{ quote.quote_time || '' }}</span>
      </div>
    </div>
    <div class="price-block">
      <div class="price" :class="changeClass">{{ fmt(quote.price) }}</div>
      <div class="change" :class="changeClass">
        {{ fmtPct(quote.pct_chg) }}
        <span v-if="quote.change !== null">（{{ quote.change > 0 ? '+' : '' }}{{ fmt(quote.change) }}）</span>
      </div>
      <div class="sub-meta">
        开 {{ fmt(quote.open) }} · 高 {{ fmt(quote.high) }} · 低 {{ fmt(quote.low) }}
      </div>
    </div>
  </section>
</template>