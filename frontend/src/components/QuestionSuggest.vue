<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { QuestionGroup } from '../types'

interface FlatQuestion {
  group: string
  text: string
  index: number
}

interface GroupRow {
  label: string
  items: FlatQuestion[]
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    groups: QuestionGroup[]
    placeholder?: string
    rows?: number
    maxlength?: number
    disabled?: boolean
  }>(),
  {
    placeholder: '',
    rows: 2,
    disabled: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'submit'): void
  (e: 'select', value: string): void
}>()

const root = ref<HTMLDivElement | null>(null)
const textarea = ref<HTMLTextAreaElement | null>(null)
const open = ref(false)
const active = ref(0)

const flat = computed<FlatQuestion[]>(() => {
  const query = props.modelValue.trim().toLowerCase()
  const rows: FlatQuestion[] = []
  for (const group of props.groups) {
    for (const text of group.questions) {
      if (
        query &&
        !text.toLowerCase().includes(query) &&
        !group.label.toLowerCase().includes(query)
      ) {
        continue
      }
      rows.push({ group: group.label, text, index: rows.length })
    }
  }
  return rows.slice(0, 24)
})

const grouped = computed<GroupRow[]>(() => {
  const output: GroupRow[] = []
  for (const row of flat.value) {
    let bucket = output.find((item) => item.label === row.group)
    if (!bucket) {
      bucket = { label: row.group, items: [] }
      output.push(bucket)
    }
    bucket.items.push(row)
  }
  return output
})

function toggle() {
  if (props.disabled) return
  if (open.value) {
    open.value = false
    return
  }
  open.value = true
  active.value = 0
  void nextTick(() => textarea.value?.focus())
}

function onInput(event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
  active.value = 0
  open.value = true
}

function choose(row: FlatQuestion) {
  emit('update:modelValue', row.text)
  emit('select', row.text)
  open.value = false
  active.value = 0
  void nextTick(() => {
    const el = textarea.value
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  })
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (open.value) {
      open.value = false
      event.preventDefault()
      event.stopPropagation()
    }
    return
  }
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    open.value = false
    emit('submit')
    return
  }
  if (!open.value) {
    if (event.key === 'ArrowDown' && flat.value.length) {
      event.preventDefault()
      open.value = true
      active.value = 0
    }
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (flat.value.length) active.value = (active.value + 1) % flat.value.length
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (flat.value.length) {
      active.value = (active.value - 1 + flat.value.length) % flat.value.length
    }
    return
  }
  if (event.key === 'Enter') {
    const row = flat.value[active.value]
    if (row) {
      event.preventDefault()
      choose(row)
    }
  }
}

function onPointerDown(event: PointerEvent) {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && root.value?.contains(target)) return
  open.value = false
}

onMounted(() => document.addEventListener('pointerdown', onPointerDown))

onBeforeUnmount(() => document.removeEventListener('pointerdown', onPointerDown))

defineExpose({
  focus: () => textarea.value?.focus(),
})
</script>

<template>
  <div ref="root" class="qs">
    <div class="qs-wrap">
      <textarea
        ref="textarea"
        class="qs-input"
        :value="modelValue"
        :rows="rows"
        :maxlength="maxlength"
        :placeholder="placeholder"
        :disabled="disabled"
        aria-autocomplete="list"
        :aria-expanded="open"
        @input="onInput"
        @keydown="onKeydown"
      ></textarea>
      <button
        type="button"
        class="qs-toggle"
        :disabled="disabled"
        title="常用问题"
        @mousedown.prevent="toggle"
      >
        常用问题 ▾
      </button>
    </div>

    <div v-if="open" class="qs-menu" role="listbox">
      <template v-if="grouped.length">
        <div v-for="group in grouped" :key="group.label" class="qs-group">
          <div class="qs-group-label">{{ group.label }}</div>
          <button
            v-for="row in group.items"
            :key="row.text"
            type="button"
            class="qs-option"
            :class="{ active: row.index === active }"
            role="option"
            :aria-selected="row.index === active"
            @mousedown.prevent="choose(row)"
            @mouseenter="active = row.index"
          >
            {{ row.text }}
          </button>
        </div>
      </template>
      <div v-else class="qs-empty">没有匹配的常用问题，直接输入即可。</div>
      <div class="qs-empty">↑↓ 选择 · Enter 填入 · Ctrl+Enter 生成</div>
    </div>
  </div>
</template>
