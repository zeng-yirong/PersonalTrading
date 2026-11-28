"""Query DSL for the screener: validation first, SQL second.

The model (or a human) writes conditions against the field catalogue; this module
rejects anything outside it before a single character of SQL is built. Values are
always bound as parameters — the field name is the only thing interpolated, and
only after it has been whitelisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.screener.fields import BY_NAME, NUMERIC_KINDS, OPERATORS, validate_field
from app.screener.snapshot import COLUMNS

MAX_LIMIT = 200
DEFAULT_LIMIT = 30
MAX_FILTERS = 12


class QueryError(ValueError):
    """Raised for an invalid field, operator or value — surfaced to the caller."""


@dataclass
class Filter:
    field: str
    op: str
    value: Any = None
    value2: Any = None

    def sql(self) -> str:
        try:
            spec = validate_field(self.field)
        except ValueError as exc:
            raise QueryError(str(exc)) from exc
        if self.op not in OPERATORS:
            raise QueryError(f"不支持的运算符 {self.op}")
        if spec.kind == "text" and self.op not in {"==", "!=", "in", "contains"}:
            raise QueryError(f"文本字段 {self.field} 只支持 == / != / in / contains")
        if spec.kind != "text" and self.op == "contains":
            raise QueryError(f"数值字段 {self.field} 不支持 contains")

        column = f'"{self.field}"'
        if self.op == "between":
            if not isinstance(self.value, (list, tuple)) or len(self.value) != 2:
                raise QueryError("between 需要两个值 [低, 高]")
            return f"{column} BETWEEN ? AND ?"
        if self.op == "in":
            if not isinstance(self.value, (list, tuple)) or not self.value:
                raise QueryError("in 需要一个非空列表")
            placeholders = ", ".join("?" for _ in self.value)
            return f"{column} IN ({placeholders})"
        if self.op == "contains":
            return f"{column} LIKE ?"
        return f"{column} {self.op} ?"

    def params(self) -> list[Any]:
        if self.op == "between":
            return [self.value[0], self.value[1]]
        if self.op == "in":
            return list(self.value)
        if self.op == "contains":
            return [f"%{self.value}%"]
        return [self.value]


@dataclass
class ScreenQuery:
    filters: list[Filter] = field(default_factory=list)
    asset_type: str = "stock"
    boards: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    exclude_st: bool = True
    sort: list[dict[str, Any]] = field(default_factory=list)
    limit: int = DEFAULT_LIMIT
    title: str = ""
    explanation: str = ""

    # ---------------------------------------------------------------- parsing
    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScreenQuery":
        if not isinstance(payload, dict):
            raise QueryError("查询必须是一个 JSON 对象")

        universe = payload.get("universe") or {}
        if not isinstance(universe, dict):
            raise QueryError("universe 必须是一个对象，例如 {\"asset_type\":\"stock\"}")
        asset_type = str(universe.get("asset_type") or payload.get("asset_type") or "stock")
        if asset_type not in {"stock", "etf", "all"}:
            raise QueryError("asset_type 只能是 stock / etf / all")

        raw_filters = payload.get("filters") or []
        if not isinstance(raw_filters, list):
            raise QueryError("filters 必须是数组")
        if len(raw_filters) > MAX_FILTERS:
            raise QueryError(f"筛选条件最多 {MAX_FILTERS} 个")
        filters: list[Filter] = []
        for raw in raw_filters:
            if not isinstance(raw, dict) or "field" not in raw or "op" not in raw:
                raise QueryError("每个筛选条件需要 field 和 op")
            value = raw.get("value")
            if isinstance(value, list) and len(value) > 200:
                raise QueryError("in 的取值列表最多 200 个")
            filters.append(
                Filter(
                    field=str(raw["field"]),
                    op=str(raw["op"]),
                    value=value,
                    value2=raw.get("value2"),
                )
            )

        sort: list[dict[str, Any]] = []
        for raw in payload.get("sort") or []:
            if isinstance(raw, str):
                sort.append({"field": raw, "desc": True})
            elif isinstance(raw, dict) and raw.get("field"):
                sort.append({"field": str(raw["field"]), "desc": bool(raw.get("desc", True))})

        limit_value = payload.get("limit")
        try:
            limit = DEFAULT_LIMIT if limit_value is None else int(limit_value)
        except (TypeError, ValueError) as exc:
            raise QueryError(f"limit 必须是整数，收到 {limit_value!r}") from exc
        return cls(
            filters=filters,
            asset_type=asset_type,
            boards=[str(item) for item in (universe.get("boards") or [])],
            industries=[str(item) for item in (universe.get("industries") or [])],
            exclude_st=bool(universe.get("exclude_st", True)),
            sort=sort,
            limit=max(1, min(limit, MAX_LIMIT)),
            title=str(payload.get("title") or ""),
            explanation=str(payload.get("explanation") or ""),
        )

    # ------------------------------------------------------------------ SQL
    def where(self) -> tuple[str, list[Any]]:
        clauses: list[str] = ["close IS NOT NULL"]
        params: list[Any] = []

        if self.asset_type != "all":
            clauses.append("asset_type = ?")
            params.append(self.asset_type)
        if self.boards:
            clauses.append(f"board IN ({', '.join('?' for _ in self.boards)})")
            params.extend(self.boards)
        if self.industries:
            clauses.append(f"industry IN ({', '.join('?' for _ in self.industries)})")
            params.extend(self.industries)
        if self.exclude_st:
            clauses.append("name NOT LIKE '%ST%'")

        for item in self.filters:
            clauses.append(item.sql())
            params.extend(item.params())
        return " AND ".join(clauses), params

    def order_columns(self) -> str:
        """The ORDER BY body (no keyword) — reused by the backtest's row_number.

        末尾固定追加 ``symbol ASC``：只按因子排序时并列值极多（例如"近5日涨停
        次数"），DuckDB 对并列行的顺序不作保证，同一份面板重跑会选出不同的组合
        （2026-11-28 实测：同一预设两次跑出 -53.21% 与 -53.48%）。这违反 goat.md
        的第 3 条不变量，所以并列时一律按代码升序。
        """
        if not self.sort:
            return "total_mv DESC NULLS LAST, symbol ASC"
        parts = []
        for item in self.sort:
            try:
                validate_field(item["field"])
            except ValueError as exc:
                raise QueryError(str(exc)) from exc
            direction = "DESC" if item.get("desc", True) else "ASC"
            parts.append(f'"{item["field"]}" {direction} NULLS LAST')
        parts.append("symbol ASC")
        return ", ".join(parts)

    def order_by(self) -> str:
        return "ORDER BY " + self.order_columns()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "explanation": self.explanation,
            "universe": {
                "asset_type": self.asset_type,
                "boards": self.boards,
                "industries": self.industries,
                "exclude_st": self.exclude_st,
            },
            "filters": [
                {"field": item.field, "op": item.op, "value": item.value} for item in self.filters
            ],
            "sort": self.sort,
            "limit": self.limit,
        }


def run(query: ScreenQuery) -> dict[str, Any]:
    from app.data import store
    from app.screener.snapshot import ensure_built

    meta = ensure_built()
    where, params = query.where()
    columns = ", ".join(f'"{name}"' for name in COLUMNS if name in _SELECTABLE)
    sql = (
        f"SELECT {columns} FROM screen_snapshot WHERE {where} "
        f"{query.order_by()} LIMIT ?"
    )
    count_sql = f"SELECT COUNT(*) FROM screen_snapshot WHERE {where}"

    with store.session(read_only=True) as conn:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(sql, params + [query.limit]).fetchall()
        names = [item[0] for item in conn.description]

    return {
        "as_of": meta["trade_date"],
        "sources": meta.get("sources"),
        "total_matched": int(total),
        "returned": len(rows),
        "columns": names,
        "rows": [dict(zip(names, row)) for row in rows],
    }


# Columns returned to callers (a subset keeps the payload readable).
_SELECTABLE = {
    "symbol", "name", "industry", "board",
    "close", "pct_chg", "ret5", "ret20", "ret60", "amount", "avg_amount_20d",
    "turnover_rate", "volume_ratio",
    "pe_ttm", "pb", "dv_ttm", "total_mv", "pe_pct_3y",
    "roe", "revenue_yoy", "profit_yoy",
    "mf_net_20d", "margin_chg_20d", "chip_winner",
    "rsi12", "macd_hist", "gap_ma20", "drawdown_52w",
    "kdj_k", "kdj_d", "kdj_j", "cci",
    "a_vstd_20", "a_vstd_5", "a_qtl20_60", "a_corr_20", "a_corr_5",
    "a_mingap_60", "a_kbar_kup", "fd_pead",
    "limit_up_90d", "lhb_90d", "hot_rank",
}


def field_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "label": item.label,
            "kind": item.kind,
            "unit": item.unit,
            "description": item.description,
            "filterable": True,
        }
        for item in BY_NAME.values()
    ]


def describe_ops() -> dict[str, list[str]]:
    return {
        "numeric": ["<", "<=", ">", ">=", "==", "!=", "between", "in"],
        "text": ["==", "!=", "in", "contains"],
        "kinds": sorted(NUMERIC_KINDS),
    }
