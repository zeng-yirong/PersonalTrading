<script setup lang="ts">
import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChartData } from '../types'

const props = defineProps<{ chart: ChartData | null }>()

const container = ref<HTMLDivElement | null>(null)
let instance: echarts.ECharts | null = null
let observer: ResizeObserver | null = null
let themeObserver: MutationObserver | null = null

function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function buildOption(chart: ChartData): echarts.EChartsOption {
  const UP = cssColor('--up', '#ff5d5d')
  const DOWN = cssColor('--down', '#23c483')
  const MUTED = cssColor('--muted', '#8b9cb0')
  const BORDER = cssColor('--border', '#223041')
  const PANEL = cssColor('--panel-2', '#15202b')
  const TEXT = cssColor('--text', '#e7eef6')

  const dates = chart.candles.map((item) => item.trade_date)
  const kline = chart.candles.map((item) => [
    item.open ?? 0,
    item.close ?? 0,
    item.low ?? 0,
    item.high ?? 0,
  ])
  const volumes = chart.candles.map((item, index) => ({
    value: item.volume ?? 0,
    itemStyle: {
      color:
        (item.close ?? 0) >= (item.open ?? 0)
          ? 'rgba(255,93,93,0.55)'
          : 'rgba(35,196,131,0.55)',
    },
    name: item.trade_date,
    index,
  }))

  const maSeries = [
    { name: 'MA5', data: chart.indicators.ma5, color: '#f3b34c' },
    { name: 'MA10', data: chart.indicators.ma10, color: '#4da3ff' },
    { name: 'MA20', data: chart.indicators.ma20, color: '#b48cff' },
    { name: 'MA60', data: chart.indicators.ma60, color: MUTED },
  ]

  return {
    backgroundColor: 'transparent',
    animation: false,
    legend: {
      top: 6,
      left: 48,
      textStyle: { color: MUTED, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: BORDER } },
      backgroundColor: PANEL,
      borderColor: BORDER,
      textStyle: { color: TEXT, fontSize: 12 },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 52, right: 64, top: 38, height: '58%' },
      { left: 52, right: 64, top: '72%', height: '14%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: BORDER } },
        axisLabel: { color: MUTED, fontSize: 11 },
        splitLine: { show: false },
        axisPointer: { label: { show: true } },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: BORDER } },
        axisLabel: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        position: 'right',
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: MUTED, fontSize: 11 },
        splitLine: { lineStyle: { color: 'rgba(34,48,65,0.55)' } },
      },
      {
        scale: true,
        gridIndex: 1,
        position: 'right',
        splitNumber: 3,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: MUTED,
          fontSize: 10,
          formatter: (value: number) => {
            if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)}亿`
            if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(0)}万`
            return String(value)
          },
        },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 40, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        bottom: 4,
        height: 16,
        borderColor: BORDER,
        fillerColor: 'rgba(77,163,255,0.12)',
        textStyle: { color: MUTED, fontSize: 10 },
      },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: kline,
        itemStyle: {
          color: UP,
          color0: DOWN,
          borderColor: UP,
          borderColor0: DOWN,
        },
      },
      ...maSeries.map((item) => ({
        name: item.name,
        type: 'line' as const,
        data: item.data,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.1, color: item.color },
        emphasis: { focus: 'series' as const },
      })),
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barMaxWidth: 8,
      },
    ],
  }
}

function render() {
  if (!container.value) return
  if (!instance) {
    instance = echarts.init(container.value, undefined, { renderer: 'canvas' })
  }
  if (props.chart && props.chart.candles.length) {
    instance.setOption(buildOption(props.chart), true)
  } else {
    instance.clear()
  }
}

onMounted(() => {
  render()
  if (container.value) {
    observer = new ResizeObserver(() => instance?.resize())
    observer.observe(container.value)
  }
  themeObserver = new MutationObserver(() => render())
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
})

onBeforeUnmount(() => {
  themeObserver?.disconnect()
  themeObserver = null
  observer?.disconnect()
  instance?.dispose()
  instance = null
})

watch(
  () => props.chart,
  async () => {
    await nextTick()
    render()
  },
  { deep: true },
)
</script>

<template>
  <div ref="container" class="chart"></div>
</template>