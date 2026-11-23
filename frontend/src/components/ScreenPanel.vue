<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { ScreenField, ScreenQueryPayload, ScreenResult } from '../types'

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'backtest', query: ScreenQueryPayload): void
}>()

const text = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<ScreenResult | null>(null)
const fields = ref<Record<string, ScreenField>>({})
const asOf = ref('')
const symbolCount = ref(0)
const showQuery = ref(false)

const PRESETS = [
  '市盈率低于30、ROE大于15%、20日主力净流入为正',
  '近20日主力净流入超过5亿、换手率大于3%、市盈率低于行业中位数',
  '股价回撤超过30%但ROE大于12%、资产负债率低于50%',
  '股息率超过4%、市盈率低于15、市值超过200亿',
  '近5日涨停、龙虎榜上榜过、流通市值低于100亿',
]

const visibleFields = computed(() => {
  if (!result.value) return []
  return result.value.columns.filter((name) => name !== 'symbol')
})

function fmt(value: any, field: string): string {
  if (value === null || value === undefined) return '—'
  const spec = fields.value[field]
  if (typeof value !== 'number') return String(value)
  if (!spec) return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  if (spec.kind === 'money') {
    const amount = spec.unit === '万元' ? value * 1e4 : value
    if (Math.abs(amount) >= 1e8) return `${(amount / 1e8).toFixed(2)} 亿`
    if (Math.abs(amount) >= 1e4) return `${(amount / 1e4).toFixed(1)} 万`
    return amount.toFixed(0)
  }
  if (spec.kind === 'ratio') {
    return `${value.toFixed(2)}${spec.unit === '%' ? '%' : ''}`
  }
  if (spec.kind === 'count' || spec.kind === 'rank') return value.toFixed(0)
  return String(value)
}

function label(field: string): string {
  if (field === 'name') return '名称'
  if (field === 'industry') return '行业'
  return fields.value[field]?.label || field
}

async function run(preset?: string) {
  if (preset) text.value = preset
  const query = text.value.trim()
  if (!query || loading.value) return
  loading.value = true
  error.value = ''
  try {
    result.value = await api.screenerAsk(query, 50)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
    result.value = null
  } finally {
    loading.value = false
  }
}

function sendBacktest() {
  const query = result.value?.query
  if (!query) return
  emit('backtest', query)
}

onMounted(async () => {
  try {
    const [fieldList, meta] = await Promise.all([api.screenerFields(), api.screenerMeta()])
    fields.value = Object.fromEntries(fieldList.fields.map((item) => [item.name, item]))
    asOf.value = meta.as_of
    symbolCount.value = meta.symbols
  } catch {
    /* screener is optional: a panel without the catalogue still renders */
  }
})
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">选股器</h3>
        <div class="sub-meta" style="margin-top: 4px">
          用自然语言描述条件，直接查全市场横截面
          <span v-if="asOf">（数据截至 {{ asOf }}，{{ symbolCount }} 只）</span>
        </div>
      </div>
      <button class="btn" :disabled="loading || !text.trim()" @click="run()">
        {{ loading ? '筛选中…' : '开始筛选' }}
      </button>
    </div>

    <textarea
      v-model="text"
      class="screen-input"
      rows="2"
      placeholder="例如：市盈率低于30、ROE大于15%、近20日主力净流入为正、近5日没有涨停"
      @keydown.ctrl.enter="run()"
    ></textarea>

    <div class="screen-presets">
      <button
        v-for="preset in PRESETS"
        :key="preset"
        class="chip"
        :disabled="loading"
        @click="run(preset)"
      >
        {{ preset.slice(0, 14) }}…
      </button>
    </div>

    <div v-if="error" class="error-bar" style="margin-top: 10px">{{ error }}</div>

    <div v-if="result" style="margin-top: 12px">
      <div class="screen-result-head">
        <div class="sub-meta" style="margin: 0">
          {{ result.query?.title || '筛选结果' }}——{{ result.query?.explanation }}
          ｜命中 <strong>{{ result.total_matched }}</strong> 只，展示 {{ result.returned }} 只
        </div>
        <button class="btn ghost" :disabled="!result.query" @click="sendBacktest">
          用这些条件回测
        </button>
      </div>

      <div class="screen-table-wrap">
        <table class="screen-table">
          <thead>
            <tr>
              <th>代码</th>
              <th v-for="field in visibleFields" :key="field">{{ label(field) }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in result.rows"
              :key="row.symbol"
              class="screen-row"
              @click="emit('select', row.symbol)"
            >
              <td class="mono">{{ row.symbol }}</td>
              <td v-for="field in visibleFields" :key="field">{{ fmt(row[field], field) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details class="screen-query" :open="showQuery" @toggle="showQuery = !showQuery">
        <summary>查看查询条件</summary>
        <pre>{{ JSON.stringify(result.query, null, 2) }}</pre>
      </details>
    </div>

    <div v-else-if="!loading" class="loading" style="margin-top: 12px">
      输入条件或点一个预设，结果里的任意一行可以直接点开做研究。
    </div>
  </section>
</template>
