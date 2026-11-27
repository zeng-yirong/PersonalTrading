"""Versioned skill fragments, loaded by the decision nodes.

A skill is **text**, not code: it is appended to the system prompt of one role and
its `id@version` is recorded on every run that used it. That is the whole point —
six weeks from now we can ask "which wins did the desk rules produce" instead of
guessing whether a prompt change helped.

Constraints (goat.md 3.0), enforced at load time rather than by review:

* at most :data:`MAX_SKILLS` skills;
* each role has at most one **champion** (副本不接正式决策);
* body ≤ :data:`MAX_CHARS` characters — a skill that needs 3k tokens is a doc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parent
MANIFEST = SKILL_DIR / "manifest.json"
MAX_SKILLS = 5
MAX_CHARS = 2400          # ≈1.2k 汉字 token，留出余量


class SkillError(RuntimeError):
    """manifest 不合规：宁可启动失败，也不要静默跳过一个技能。"""


@dataclass(frozen=True)
class Skill:
    id: str
    version: str
    status: str
    roles: tuple[str, ...]
    summary: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.id}@{self.version}"

    def prompt_block(self) -> str:
        return f"\n\n【技能 {self.label}】\n{self.text.strip()}"


def _load(path: Path = MANIFEST) -> list[Skill]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 手改坏文件时
        raise SkillError(f"manifest.json 不是合法 JSON：{exc}") from exc
    entries = payload.get("skills") or []
    if len(entries) > MAX_SKILLS:
        raise SkillError(f"技能数量 {len(entries)} 超过上限 {MAX_SKILLS}")

    skills: list[Skill] = []
    for entry in entries:
        identifier = str(entry.get("id") or "").strip()
        version = str(entry.get("version") or "").strip()
        if not identifier or not version:
            raise SkillError("每个技能都要有 id 与 version")
        body_path = path.parent / str(entry.get("file") or f"{identifier}.md")
        if not body_path.exists():
            raise SkillError(f"技能 {identifier} 的正文不存在：{body_path.name}")
        text = body_path.read_text(encoding="utf-8")
        if len(text) > MAX_CHARS:
            raise SkillError(
                f"技能 {identifier} 正文 {len(text)} 字，超过上限 {MAX_CHARS}"
            )
        skills.append(
            Skill(
                id=identifier,
                version=version,
                status=str(entry.get("status") or "champion"),
                roles=tuple(str(role) for role in entry.get("roles") or ()),
                summary=str(entry.get("summary") or ""),
                text=text,
            )
        )

    champions: dict[str, str] = {}
    for skill in skills:
        if skill.status != "champion":
            continue
        for role in skill.roles:
            if role in champions:
                raise SkillError(
                    f"角色 {role} 有两个正式版技能：{champions[role]} 与 {skill.label}"
                )
            champions[role] = skill.label
    return skills


@lru_cache(maxsize=1)
def _cached(mtime: float) -> list[Skill]:
    return _load()


def all_skills() -> list[Skill]:
    """Every skill in the manifest (champions and challengers)."""
    stamp = MANIFEST.stat().st_mtime if MANIFEST.exists() else 0.0
    return _cached(stamp)


def active() -> list[Skill]:
    return [skill for skill in all_skills() if skill.status == "champion"]


def for_role(role: str) -> Optional[Skill]:
    for skill in active():
        if role in skill.roles:
            return skill
    return None


def prompt_suffix(role: str) -> str:
    """Text to append to a role's system prompt (empty when no skill applies)."""
    skill = for_role(role)
    return skill.prompt_block() if skill else ""


def label() -> str:
    """`equity-desk@v0.1,...` for every active skill (status endpoint)."""
    return ",".join(skill.label for skill in active())


def labels_for(roles: tuple[str, ...]) -> str:
    """只取这些角色实际加载的技能——一次推演不该记成"五个技能都用了"。"""
    labels: list[str] = []
    for role in roles:
        skill = for_role(role)
        if skill and skill.label not in labels:
            labels.append(skill.label)
    return ",".join(labels)


def describe() -> list[dict[str, object]]:
    """For status endpoints: what is loaded, how big, which roles."""
    return [
        {
            "id": skill.id,
            "version": skill.version,
            "status": skill.status,
            "roles": list(skill.roles),
            "chars": len(skill.text),
            "summary": skill.summary,
        }
        for skill in all_skills()
    ]


def record(module: str, ref_id: Optional[int] = None, role: str = "") -> str:
    """记下"这次调用用了哪一版技能"。没有技能时返回空串，不写空行。

    ``module`` 是加载点（agents / topic / screener / backtest / factor / review），
    ``role`` 缺省时按模块推断——一个模块只挂一个技能。
    """
    skill = for_role(role or module)
    if skill is None:
        return ""
    from app.db import connection, now_iso

    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO skill_calls(module, ref_id, skill_id, version, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (module, ref_id, skill.id, skill.version, now_iso()),
            )
    except Exception:  # noqa: BLE001 - 记账失败不该挡住业务
        return skill.label
    return skill.label


def usage(limit: int = 20) -> list[dict[str, object]]:
    """最近哪些模块用了哪一版技能（运维面板用）。"""
    from app.db import connection

    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT module, ref_id, skill_id, version, created_at FROM skill_calls "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:  # noqa: BLE001 - 老库还没建表
        return []
    return [dict(row) for row in rows]
