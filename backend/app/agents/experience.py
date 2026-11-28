"""经验记忆（P8-0）：从复盘里抽可复用的经验卡，决策时影子召回。

范围刻意收窄：

* **写**：只从 ``agent_reviews`` 抽候选卡；不调用模型，纯规则、可复现。
* **读**：只在“作出新决策”时召回；P8-0 只记录（``experience_recalls``），
  **不改 prompt**。等影子召回的精确率被证明后，P8-2 才注入。
* **门槛**：1–2 条证据的卡只是 candidate，永不注入；≥3 条独立证据且人工确认
  才 active。反证达到 2 条则 retired，留档不删。

卡片是结构化的（trigger / lesson / action / evidence），不是复盘摘要：没有
触发条件无法召回，没有证伪条件无法淘汰。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any, Optional

from app.db import connection, now_iso
from app.config import settings

MAX_EVIDENCE_JSON = 12000
STATUSES = ("candidate", "active", "retired")
ACTIVE_MIN_EVIDENCE = 3
TRANSITIONS = {
    "candidate": {"active", "retired"},
    "active": {"candidate", "retired"},
    "retired": {"candidate"},
}

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS experiences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL,
        scope_key TEXT,
        source TEXT NOT NULL,
        trigger_json TEXT NOT NULL,
        lesson TEXT NOT NULL,
        action_json TEXT,
        evidence_json TEXT NOT NULL,
        confidence INTEGER NOT NULL DEFAULT 40,
        status TEXT NOT NULL DEFAULT 'candidate',
        status_reason TEXT,
        status_changed_at TEXT,
        as_of TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS idx_experience_status ON experiences(status, scope)",
    "CREATE INDEX IF NOT EXISTS idx_experience_asof ON experiences(as_of DESC)",
    """CREATE TABLE IF NOT EXISTS experience_recalls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        experience_id INTEGER NOT NULL,
        score REAL,
        status TEXT,
        injected INTEGER NOT NULL DEFAULT 0,
        features_json TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(run_id, experience_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_experience_recalls_run ON experience_recalls(run_id)",
)


def ensure_schema() -> None:
    with connection() as conn:
        for statement in _SCHEMA:
            conn.execute(statement)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(experiences)")}
        for column in ("status_reason", "status_changed_at"):
            if column not in existing:
                conn.execute(f"ALTER TABLE experiences ADD COLUMN {column} TEXT")


def _iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _fingerprint(
    scope: str, scope_key: Optional[str], trigger: dict[str, Any], lesson: str
) -> str:
    payload = json.dumps(
        {"scope": scope, "key": scope_key, "trigger": trigger, "lesson": lesson},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _confidence(evidence: int, contradictions: int = 0) -> int:
    return max(0, min(90, 40 + 10 * evidence - 15 * contradictions))


# ------------------------------------------------------------------ features
def features_from_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Features available *before* a decision; retrieval only uses these."""
    market = ctx.get("market") or {}
    benchmarks = market.get("benchmarks") or []
    benchmark = next(
        (item for item in benchmarks if item.get("ts_code") == "000300.SH"), {}
    )
    regime = ctx.get("regime") or {}
    breadth = regime.get("breadth") or {}
    technical = ctx.get("technical") or {}
    price = ctx.get("price") or {}
    profile = ctx.get("profile") or {}
    return {
        "index_20d": benchmark.get("return_20d"),
        "index_5d": benchmark.get("return_5d"),
        "rsi14": technical.get("rsi14"),
        "close_vs_ma20": technical.get("close_vs_ma20_pct"),
        "drawdown_from_high": price.get("drawdown_from_high"),
        "ret20": price.get("return_20d"),
        "industry": profile.get("industry"),
        "board": profile.get("board"),
        "limit_up": breadth.get("limit_up"),
        "limit_down": breadth.get("limit_down"),
    }


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def match(trigger: dict[str, Any], features: dict[str, Any]) -> bool:
    """All trigger conditions must hold; a missing feature never matches."""
    if not isinstance(trigger, dict) or not trigger:
        return False
    for key, expected in trigger.items():
        base, _, op = key.rpartition("_")
        if not base:
            return False
        actual = _as_float(features.get(base))
        target = _as_float(expected)
        if actual is None or target is None:
            return False
        if op == "lt" and not actual < target:
            return False
        if op == "lte" and not actual <= target:
            return False
        if op == "gt" and not actual > target:
            return False
        if op == "gte" and not actual >= target:
            return False
        if op == "eq" and actual != target:
            return False
    return True


def _scope_match(item: dict[str, Any], features: dict[str, Any]) -> bool:
    scope = item.get("scope")
    if scope == "industry":
        return bool(item.get("scope_key")) and item.get("scope_key") == features.get(
            "industry"
        )
    return scope in {"market", "decision", "role"}


def _recency(as_of: Optional[str], today: Optional[str]) -> float:
    if not as_of or not today:
        return 0.0
    try:
        days = (date.fromisoformat(today) - date.fromisoformat(as_of)).days
    except ValueError:
        return 0.0
    return math.exp(-max(0, days) / 90.0)


def _item(row: Any) -> dict[str, Any]:
    evidence = json.loads(row["evidence_json"] or "[]")
    return {
        "id": row["id"],
        "fingerprint": row["fingerprint"],
        "scope": row["scope"],
        "scope_key": row["scope_key"],
        "source": row["source"],
        "trigger": json.loads(row["trigger_json"] or "{}"),
        "lesson": row["lesson"],
        "action": json.loads(row["action_json"] or "{}"),
        "confidence": row["confidence"],
        "status": row["status"],
        "status_reason": row["status_reason"] if "status_reason" in row.keys() else None,
        "status_changed_at": (
            row["status_changed_at"] if "status_changed_at" in row.keys() else None
        ),
        "as_of": row["as_of"],
        "evidence_count": len(evidence),
        "can_activate": row["status"] == "candidate" and len(evidence) >= ACTIVE_MIN_EVIDENCE,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
    }


# ------------------------------------------------------------------- extract
def _patterns(features: dict[str, Any], position: float, outcome: str) -> list[dict]:
    """Deterministic failure patterns; every card must be decision-actionable."""
    index_20d = _as_float(features.get("index_20d"))
    rsi = _as_float(features.get("rsi14"))
    gap = _as_float(features.get("close_vs_ma20"))
    wrong = "错误" in outcome
    cards: list[dict] = []

    if wrong and position > 0 and index_20d is not None and index_20d <= -3:
        cards.append(
            {
                "scope": "market",
                "scope_key": "weak_tape",
                "trigger": {"index_20d_lte": -3},
                "lesson": "沪深300 20 日跌幅达到 3% 以上时，逆势批准正仓位的事后错误率偏高；仓位应降一档",
                "action": {"position_multiplier": 0.6},
            }
        )
    if wrong and position <= 0 and index_20d is not None and index_20d >= 3:
        cards.append(
            {
                "scope": "market",
                "scope_key": "strong_tape_miss",
                "trigger": {"index_20d_gte": 3},
                "lesson": "沪深300 20 日涨幅达到 3% 以上时，空仓/否决错过上涨的概率偏高；至少保留观察仓",
                "action": {"min_position_pct": 10},
            }
        )
    if (
        wrong
        and position > 0
        and rsi is not None
        and rsi >= 65
        and gap is not None
        and gap >= 3
    ):
        cards.append(
            {
                "scope": "decision",
                "scope_key": "chase_overbought",
                "trigger": {"rsi14_gte": 65, "close_vs_ma20_gte": 3},
                "lesson": "RSI14 ≥65 且偏离 MA20 ≥3% 时追多，20 日事后错误率偏高；等回踩再建仓",
                "action": {"entry": "wait_pullback"},
            }
        )
    return cards


def extract(limit: Optional[int] = None) -> dict[str, int]:
    """Build/merge candidate cards from judged reviews. Idempotent."""
    ensure_schema()
    sql = (
        "SELECT v.run_id, v.symbol, v.position_pct, v.d20_return, v.d20_excess, "
        "v.outcome, v.entry_date, v.d20_date, r.context_json, r.verdict "
        "FROM agent_reviews v JOIN agent_runs r ON r.id = v.run_id "
        "WHERE v.d20_return IS NOT NULL AND r.context_json IS NOT NULL "
        "ORDER BY v.run_id"
    )
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    created = merged = skipped = 0
    with connection() as conn:
        for row in rows:
            try:
                ctx = json.loads(row["context_json"] or "{}")
            except (TypeError, ValueError):
                skipped += 1
                continue
            verdict = json.loads(row["verdict"]) if row["verdict"] else {}
            position = _as_float(row["position_pct"])
            if position is None:
                position = _as_float(verdict.get("final_position_pct")) or 0.0
            features = features_from_context(ctx)
            outcome = str(row["outcome"] or "")
            learned_at = _iso(row["d20_date"]) or _iso(row["entry_date"])
            evidence = {
                "kind": "review",
                "ref_id": int(row["run_id"]),
                "symbol": row["symbol"],
                "date": _iso(row["entry_date"]),
                "metric": row["d20_return"],
                "excess": row["d20_excess"],
                "outcome": outcome,
            }
            for card in _patterns(features, position, outcome):
                fp = _fingerprint(
                    card["scope"], card["scope_key"], card["trigger"], card["lesson"]
                )
                existing = conn.execute(
                    "SELECT * FROM experiences WHERE fingerprint = ?", (fp,)
                ).fetchone()
                if existing:
                    items = json.loads(existing["evidence_json"] or "[]")
                    if any(int(item.get("ref_id") or -1) == evidence["ref_id"] for item in items):
                        skipped += 1
                        continue
                    items.append(evidence)
                    payload = json.dumps(items[-80:], ensure_ascii=False)
                    if len(payload) > MAX_EVIDENCE_JSON:
                        payload = json.dumps(items[-40:], ensure_ascii=False)
                    confidence = _confidence(len(items))
                    as_of = max(filter(None, [_iso(existing["as_of"]), learned_at]), default=None)
                    conn.execute(
                        "UPDATE experiences SET evidence_json = ?, confidence = ?, "
                        "as_of = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                        (payload, confidence, as_of, now_iso(), existing["id"]),
                    )
                    merged += 1
                else:
                    conn.execute(
                        "INSERT INTO experiences(fingerprint, scope, scope_key, source, "
                        "trigger_json, lesson, action_json, evidence_json, confidence, "
                        "status, as_of, created_at, updated_at) "
                        "VALUES(?, ?, ?, 'review', ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)",
                        (
                            fp,
                            card["scope"],
                            card["scope_key"],
                            json.dumps(card["trigger"], ensure_ascii=False),
                            card["lesson"],
                            json.dumps(card["action"], ensure_ascii=False),
                            json.dumps([evidence], ensure_ascii=False),
                            _confidence(1),
                            learned_at,
                            now_iso(),
                            now_iso(),
                        ),
                    )
                    created += 1
    return {"created": created, "merged": merged, "skipped": skipped}


# -------------------------------------------------------------------- recall
def recall(
    features: dict[str, Any],
    as_of: Optional[str] = None,
    limit: int = 3,
    include_candidate: bool = False,
) -> list[dict[str, Any]]:
    """Match active cards (optionally candidates) against decision-time features."""
    ensure_schema()
    statuses = ["active"] + (["candidate"] if include_candidate else [])
    marks = ",".join("?" for _ in statuses)
    sql = f"SELECT * FROM experiences WHERE status IN ({marks})"
    params: list[Any] = list(statuses)
    cutoff = _iso(as_of)
    if cutoff:
        sql += " AND as_of IS NOT NULL AND date(as_of) <= date(?)"
        params.append(cutoff)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    scored = []
    for row in rows:
        item = _item(row)
        if not _scope_match(item, features):
            continue
        if not match(item["trigger"], features):
            continue
        score = (
            0.55
            + 0.25 * (item["confidence"] / 100.0)
            + 0.20 * _recency(item["as_of"], cutoff)
        )
        item["score"] = round(score, 4)
        scored.append(item)
    scored.sort(key=lambda item: (-item["score"], item["as_of"] or "", -item["id"]))
    return scored[: max(1, limit)]


EXPERIENCE_LABEL = "经验（不是事实）"


def render(items: list[dict[str, Any]], max_chars: Optional[int] = None) -> str:
    """Prompt block for the decision layer. Empty when there is nothing."""
    if not items:
        return ""
    cap = max(120, int(max_chars or settings.experience_block_chars))
    lines = [
        f"【{EXPERIENCE_LABEL}】来自历史复盘，只能对照，不能当作今天的数字或事实："
    ]
    for item in items:
        lines.append(
            f"- {item.get('as_of') or '—'}｜{item.get('evidence_count') or 0} 次复盘"
            f"｜{str(item.get('lesson') or '')[:120]}"
        )
    while len("\n".join(lines)) > cap and len(lines) > 2:
        lines.pop()
    return "\n".join(lines)


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [f"experience:{item['id']}" for item in items]


def shadow_recall(run_id: int, ctx: dict[str, Any]) -> int:
    """Log what would be recalled for this run; never changes the prompt."""
    ensure_schema()
    features = features_from_context(ctx)
    items = recall(features, as_of=ctx.get("as_of"), limit=5, include_candidate=True)
    if not items:
        return 0
    with connection() as conn:
        for item in items:
            conn.execute(
                "INSERT OR IGNORE INTO experience_recalls(run_id, experience_id, score, "
                "status, injected, features_json, created_at) VALUES(?, ?, ?, ?, 0, ?, ?)",
                (
                    int(run_id),
                    int(item["id"]),
                    float(item["score"]),
                    item["status"],
                    json.dumps(features, ensure_ascii=False),
                    now_iso(),
                ),
            )
    return len(items)


# -------------------------------------------------------------------- status
def change_status(
    experience_id: int, status: str, reason: str
) -> Optional[dict[str, Any]]:
    """Manual promotion / retirement. Evidence gate and reason are enforced."""
    ensure_schema()
    target = (status or "").strip()
    if target not in STATUSES:
        raise ValueError(f"未知状态：{status}")
    note = (reason or "").strip()
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM experiences WHERE id = ?", (int(experience_id),)
        ).fetchone()
        if row is None:
            return None
        current = row["status"]
        if target == current:
            return _item(row)
        if target not in TRANSITIONS.get(current, set()):
            raise ValueError(f"不允许 {current} → {target}")
        if target == "active":
            evidence = json.loads(row["evidence_json"] or "[]")
            if len(evidence) < ACTIVE_MIN_EVIDENCE:
                raise ValueError(
                    f"证据不足：{len(evidence)}/{ACTIVE_MIN_EVIDENCE}，先积累独立复盘"
                )
        if not note:
            raise ValueError("状态变更必须写理由")
        conn.execute(
            "UPDATE experiences SET status = ?, status_reason = ?, status_changed_at = ?, "
            "updated_at = ?, version = version + 1 WHERE id = ?",
            (target, note, now_iso(), now_iso(), int(experience_id)),
        )
        updated = conn.execute(
            "SELECT * FROM experiences WHERE id = ?", (int(experience_id),)
        ).fetchone()
    return _item(updated)


# --------------------------------------------------------------------- reads
def list_experiences(
    status: str = "", scope: str = "", q: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    ensure_schema()
    sql = "SELECT * FROM experiences WHERE 1 = 1"
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if scope:
        sql += " AND scope = ?"
        params.append(scope)
    if q.strip():
        sql += " AND lesson LIKE ?"
        params.append(f"%{q.strip()}%")
    sql += " ORDER BY as_of DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_item(row) for row in rows]


def details(experience_id: int) -> Optional[dict[str, Any]]:
    ensure_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM experiences WHERE id = ?", (int(experience_id),)
        ).fetchone()
        if not row:
            return None
        item = _item(row)
        item["evidence"] = json.loads(row["evidence_json"] or "[]")
        item["recalls"] = [
            dict(recall_row)
            for recall_row in conn.execute(
                "SELECT run_id, score, status, injected, created_at "
                "FROM experience_recalls WHERE experience_id = ? ORDER BY id DESC LIMIT 50",
                (int(experience_id),),
            ).fetchall()
        ]
        return item


def recalls(limit: int = 50, run_id: Optional[int] = None) -> list[dict[str, Any]]:
    ensure_schema()
    sql = (
        "SELECT r.*, e.scope, e.scope_key, e.lesson, e.confidence, e.status AS card_status "
        "FROM experience_recalls r JOIN experiences e ON e.id = r.experience_id"
    )
    params: list[Any] = []
    if run_id is not None:
        sql += " WHERE r.run_id = ?"
        params.append(int(run_id))
    sql += " ORDER BY r.id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def stats() -> dict[str, Any]:
    ensure_schema()
    with connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
        by_status = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM experiences GROUP BY status"
            ).fetchall()
        }
        by_scope = {
            row["scope"]: row["n"]
            for row in conn.execute(
                "SELECT scope, COUNT(*) AS n FROM experiences GROUP BY scope"
            ).fetchall()
        }
        recall_count = conn.execute(
            "SELECT COUNT(*) FROM experience_recalls WHERE injected = 0"
        ).fetchone()[0]
        injected = conn.execute(
            "SELECT COUNT(*) FROM experience_recalls WHERE injected = 1"
        ).fetchone()[0]
    return {
        "total": int(total or 0),
        "by_status": by_status,
        "by_scope": by_scope,
        "shadow_recalls": int(recall_count or 0),
        "injected_recalls": int(injected or 0),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
