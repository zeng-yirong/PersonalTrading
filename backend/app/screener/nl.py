"""Turn a Chinese screening request into a validated query.

The model never writes SQL. It fills a JSON condition list against a closed field
catalogue; anything unrecognised is rejected and the model gets one corrective
round with the exact error, so a typo cannot silently become a wrong screen.
"""

from __future__ import annotations

from typing import Any

from app.agents.llm import ChatClient, LLMError
from app.screener.fields import describe_for_prompt
from app.screener.query import QueryError, ScreenQuery
from app import skills

SYSTEM = """你是 A 股选股条件解析器。把用户的中文需求翻译成一个 JSON 查询对象。

硬性规则：
1. 只能使用下面列出的字段名，不得自创字段。
2. 数值单位必须与说明一致：涨跌幅/占比类字段用百分数（15 表示 15%），
   金额类字段按说明的货币单位填写（如 total_mv 用元，50 亿要写 5000000000；
   mf_net_20d 用万元）。
3. 用户没提到的条件不要擅自添加；宁少勿多。
4. 只输出一个 JSON 对象，不要解释文字，不要输出思考过程。

可用字段：
{catalog}

输出格式：
{{
  "title": "给这个筛选起个短标题，10 字以内",
  "explanation": "一句话说明你理解的条件（60 字以内）",
  "universe": {{
    "asset_type": "stock" | "etf" | "all",
    "boards": [],          // 可填 沪市主板/深市主板/创业板/科创板/北交所/ETF
    "industries": [],      // 例如 ["白酒","银行"]
    "exclude_st": true
  }},
  "filters": [
    {{"field": "pe_ttm", "op": "<=", "value": 30}},
    {{"field": "roe", "op": ">=", "value": 15}}
  ],
  "sort": [{{"field": "roe", "desc": true}}],
  "limit": 30
}}

op 可用：< <= > >= == != between（值写 [低, 高]）in（值写列表）contains（仅文本字段）。
"""


async def parse(text: str, *, limit: int = 30) -> ScreenQuery:
    client = ChatClient()
    ok, reason = client.available()
    if not ok:
        raise LLMError(reason)

    system = SYSTEM.format(catalog=describe_for_prompt()) + skills.prompt_suffix("screener")
    user = f"选股需求：{text}\n\n请输出 JSON，limit 用 {limit}。"

    payload = await client.complete_json(system, user, max_tokens=1200)
    try:
        query = ScreenQuery.from_dict(payload)
    except QueryError as exc:
        # One corrective round with the precise complaint.
        corrective = (
            f"{user}\n\n上一次的输出有问题：{exc}。"
            "请修正后重新输出完整 JSON，只能使用给定字段名与单位。"
        )
        payload = await client.complete_json(system, corrective, max_tokens=1200)
        try:
            query = ScreenQuery.from_dict(payload)
        except QueryError as second:
            raise QueryError(f"无法解析为有效查询：{second}") from second
    skills.record("screener")
    return query


async def parse_and_run(text: str, *, limit: int = 30) -> dict[str, Any]:
    from app.screener.query import run

    query = await parse(text, limit=limit)
    result = run(query)
    result["query"] = query.to_dict()
    return result
