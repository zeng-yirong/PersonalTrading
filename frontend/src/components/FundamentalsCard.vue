<script setup lang="ts">
import type { Fundamentals } from '../types'

defineProps<{ data: Fundamentals | null }>()

function fmt(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}

function money(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`
  return value.toFixed(2)
}

function pct(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(2)}%`
}
</script>

<template>
  <section class="panel card">
    <h3>基本面快照</h3>
    <div v-if="!data" class="loading">加载中…</div>
    <template v-else>
      <div v-if="data.asset_type === 'etf'" class="sub-meta" style="margin: 0 0 10px">
        ETF 基金没有企业财务报表，这里只展示行情与份额相关数据。
      </div>
      <div class="kv-grid">
        <div class="kv">
          <span class="k">报告期</span>
          <span class="v">{{ data.report_date || '—' }}</span>
        </div>
        <div class="kv">
          <span class="k">数据源</span>
          <span class="v">{{ data.source === 'eastmoney' ? '东方财富' : '新浪财经' }}</span>
        </div>
        <div class="kv">
          <span class="k">营业收入</span>
          <span class="v">{{ money(data.revenue) }}</span>
        </div>
        <div class="kv">
          <span class="k">归母净利润</span>
          <span class="v">{{ money(data.net_profit) }}</span>
        </div>
        <div class="kv">
          <span class="k">ROE</span>
          <span class="v">{{ pct(data.roe) }}</span>
        </div>
        <div class="kv">
          <span class="k">毛利率</span>
          <span class="v">{{ pct(data.gross_margin) }}</span>
        </div>
        <div class="kv">
          <span class="k">资产负债率</span>
          <span class="v">{{ pct(data.debt_ratio) }}</span>
        </div>
        <div class="kv">
          <span class="k">EPS / BPS</span>
          <span class="v">{{ fmt(data.eps) }} / {{ fmt(data.bps) }}</span>
        </div>
        <div class="kv">
          <span class="k">PE / PB</span>
          <span class="v">{{ fmt(data.pe) }} / {{ fmt(data.pb) }}</span>
        </div>
        <div class="kv">
          <span class="k">总市值</span>
          <span class="v">{{ money(data.total_mv) }}</span>
        </div>
      </div>
    </template>
  </section>
</template>