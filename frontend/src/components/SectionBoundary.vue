<script setup lang="ts">
import { onErrorCaptured, ref, watch } from 'vue'

/**
 * 一个 section 渲染炸掉时，只坏这一块，不要把整页变成空白。
 * 之前的做法是直接渲染，ScreenPanel 抛一次异常后 KeepAlive 的 patch 链就断了，
 * 后面切到回测/因子/运维全是空的。
 */
const props = defineProps<{ sectionKey: string }>()

const error = ref('')
const detail = ref('')

onErrorCaptured((exc) => {
  error.value = exc instanceof Error ? exc.message : String(exc)
  detail.value = exc instanceof Error ? (exc.stack || '').split('\n').slice(0, 4).join('\n') : ''
  // 阻止继续向上冒泡：App 仍然要能切别的模块。
  return false
})

watch(
  () => props.sectionKey,
  () => {
    error.value = ''
    detail.value = ''
  },
)
</script>

<template>
  <div v-if="error" class="panel card">
    <h3 style="margin: 0">这个模块渲染失败了</h3>
    <div class="sub-meta" style="margin-top: 6px">{{ error }}</div>
    <pre v-if="detail" class="boundary-detail">{{ detail }}</pre>
    <div style="margin-top: 10px">
      <button class="btn" type="button" @click="error = ''; detail = ''">重试</button>
    </div>
  </div>
  <slot v-else />
</template>
