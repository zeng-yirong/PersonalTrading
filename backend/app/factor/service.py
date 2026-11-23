"""Factor mining orchestration: validate the request, evaluate, keep the run."""

from __future__ import annotations

import threading
from datetime import date, timedelta
from typing import Any, Optional

from app.factor import evaluate, library, store
from app.factor.evaluate import FactorError, HORIZONS, Spec

RUN_LOCK = threading.Lock()   # one writer at a time: the panel is a single file
DEFAULT_YEARS = 3
MAX_YEARS = 5


def meta() -> dict[str, Any]:
    from app.data import store as panel

    with panel.session(read_only=True) as conn:
        row = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily").fetchone()
    return {
        "panel": {
            "start": row[0].isoformat() if row and row[0] else None,
            "end": row[1].isoformat() if row and row[1] else None,
        },
        "defaults": {
            "start_years": DEFAULT_YEARS,
            "rebalance": 20,
            "horizon": 20,
        },
        "limits": {
            "max_years": MAX_YEARS,
            "rebalance": [5, 10, 20, 60],
            "horizons": list(HORIZONS),
            "min_sections": 12,
        },
        "factors": library.catalogue(include_imported=True),
        "imported": {
            "alpha158": len(library.alpha158_factors()),
            "academic": len(library.academic_factors()),
            "gtja": len(library.gtja_factors()),
            "fundamental": len(library.fundamental_factors()),
            "note": "用 source=alpha158 / academic / gtja / fundamental 分别评估；all 全跑",
        },
        "metrics": {
            "ic": "每个截面因子值与未来收益的 Spearman 秩相关（平均秩处理并列值）",
            "icir": "IC 均值 / IC 标准差；|ICIR| < 0.1 基本等同于噪音",
            "t_stat": "ICIR × √期数，|t| < 2 时不要下结论",
            "spread": "第 5 分组减第 1 分组的平均前瞻收益（百分点）",
        },
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    from app.data import store as panel

    with panel.session(read_only=True) as conn:
        row = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily").fetchone()
    panel_start = row[0] if row else None
    panel_end = row[1] if row else None
    if panel_end is None:
        raise FactorError("面板里还没有行情，请先运行 scripts/sync_market.py")

    end = _date(payload.get("end")) or panel_end
    start = _date(payload.get("start")) or (end - timedelta(days=365 * DEFAULT_YEARS))
    if panel_start and start < panel_start:
        start = panel_start
    if end > panel_end:
        end = panel_end
    if start >= end:
        raise FactorError(f"开始日 {start} 必须早于结束日 {end}")
    if (end - start).days > 365 * MAX_YEARS:
        raise FactorError(f"区间最长 {MAX_YEARS} 年")
    if (end - start).days < 365:
        raise FactorError("至少需要一年数据，否则 IC 的统计意义不足")

    rebalance = int(payload.get("rebalance") or 20)
    if not 1 <= rebalance <= 250:
        raise FactorError("调仓周期必须在 1-250 个交易日之间")

    horizon = int(payload.get("horizon") or 20)
    if horizon not in HORIZONS:
        raise FactorError(f"前瞻期只能是 {'、'.join(str(h) for h in HORIZONS)} 个交易日")

    oos_months = int(payload.get("oos_months") or 0)
    if oos_months and not 3 <= oos_months <= 24:
        raise FactorError("样本外区间只能是 0（关闭）或 3-24 个月")

    source = str(payload.get("source") or "panel").strip().lower()
    if source not in {"panel", "alpha158", "academic", "gtja", "fundamental", "all"}:
        raise FactorError("source 只能是 panel / alpha158 / academic / gtja / fundamental / all")
    incremental = bool(payload.get("incremental"))

    names = [str(item) for item in (payload.get("factors") or [])]
    try:
        if names:
            factors = library.validate(names)
        elif source == "alpha158":
            factors = library.alpha158_factors()
        elif source == "academic":
            factors = library.academic_factors()
        elif source == "gtja":
            factors = library.gtja_factors()
        elif source == "fundamental":
            factors = library.fundamental_factors()
        elif source == "all":
            factors = list(library.panel_factors()) + library.imported()
        else:
            factors = list(library.panel_factors())
    except ValueError as exc:
        raise FactorError(str(exc)) from exc

    spec = Spec(
        start=start,
        end=end,
        rebalance=rebalance,
        horizon=horizon,
        factors=factors,
        oos_months=oos_months,
        incremental=incremental,
    )
    with RUN_LOCK:
        result = evaluate.run(spec)
    result["id"] = store.save(result)
    result["created_at"] = _created_at(result["id"])
    return result


def history(limit: int = 20) -> list[dict[str, Any]]:
    return store.list_runs(limit)


def detail(run_id: int) -> Optional[dict[str, Any]]:
    return store.get(run_id)


def _date(raw: Any) -> Optional[date]:
    if raw in (None, ""):
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise FactorError(f"日期格式应为 YYYY-MM-DD：{raw}") from exc


def _created_at(run_id: int) -> str:
    record = store.get(run_id)
    return record["created_at"] if record else ""
