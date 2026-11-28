<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type {
  PresetBatch,
  ScreenField,
  ScreenQueryPayload,
  ScreenResult,
  StockContextPayload,
} from '../types'

const emit = defineEmits<{
  (e: 'select', symbol: string): void
  (e: 'backtest', query: ScreenQueryPayload): void
  (e: 'context', payload: StockContextPayload): void
}>()

const text = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<ScreenResult | null>(null)
const fields = ref<Record<string, ScreenField>>({})
const asOf = ref('')
const symbolCount = ref(0)
const showQuery = ref(false)

const PRESETS_FALLBACK: { label: string; text: string }[] = [
  { label: '低估值蓝筹', text: '市盈率低于15、ROE大于15%、市值超过500亿' },
  { label: '高成长', text: '营收增速大于25%、净利润增速大于25%、市盈率低于60' },
  { label: '高股息防御', text: '股息率超过4%、市盈率低于20、资产负债率低于60' },
  { label: '超跌反弹', text: '股价回撤超过40%、ROE大于8%、换手率大于2' },
  { label: '量价齐升', text: '量比大于1.5、近20日涨幅大于10%、成交额大于5亿' },
  { label: '放量突破', text: '近5日涨幅大于8%、量能波动20日大于0.3、成交额大于3亿' },
  { label: '低波动', text: '量能波动20日小于0.3、市盈率低于30、成交额大于1亿' },
  { label: '业绩预增', text: '净利润增速大于50%、营收增速大于20%、市盈率低于50' },
  { label: '破净修复', text: '市净率低于1、ROE大于5、资产负债率低于70' },
  { label: '主力流入', text: '近20日主力净流入超过3亿、换手率大于2、市盈率低于40' },
  { label: '公告后强势', text: '公告后漂移大于5、市盈率低于40、成交额大于1亿' },
  { label: '涨停回踩', text: '近5日涨停、换手率大于5、市盈率低于60' },
]

const presets = ref<{ id?: string; label: string; text: string }[]>(PRESETS_FALLBACK)
const batch = ref<PresetBatch | null>(null)
const rankBusy = ref(false)
const rankNote = ref('')

/** 预设按最近一批回测排名排序；没排到的排在后面（保持原顺序）。 */
const orderedPresets = computed(() => {
  const rows = batch.value?.rows ?? []
  if (!rows.length) return presets.value
  const rank = new Map(rows.map((row) => [row.label, row.rank]))
  return [...presets.value].sort((a, b) => {
    const ra = rank.get(a.label) ?? 999
    const rb = rank.get(b.label) ?? 999
    return ra === rb ? 0 : ra - rb
  })
})

function rankOf(label: string): number | null {
  const row = batch.value?.rows.find((item) => item.label === label)
  return row?.rank ?? null
}

async function loadPresets() {
  try {
    const data = await api.screenerPresets()
    if (data.presets?.length) presets.value = data.presets
    batch.value = data.batch ?? null
  } catch (exc) {
    // 接口挂了也要能筛股：退回内置预设，只是没有排名
    rankNote.value = `排名加载失败：${exc instanceof Error ? exc.message : String(exc)}`
  }
}

async function runRanking() {
  if (rankBusy.value) return
  rankBusy.value = true
  rankNote.value = '全量回测中（12 个预设 × 近一年，约 30–90 秒）…'
  try {
    batch.value = await api.presetsRun()
    rankNote.value = `已更新：${batch.value?.created_at?.slice(0, 16) ?? ''}`
  } catch (exc) {
    rankNote.value = `全量回测失败：${exc instanceof Error ? exc.message : String(exc)}`
  } finally {
    rankBusy.value = false
  }
}

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

function openContext(row: Record<string, unknown>, event: MouseEvent) {
  const symbol = typeof row.symbol === 'string' ? row.symbol : ''
  if (!symbol) return
  const name = typeof row.name === 'string' ? row.name : ''
  emit('context', {
    symbol,
    name,
    x: event.clientX,
    y: event.clientY,
  })
}

onMounted(async () => {
  void loadPresets()
  try {
    const [fieldList, meta] = await Promise.all([api.screenerFields(), api.screenerMeta()])
    fields.value = Object.fromEntries(fieldList.fields.map((item) => [item.name, item]))
    asOf.value = meta.as_of
    symbolCount.value = meta.symbols
  } catch {
    /* screener is optional: a panel without the catalogue still renders */
  }
})

const HORIZON_TEXT: Record<string, string> = { short: '短线', mid: '中线', long: '长线' }
const HORIZON_ORDER: Record<string, number> = { short: 0, mid: 1, long: 2 }

function horizonText(value?: string): string {
  return HORIZON_TEXT[value || 'long'] || '—'
}

/** 表格里的百分比：null 显示 —，0 位小数用于胜率/仓位。 */
function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}
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
        v-for="preset in orderedPresets"
        :key="preset.label"
        class="chip"
        :disabled="loading"
        :title="rankOf(preset.label) ? `近一年超额排名第 ${rankOf(preset.label)}` : preset.text"
        @click="run(preset.text)"
      >
        <span v-if="rankOf(preset.label)" class="preset-rank">#{{ rankOf(preset.label) }}</span>
        {{ preset.label }}
      </button>
    </div>

    <details v-if="batch?.rows?.length" class="preset-ranking">
      <summary>
        预设策略近一年排名（{{ batch.start }} → {{ batch.end }}，按超额年化）
        ｜更新于 {{ (batch.created_at || '').slice(0, 16) }}
      </summary>
      <div class="screen-table-wrap">
        <table class="screen-table">
          <thead>
            <tr>
              <th>#</th>
              <th>周期</th>
              <th>预设</th>
              <th>区间收益</th>
              <th>年化</th>
              <th>超额年化</th>
              <th>最大回撤</th>
              <th>胜率</th>
              <th>仓位</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in batch.rows" :key="row.id">
              <td class="mono">{{ row.rank }}</td>
              <td class="mono" style="font-size: 11px">{{ horizonText(row.horizon) }}</td>
              <td>
                <button type="button" class="link-btn" @click="run(row.text)">{{ row.label }}</button>
              </td>
              <td class="mono" :class="(row.total_return_pct ?? 0) >= 0 ? 'up' : 'down'">
                {{ pct(row.total_return_pct) }}
              </td>
              <td class="mono">{{ pct(row.cagr_pct) }}</td>
              <td class="mono" :class="(row.excess_cagr_pct ?? 0) >= 0 ? 'up' : 'down'">
                {{ pct(row.excess_cagr_pct) }}
              </td>
              <td class="mono">{{ pct(row.max_drawdown_pct) }}</td>
              <td class="mono">{{ pct(row.hit_rate_pct, 0) }}</td>
              <td class="mono">{{ pct(row.exposure_pct, 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="preset-ranking-foot">
        <button type="button" class="btn ghost" :disabled="rankBusy" @click="runRanking">
          {{ rankBusy ? '全量回测中…' : '立即重跑' }}
        </button>
        <span v-if="rankNote" class="sub-meta">{{ rankNote }}</span>
      </div>
    </details>

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
              @contextmenu.prevent="openContext(row, $event)"
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
