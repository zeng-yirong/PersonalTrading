<script setup lang="ts">
import { computed } from 'vue'

// 右键菜单：查看详情 / 加入或移出自选 / 复制代码；Esc 关闭。
const props = defineProps<{
  x: number
  y: number
  symbol: string
  name: string
  watching: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'detail'): void
  (e: 'toggle-watch'): void
  (e: 'copy'): void
}>()

const MENU_WIDTH = 198
const MENU_HEIGHT = 180

const position = computed(() => {
  const maxX = Math.max(8, window.innerWidth - MENU_WIDTH - 8)
  const maxY = Math.max(8, window.innerHeight - MENU_HEIGHT - 8)
  return {
    left: `${Math.min(Math.max(8, props.x), maxX)}px`,
    top: `${Math.min(Math.max(8, props.y), maxY)}px`,
  }
})
</script>

<template>
  <Teleport to="body">
    <div class="ctx-menu" :style="position" role="menu" @contextmenu.prevent @click.stop>
      <div class="ctx-head">
        <strong>{{ name || symbol }}</strong>
        <span class="mono">{{ symbol }}</span>
      </div>
      <button type="button" class="ctx-item" role="menuitem" @click="emit('detail')">
        <span aria-hidden="true">↗</span>
        查看详情
      </button>
      <button
        type="button"
        class="ctx-item"
        :class="{ 'ctx-warn': watching }"
        role="menuitem"
        @click="emit('toggle-watch')"
      >
        <span aria-hidden="true">{{ watching ? '−' : '+' }}</span>
        {{ watching ? '移出自选' : '加入自选' }}
      </button>
      <div class="ctx-sep"></div>
      <button type="button" class="ctx-item" role="menuitem" @click="emit('copy')">
        <span aria-hidden="true">⧉</span>
        复制代码
        <span class="ctx-key mono">{{ symbol }}</span>
      </button>
    </div>
  </Teleport>
</template>
