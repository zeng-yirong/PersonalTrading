<script setup lang="ts">
import { ref } from 'vue'
import type { ScreenQueryPayload, StockContextPayload } from '../types'
import BacktestPanel from './BacktestPanel.vue'

const backtest = ref<InstanceType<typeof BacktestPanel> | null>(null)

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'context', payload: StockContextPayload): void
}>()

async function runWith(query: ScreenQueryPayload) {
  await backtest.value?.runWith(query)
}

defineExpose({ runWith })
</script>

<template>
  <div class="section-stack">
    <BacktestPanel ref="backtest" @select="emit('select', $event)" @context="emit('context', $event)" />
  </div>
</template>
