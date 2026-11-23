<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ latest: Record<string, number | null> | null }>()

function fmt(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}

const rsi = computed(() => props.latest?.rsi14 ?? null)
const rsiSignal = computed(() => {
  if (rsi.value === null) return { text: '数据不足', cls: '' }
  if (rsi.value >= 70) return { text: '超买区', cls: 'negative' }
  if (rsi.value <= 30) return { text: '超卖区', cls: 'positive' }
  return { text: '中性区', cls: '' }
})

const macdSignal = computed(() => {
  const dif = props.latest?.dif
  const dea = props.latest?.dea
  const hist = props.latest?.macd_hist
  if (dif === null || dif === undefined || dea === null || dea === undefined) {
    return { text: '数据不足', cls: '' }
  }
  if (hist !== null && hist !== undefined && hist > 0) return { text: '偏多', cls: 'positive' }
  if (hist !== null && hist !== undefined && hist < 0) return { text: '偏空', cls: 'negative' }
  return { text: '中性', cls: '' }
})

const maSignal = computed(() => {
  const close = props.latest?.close
  const ma5 = props.latest?.ma5
  const ma10 = props.latest?.ma10
  const ma20 = props.latest?.ma20
  const ma60 = props.latest?.ma60
  if ([close, ma5, ma10, ma20, ma60].some((v) => v === null || v === undefined)) {
    return { text: '数据不足', cls: '' }
  }
  if (close! > ma5! && ma5! > ma10! && ma10! > ma20! && ma20! > ma60!) {
    return { text: '多头排列', cls: 'positive' }
  }
  if (close! < ma5! && ma5! < ma10! && ma10! < ma20! && ma20! < ma60!) {
    return { text: '空头排列', cls: 'negative' }
  }
  return { text: '均线交织', cls: '' }
})

const bollSignal = computed(() => {
  const close = props.latest?.close
  const upper = props.latest?.boll_upper
  const lower = props.latest?.boll_lower
  if (close === null || close === undefined || upper === null || upper === undefined || lower === null || lower === undefined) {
    return { text: '数据不足', cls: '' }
  }
  if (close > upper) return { text: '突破上轨', cls: 'positive' }
  if (close < lower) return { text: '跌破下轨', cls: 'negative' }
  return { text: '区间内', cls: '' }
})
</script>

<template>
  <section class="panel card">
    <h3>技术指标</h3>
    <div class="kv-grid">
      <div class="kv">
        <span class="k">MA5 / MA10</span>
        <span class="v">{{ fmt(latest?.ma5) }} / {{ fmt(latest?.ma10) }}</span>
      </div>
      <div class="kv">
        <span class="k">MA20 / MA60</span>
        <span class="v">{{ fmt(latest?.ma20) }} / {{ fmt(latest?.ma60) }}</span>
      </div>
      <div class="kv">
        <span class="k">RSI14</span>
        <span class="v">
          {{ fmt(latest?.rsi14) }}
          <span class="signal" :class="rsiSignal.cls">{{ rsiSignal.text }}</span>
        </span>
      </div>
      <div class="kv">
        <span class="k">MACD 柱</span>
        <span class="v">
          {{ fmt(latest?.macd_hist, 3) }}
          <span class="signal" :class="macdSignal.cls">{{ macdSignal.text }}</span>
        </span>
      </div>
      <div class="kv">
        <span class="k">DIF / DEA</span>
        <span class="v">{{ fmt(latest?.dif, 3) }} / {{ fmt(latest?.dea, 3) }}</span>
      </div>
      <div class="kv">
        <span class="k">均线形态</span>
        <span class="v"><span class="signal" :class="maSignal.cls">{{ maSignal.text }}</span></span>
      </div>
      <div class="kv">
        <span class="k">BOLL 上 / 下</span>
        <span class="v">{{ fmt(latest?.boll_upper) }} / {{ fmt(latest?.boll_lower) }}</span>
      </div>
      <div class="kv">
        <span class="k">BOLL 位置</span>
        <span class="v"><span class="signal" :class="bollSignal.cls">{{ bollSignal.text }}</span></span>
      </div>
    </div>
  </section>
</template>