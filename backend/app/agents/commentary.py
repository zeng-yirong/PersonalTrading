"""模型对「已经跑完的结果」做复核：回测 / 因子（strategy-review）与复盘（review-desk）。

这两个模块本身是**确定性**的——回测不调模型，复盘也不调模型。技能表里的
`strategy-review` / `review-desk` 因此没有天然加载点，这里补一个显式入口：

* 先用**确定性 rubric** 把硬门槛跑一遍（不花 token，永远可复现）；
* 需要时再调一次模型，把 rubric 结果与原始指标一起交给技能，写一段复核意见。

rubric 在前、模型在后，是为了让"没模型也能用"和"模型只是解释，不是判据"这两点
同时成立。
"""

from __future__ import annotations

from typing import Any, Optional

from app import skills
from app.agents.llm import ChatClient, LLMError

MIN_PERIODS = 10
MAX_DRAWDOWN_PCT = 25.0
MIN_WIN_RATE_PCT = 50.0

SYSTEM = """你是投研复核人。数据由我提供，只基于它写复核意见。
输出一个 JSON 对象：
{"verdict": "可用|观察池|不可用",
 "why": "120 字以内，说明依据（必须引用具体数字）",
 "risks": ["2-3 条这条结论可能错在哪"],
 "next": "下一步该做什么验证，一句话"}
不要复述数据，不要给投资建议。"""


def rubric_backtest(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """四条硬门槛，缺一条就是不可用。"""
    periods = _num(metrics.get("periods"))
    excess = _num(metrics.get("excess_cagr_pct"))
    drawdown = _num(metrics.get("max_drawdown_pct"))
    # 回测里这个字段叫 hit_rate_pct（README 也这么写），兼容旧数据的 win_rate_pct。
    win = _num(metrics.get("hit_rate_pct"))
    if win is None:
        win = _num(metrics.get("win_rate_pct"))
    return [
        {
            "name": "样本期数",
            "rule": f"≥ {MIN_PERIODS} 期",
            "value": periods,
            "passed": periods is not None and periods >= MIN_PERIODS,
        },
        {
            "name": "样本外超额",
            "rule": "> 0",
            "value": excess,
            "passed": excess is not None and excess > 0,
        },
        {
            "name": "最大回撤",
            "rule": f"≤ {MAX_DRAWDOWN_PCT:g}%",
            "value": drawdown,
            "passed": drawdown is not None and abs(drawdown) <= MAX_DRAWDOWN_PCT,
        },
        {
            "name": "胜率",
            "rule": f"≥ {MIN_WIN_RATE_PCT:g}%",
            "value": win,
            "passed": win is not None and win >= MIN_WIN_RATE_PCT,
        },
    ]


def rubric_factor(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """因子的三道门简化版：方向稳定、样本外同号、残差仍有效。"""
    icir = _num(metrics.get("icir"))
    oos = _num(metrics.get("oos_icir"))
    residual = _num(metrics.get("residual_icir"))
    return [
        {
            "name": "样本内 ICIR",
            "rule": "|ICIR| ≥ 0.3",
            "value": icir,
            "passed": icir is not None and abs(icir) >= 0.3,
        },
        {
            "name": "样本外同号且 ≥0.3",
            "rule": "同号且 |ICIR| ≥ 0.3",
            "value": oos,
            "passed": icir is not None and oos is not None and icir * oos > 0 and abs(oos) >= 0.3,
        },
        {
            "name": "残差 ICIR",
            "rule": "|ICIR| ≥ 0.15 且同号",
            "value": residual,
            "passed": (
                residual is not None
                and icir is not None
                and residual * icir > 0
                and abs(residual) >= 0.15
            ),
        },
    ]


def factor_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """把因子评估里的一行压成 rubric 需要的三个数。"""
    oos = row.get("out_of_sample") or {}
    incremental = row.get("incremental") or {}
    return {
        "icir": row.get("icir"),
        "oos_icir": oos.get("icir") if isinstance(oos, dict) else None,
        "residual_icir": incremental.get("icir") if isinstance(incremental, dict) else None,
    }


def summarise(rubric: list[dict[str, Any]]) -> str:
    passed = sum(1 for item in rubric if item["passed"])
    lines = [f"硬门槛 {passed}/{len(rubric)} 通过"]
    for item in rubric:
        mark = "✅" if item["passed"] else "❌"
        value = "—" if item["value"] is None else f"{item['value']:g}"
        lines.append(f"- {mark} {item['name']}（要求 {item['rule']}，实际 {value}）")
    return "\n".join(lines)


async def comment_backtest(
    metrics: dict[str, Any], title: str = "", model: str = "", ref_id: Optional[int] = None
) -> dict[str, Any]:
    return await _comment(
        "backtest", title or "回测复核", metrics, rubric_backtest(metrics), model, ref_id
    )


async def comment_factor(
    metrics: dict[str, Any], title: str = "", model: str = "", ref_id: Optional[int] = None
) -> dict[str, Any]:
    return await _comment(
        "factor", title or "因子复核", metrics, rubric_factor(metrics), model, ref_id
    )


async def comment_review(
    payload: dict[str, Any], model: str = ""
) -> dict[str, Any]:
    """复盘命中率的复核（review-desk）。"""
    accuracy = payload.get("accuracy") or {}
    total = accuracy.get("prediction", {}).get("total") or accuracy.get("total") or 0
    hits = accuracy.get("prediction", {}).get("hits") or accuracy.get("hits") or 0
    rate = (hits / total * 100) if total else None
    rubric = [
        {
            "name": "预测样本数",
            "rule": "≥ 20 条",
            "value": float(total),
            "passed": total >= 20,
        },
        {
            "name": "预测命中率",
            "rule": "≥ 55%（<55% 这套观察不可用）",
            "value": None if rate is None else round(rate, 1),
            "passed": rate is not None and rate >= 55.0,
        },
    ]
    return await _comment(
        "review", "复盘复核", {"accuracy": accuracy, "reviews": payload.get("reviews")}, rubric, model
    )


async def _comment(
    module: str,
    title: str,
    data: dict[str, Any],
    rubric: list[dict[str, Any]],
    model: str,
    ref_id: Optional[int] = None,
) -> dict[str, Any]:
    skill = skills.for_role(module)
    result: dict[str, Any] = {
        "module": module,
        "title": title,
        "skill": skill.label if skill else "",
        "rubric": rubric,
        "summary": summarise(rubric),
        "verdict": _verdict(rubric),
        "text": "",
        "usage": {},
    }
    if skill is None:
        return result

    client = ChatClient(model=model)
    available, reason = client.available()
    if not available:
        result["error"] = reason
        return result

    user = f"【{title}】\n指标：{_compact(data)}\n\n硬门槛结果：\n{result['summary']}\n\n请按技能写复核意见。"
    try:
        reply = await client.complete_json(
            SYSTEM + skill.prompt_block(), user, max_tokens=1200, temperature=0.1
        )
    except LLMError as exc:
        result["error"] = str(exc)
        return result

    result["text"] = str(reply.get("why") or "")
    result["verdict_model"] = str(reply.get("verdict") or "")
    result["risks"] = reply.get("risks") or []
    result["next"] = str(reply.get("next") or "")
    result["usage"] = client.usage.as_dict()
    result["skill"] = skills.record(module, ref_id, module) or result["skill"]
    return result


def _verdict(rubric: list[dict[str, Any]]) -> str:
    passed = sum(1 for item in rubric if item["passed"])
    if passed == len(rubric):
        return "观察池"
    if passed >= len(rubric) - 1:
        return "待补证据"
    return "不可用"


def _compact(data: dict[str, Any], limit: int = 900) -> str:
    import json

    text = json.dumps(data, ensure_ascii=False, default=str)
    return text[:limit]


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
