<script setup lang="ts">
/**
 * 组合（占位）。
 *
 * 多子策略组合还没有实现：`strategy_runs` 只存了指标，没有日收益序列，
 * 算不出组合曲线，也算不出策略间相关性——没有相关性就无法判断"组合"是不是
 * 把同一个策略买了三份。所以这里只占位，先说明缺什么、按什么顺序补。
 */
const STEPS = [
  { id: 'P9-0', title: '存日收益序列 + 相关性矩阵', why: '回测结果写 nav_json，算两两相关与持仓重叠率', status: '待做' },
  { id: 'P9-1', title: '组合定义 + 离线加权检验', why: '等权 / 反波动率，先用已存收益近似，不建新引擎', status: '待做' },
  { id: 'P9-2', title: '组合回测引擎', why: '主日历合并信号 → 去重 → 权重分配 → 复用现有撮合', status: '待做' },
  { id: 'P9-3', title: 'UI：组合 tab', why: '成员权重、曲线对比、成员相关性', status: '待做' },
]
</script>

<template>
  <section class="panel card">
    <div class="research-head">
      <div>
        <h3 style="margin: 0">策略组合（占位）</h3>
        <div class="sub-meta" style="margin-top: 4px">
          多个子策略的组合功能暂未实现。入口先占位，避免以后加功能时又要改导航。
        </div>
      </div>
      <span class="strategy-status status-template">未实现</span>
    </div>

    <div class="error-bar" style="margin-top: 12px">
      为什么还不能做：<code>strategy_runs</code> 只存了超额 / 回撤 / 胜率，<strong>没有日收益序列</strong>。
      没有日收益就算不出组合曲线与策略间相关性——低波、红利、布林收口很可能是同一个策略，
      不先算相关性就组合，只是把同一份风险买三遍。
    </div>

    <div class="screen-table-wrap" style="margin-top: 12px">
      <table class="screen-table">
        <thead>
          <tr>
            <th>#</th>
            <th>步骤</th>
            <th>为什么</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="step in STEPS" :key="step.id">
            <td class="mono">{{ step.id }}</td>
            <td>{{ step.title }}</td>
            <td class="sub-meta" style="text-align: left">{{ step.why }}</td>
            <td class="mono">{{ step.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="sub-meta" style="margin-top: 10px">
      判定标准已经写死在 <code>todo.md</code> §5：组合夏普 ≥ 最好单策略 ×1.1、成员相关 &lt;0.8、
      组合换手 ≤ 成员之和的 60%、两年窗口为正才进观察。
    </div>

    <div style="margin-top: 12px">
      <button type="button" class="btn" disabled title="P9-2 之后再开放">
        新建组合（未开放）
      </button>
    </div>
  </section>
</template>
