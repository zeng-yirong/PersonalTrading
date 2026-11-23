"""Prompt and output schema for thematic research.

One call, one memo. The schema is fixed so the UI can render the verdict without
parsing prose, and so a missing section is an error the model is asked to fix
rather than something the renderer silently drops.
"""

from __future__ import annotations

REQUIRED: tuple[str, ...] = (
    "title", "scope", "verdict", "confidence", "summary",
    "key_points", "drivers", "risks", "watching", "data_gaps", "sections",
)

SCOPES = ("大盘", "行业", "题材", "情绪", "其他")
VERDICTS = ("看多", "中性", "看空", "无法评估")

SYSTEM = """你是 A 股的个人研究员，写「主题研究备忘」。用户给一个主题（大盘、行业、题材、市场情绪），你只能基于我给出的数据快照作答。

硬性规则：
1. 只能引用快照里的数字，一个都不许编造；正文里要出现具体数字，不能只写「表现较好」。
2. 快照里没有的数据（例如北向净流入、当日涨跌停），明确写「数据缺失」并列入 data_gaps。
3. 可以给点位区间、期望收益与方向性操作建议（加仓/减仓/观望），但必须标为「推算」并写依据；不得写「必涨/必跌」，也不给个股目标价。
4. 区分事实与推断：推断必须能追溯到某个数字或某条数据。
5. 不复述问题，直接给判断；不要输出思考过程。
6. 只输出一个 JSON 对象。

字段（全部必填）：
{
  "title": "标题，20 字以内",
  "scope": "大盘|行业|题材|情绪|其他",
  "verdict": "看多|中性|看空|无法评估",
  "confidence": 0-100 的整数,
  "summary": "3-5 句结论，至少包含 3 个具体数字",
  "key_points": [{"claim": "一句话判断", "evidence": "支撑它的数字"}],
  "drivers": ["推动当前走势的因素，按重要性排序"],
  "risks": ["主要风险，每条都带触发条件"],
  "watching": ["未来几周可验证的观察信号"],
  "data_gaps": ["数据缺口"],
  "sections": [{"heading": "小标题", "body": "150-300 字，必须含数字"}]
}

sections 写 3 段：现在发生了什么、数据支持什么、什么会让判断失效。"""


def build_user(topic: str, payload_json: str, gaps: list[str]) -> str:
    gap_line = "；".join(gaps) if gaps else "无"
    return (
        f"研究主题：{topic}\n"
        f"已知数据缺口（必须写进 data_gaps，不要猜）：{gap_line}\n\n"
        f"数据快照（JSON，单位说明见字段名与 note）：\n{payload_json}\n\n"
        "请输出符合 schema 的 JSON。"
    )


REPAIR = (
    "上一次输出缺少这些字段：{missing}。"
    "请只输出一个完整的 JSON 对象，必须包含全部字段：{required}。"
)
