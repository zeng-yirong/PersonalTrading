<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import type { IntradayData } from '../types'

const props = defineProps<{
  data: IntradayData | null
  preClose?: number | null
}>()

interface AxisTipParam {
  dataIndex?: number
}

const container = ref<HTMLDivElement | null>(null)
let instance: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let themeObserver: MutationObserver | null = null

const hasPoints = computed(() => (props.data?.points.length ?? 0) > 0)

function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function preCloseValue(): number | null {
  const own = props.data?.pre_close
  if (typeof own === 'number' && Number.isFinite(own)) return own
  const fallback = props.preClose
  if (typeof fallback === 'number' && Number.isFinite(fallback)) return fallback
  return null
}

function fmtPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(2)
}

function tooltip(params: unknown): string {
  const list = Array.isArray(params) ? params : [params]
  const first = list[0] as AxisTipParam | undefined
  const points = props.data?.points ?? []
  const index = first?.dataIndex
  const point = index === undefined ? undefined : points[index]
  if (!point) return ''
  const prev = preCloseValue()
  const diff = prev === null ? null : point.price - prev
  const diffPct = prev === null || prev === 0 ? null : (diff! / prev) * 100
  const tone = diff === null || diff === 0 ? '' : diff > 0 ? 'color:#ff5d5d' : 'color:#23c483'
  return [
    `<strong>${point.time}</strong>`,
    `价格 ${fmtPrice(point.price)}`,
    diff === null
      ? ''
      : `<span style="${tone}">较昨收 ${diff > 0 ? '+' : ''}${diff.toFixed(2)}（${
          diffPct! > 0 ? '+' : ''
        }${diffPct!.toFixed(2)}%）</span>`,
    `均价 ${fmtPrice(point.avg)}`,
    `成交量 ${point.volume.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 手`,
    `成交额 ${(point.amount / 1e8).toFixed(2)} 亿`,
  ]
    .filter(Boolean)
    .join('<br/>')
}

function buildOption(data: IntradayData): echarts.EChartsOption {
  const points = data.points
  const times = points.map((point) => point.time)
  const prices = points.map((point) => point.price)
  const averages = points.map((point) => point.avg)
  const prev = preCloseValue()

  const accent = cssColor('--accent', '#4da3ff')
  const warn = cssColor('--warn', '#f3b34c')
  const muted = cssColor('--muted', '#8b9cb0')
  const border = cssColor('--border', '#223041')
  const panel = cssColor('--panel-2', '#15202b')
  const text = cssColor('--text', '#e7eef6')

  const extentSource = (prev === null ? [...prices, ...averages] : [...prices, ...averages, prev])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const low = extentSource.length ? Math.min(...extentSource) : 0
  const high = extentSource.length ? Math.max(...extentSource) : 1
  const span = high - low
  const pad = span > 0 ? span * 0.08 : Math.max(Math.abs(high), 1) * 0.01

  return {
    backgroundColor: 'transparent',
    animation: false,
    legend: {
      top: 2,
      right: 8,
      itemWidth: 14,
      itemHeight: 8,
      textStyle: { color: muted, fontSize: 11 },
      data: ['价格', '均价'],
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: border } },
      backgroundColor: panel,
      borderColor: border,
      textStyle: { color: text, fontSize: 12 },
      formatter: tooltip,
    },
    grid: { left: 54, right: 18, top: 30, bottom: 30 },
    xAxis: {
      type: 'category',
      data: times,
      boundaryGap: false,
      axisLine: { lineStyle: { color: border } },
      axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 10, interval: 29 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      min: Number((low - pad).toFixed(2)),
      max: Number((high + pad).toFixed(2)),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 10, formatter: (value: number) => value.toFixed(2) },
      splitLine: { lineStyle: { color: border, opacity: 0.45 } },
    },
    series: [
      {
        name: '价格',
        type: 'line',
        data: prices,
        showSymbol: false,
        smooth: false,
        lineStyle: { color: accent, width: 1.8 },
        itemStyle: { color: accent },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: `${accent}44` },
            { offset: 1, color: `${accent}00` },
          ]),
        },
        markLine:
          prev === null
            ? undefined
            : {
                symbol: 'none',
                silent: true,
                lineStyle: { color: muted, type: 'dashed', width: 1 },
                label: {
                  color: muted,
                  fontSize: 10,
                  formatter: `昨收 ${prev.toFixed(2)}`,
                  position: 'insideEndTop',
                },
                data: [{ yAxis: prev }],
              },
      },
      {
        name: '均价',
        type: 'line',
        data: averages,
        showSymbol: false,
        smooth: true,
        lineStyle: { color: warn, width: 1.2 },
        itemStyle: { color: warn },
      },
    ],
  }
}

function dispose() {
  resizeObserver?.disconnect()
  resizeObserver = null
  instance?.dispose()
  instance = null
}

function render() {
  const el = container.value
  if (!el) return
  const data = props.data
  if (!data || !data.points.length) {
    instance?.clear()
    return
  }
  if (instance && instance.getDom() !== el) dispose()
  if (!instance) instance = echarts.init(el, undefined, { renderer: 'canvas' })
  instance.setOption(buildOption(data), true)
  if (!resizeObserver) {
    resizeObserver = new ResizeObserver(() => instance?.resize())
    resizeObserver.observe(el)
  }
}

watch(
  () => [props.data, props.preClose],
  async () => {
    await nextTick()
    render()
  },
  { deep: true },
)

onMounted(async () => {
  await nextTick()
  render()
  themeObserver = new MutationObserver(() => render())
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
})

onBeforeUnmount(() => {
  themeObserver?.disconnect()
  themeObserver = null
  dispose()
})
onDeactivated(() => {
  instance?.dispose()
  instance = null
})
onActivated(() => {
  void nextTick(render)
})
</script>

<template>
  <div v-if="hasPoints" ref="container" class="intraday-chart"></div>
  <div v-else class="chart-empty">暂无分时数据</div>
</template>
