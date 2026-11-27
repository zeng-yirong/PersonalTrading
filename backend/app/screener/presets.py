"""预设策略：``text`` 给人看，``query`` 给机器跑。

12 个预设以前只活在前端（`ScreenPanel.vue` 的数组），后端拿不到——所以"每周把
所有预设跑一遍回测"根本没有输入。现在它们落成数据文件：一次性把中文原话翻译成
结构化条件，之后每周的回测**不需要再调模型**（0 token，也不会因为模型漂移而
把同一个策略跑成两套条件）。

加载时用选股器的 :func:`ScreenQuery.from_dict` 校验一遍：字段名、单位、运算符
写错了会在 import 阶段炸掉，而不是等到周五晚上跑出一堆空结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.screener import query as screen_query

PRESETS_FILE = Path(__file__).with_name("presets.json")


class PresetError(RuntimeError):
    """预设文件不合规。"""


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    text: str
    query: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "text": self.text, "query": self.query}


def _load(path: Path = PRESETS_FILE) -> tuple[Preset, ...]:
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise PresetError(f"presets.json 不是合法 JSON：{exc}") from exc

    presets: list[Preset] = []
    seen: set[str] = set()
    for entry in payload.get("presets") or []:
        identifier = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or "").strip()
        text = str(entry.get("text") or "").strip()
        raw_query = entry.get("query")
        if not identifier or not label:
            raise PresetError("每个预设都要有 id 与 label")
        if identifier in seen:
            raise PresetError(f"预设 id 重复：{identifier}")
        if not isinstance(raw_query, dict) or not raw_query.get("filters"):
            raise PresetError(f"预设 {identifier} 缺少 query.filters")
        try:
            parsed = screen_query.ScreenQuery.from_dict(raw_query)
            # from_dict 只做形状校验；字段白名单、运算符与单位要等到 where()/order_by()
            # 才生效。这里先跑一遍，把错误拦在 import 阶段。
            parsed.where()
            parsed.order_by()
        except screen_query.QueryError as exc:
            raise PresetError(f"预设 {identifier} 的条件不合法：{exc}") from exc
        seen.add(identifier)
        presets.append(Preset(identifier, label, text, raw_query))
    return tuple(presets)


PRESETS: tuple[Preset, ...] = _load()
BY_ID: dict[str, Preset] = {preset.id: preset for preset in PRESETS}


def as_list() -> list[dict[str, Any]]:
    return [preset.to_dict() for preset in PRESETS]
