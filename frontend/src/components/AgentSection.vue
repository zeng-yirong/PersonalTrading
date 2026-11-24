<script setup lang="ts">
import type { ResearchNote } from '../types'
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
}>()
</script>

<template>
  <div class="section-stack">
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
