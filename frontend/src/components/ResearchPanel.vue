<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { ResearchNote } from '../types'

defineProps<{
  symbol: string | null
  notes: ResearchNote[]
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ (e: 'generate'): void }>()

function render(content: string) {
  const html = marked.parse(content) as string
  return DOMPurify.sanitize(html)
}

function fmtTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">AI 研究笔记</h3>
        <div class="sub-meta" style="margin-top: 4px">
          基于当前行情、技术指标与基本面快照生成，仅供个人研究参考。
        </div>
      </div>
      <button class="btn" :disabled="loading || !symbol" @click="emit('generate')">
        {{ loading ? '生成中…' : '生成研究笔记' }}
      </button>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="!notes.length && !loading" class="loading" style="margin-top: 12px">
      还没有笔记。点击上方按钮，让模型基于当前数据写一份。
    </div>

    <div v-for="(note, index) in notes" :key="note.id" class="note">
      <div class="note-meta">
        <span>{{ fmtTime(note.created_at) }}</span>
        <span>{{ note.model || '模型' }}</span>
      </div>
      <div v-if="index === 0" class="markdown" v-html="render(note.content)"></div>
      <details v-else>
        <summary>查看历史笔记</summary>
        <div class="markdown" style="margin-top: 10px" v-html="render(note.content)"></div>
      </details>
    </div>
  </section>
</template>