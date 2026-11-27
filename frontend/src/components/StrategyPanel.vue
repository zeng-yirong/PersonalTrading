<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { ScreenQueryPayload, StockContextPayload } from '../types'
import BacktestSection from './BacktestSection.vue'
import ScreenPanel from './ScreenPanel.vue'

/**
 * 「策略」模块 = 选股 + 回测，共用同一份条件。
 *
 * 两块面板本来就是同一个 DSL（ScreenQuery）：筛什么就回测什么。之前它们是两个
 * 导航项，靠 `@backtest` 事件跳模块，条件在跳转时复制一次；现在只是把跳转换成
 * 页内切 tab，条件不再离开这个模块。
 *
 * 两块都用 v-show 而不是 v-if：切回来时筛选结果和回测参数都还在。
 */
const tab = ref<'screen' | 'backtest'>('screen')
const backtest = ref<InstanceType<typeof BacktestSection> | null>(null)
const json = ref('')
const jsonNote = ref('')

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'context', payload: StockContextPayload): void
}>()

async function onBacktest(query: ScreenQueryPayload) {
  tab.value = 'backtest'
  await nextTick()
  await backtest.value?.runWith(query)
}

/** 不经过选股也能跑：直接粘一份条件 JSON（老「回测」模块的独立入口）。 */
async function runFromJson() {
  jsonNote.value = ''
  const text = json.value.trim()
  if (!text) return
  let parsed: ScreenQueryPayload
  try {
    parsed = JSON.parse(text) as ScreenQueryPayload
  } catch (exc) {
    jsonNote.value = `JSON 解析失败：${exc instanceof Error ? exc.message : String(exc)}`
    return
  }
  if (!Array.isArray(parsed?.filters) || !parsed.filters.length) {
    jsonNote.value = '条件里至少要有一个 filter'
    return
  }
  tab.value = 'backtest'
  await nextTick()
  await backtest.value?.runWith(parsed)
  jsonNote.value = '已按粘贴的条件回测'
}

async function switchTab(next: 'screen' | 'backtest') {
  tab.value = next
  await nextTick()
}
</script>

<template>
  <div class="section-stack">
    <div class="strategy-tabs">
      <button
        type="button"
        class="strategy-tab"
        :class="{ active: tab === 'screen' }"
        @click="switchTab('screen')"
      >
        选股（当前截面）
      </button>
      <button
        type="button"
        class="strategy-tab"
        :class="{ active: tab === 'backtest' }"
        @click="switchTab('backtest')"
      >
        回测（历史验证）
      </button>
      <span class="sub-meta" style="margin-left: 8px">
        同一份条件：上面筛出来的东西，下面原样跑一遍历史
      </span>
    </div>

    <div v-show="tab === 'screen'">
      <ScreenPanel
        @select="emit('select', $event)"
        @backtest="onBacktest"
        @context="emit('context', $event)"
      />
    </div>

    <div v-show="tab === 'backtest'">
      <details class="strategy-json">
        <summary>直接粘贴条件 JSON（不经过选股也能回测）</summary>
        <textarea
          v-model="json"
          rows="5"
          spellcheck="false"
          placeholder='{"filters":[{"field":"pe_ttm","op":"<=","value":20}],"limit":30}'
        />
        <div class="strategy-json-foot">
          <button type="button" class="btn ghost" :disabled="!json.trim()" @click="runFromJson">
            用这段 JSON 回测
          </button>
          <span v-if="jsonNote" class="sub-meta">{{ jsonNote }}</span>
        </div>
      </details>

      <BacktestSection
        ref="backtest"
        @select="emit('select', $event)"
        @context="emit('context', $event)"
      />
    </div>
  </div>
</template>
