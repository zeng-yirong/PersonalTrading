"""技能层：注册表约束、注入、版本记录，以及 20 条决策陷阱回归。"""

from __future__ import annotations

import json

import pytest

from app.agents import commentary, guards
from app.config import settings
from app.db import init_db
from app.skills import MAX_CHARS, MAX_SKILLS, SkillError, _load, active, for_role, label, prompt_suffix


# ------------------------------------------------------------------ registry
def test_manifest_stays_within_the_limits():
    skills = active()
    assert 1 <= len(skills) <= MAX_SKILLS
    for skill in skills:
        assert len(skill.text) <= MAX_CHARS
        assert skill.version.startswith("v")
    # 一个角色最多一个正式版
    roles = [role for skill in skills for role in skill.roles]
    assert len(roles) == len(set(roles))


def test_all_five_core_modules_have_a_skill():
    """goat 3.0 要求 5 个核心功能各一个 skill。"""
    loaded = {(skill.id, role) for skill in active() for role in skill.roles}
    expected = {
        ("equity-desk", "trader"),
        ("equity-desk", "risk_manager"),
        ("theme-desk", "topic"),
        ("screen-dsl", "screener"),
        ("strategy-review", "backtest"),
        ("strategy-review", "factor"),
        ("review-desk", "review"),
    }
    assert expected <= loaded


def test_skill_files_all_exist_and_are_not_empty():
    from app.skills import SKILL_DIR

    for skill in active():
        assert (SKILL_DIR / f"{skill.id}.md").exists()
        assert len(skill.text.strip()) > 200


def test_skill_use_is_recorded_per_module(app_db):
    from app.skills import record, usage

    label = record("topic", 7)
    assert label == "theme-desk@v0.1"
    assert record("no-such-module") == ""      # 没有对应技能就不写空行

    rows = usage(5)
    assert rows and rows[0]["module"] == "topic" and rows[0]["skill_id"] == "theme-desk"
    assert rows[0]["ref_id"] == 7


def test_skill_is_injected_only_into_the_roles_it_owns():
    assert "equity-desk" in prompt_suffix("trader")
    assert "equity-desk" in prompt_suffix("risk_manager")
    assert prompt_suffix("market") == ""          # 分析层保留原有证据规则
    assert "equity-desk@v0.1" in label()
    assert for_role("market") is None


def test_too_many_skills_is_a_load_error(tmp_path):
    manifest = tmp_path / "manifest.json"
    skills = [
        {"id": f"s{index}", "version": "v0.1", "roles": [f"role{index}"], "file": "body.md"}
        for index in range(MAX_SKILLS + 1)
    ]
    manifest.write_text(json.dumps({"skills": skills}), encoding="utf-8")
    (tmp_path / "body.md").write_text("# ok", encoding="utf-8")
    with pytest.raises(SkillError, match="上限"):
        _load(manifest)


def test_oversized_skill_body_is_rejected(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"skills": [{"id": "fat", "version": "v0.1", "roles": ["trader"], "file": "fat.md"}]}),
        encoding="utf-8",
    )
    (tmp_path / "fat.md").write_text("字" * (MAX_CHARS + 1), encoding="utf-8")
    with pytest.raises(SkillError, match="超过上限"):
        _load(manifest)


def test_two_champions_for_one_role_is_rejected(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "skills": [
                    {"id": "a", "version": "v0.1", "roles": ["trader"], "file": "body.md"},
                    {"id": "b", "version": "v0.1", "roles": ["trader"], "file": "body.md"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "body.md").write_text("# ok", encoding="utf-8")
    with pytest.raises(SkillError, match="两个正式版"):
        _load(manifest)


# ------------------------------------------------------------- skill_version
@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()


def test_run_records_which_skill_version_made_it(app_db):
    from app.agents import store as agent_store

    run_id = agent_store.create_run("600519", "贵州茅台", "fake", 1, {"x": 1}, "equity-desk@v0.1")
    run = agent_store.get_run(run_id)
    assert run["skill_version"] == "equity-desk@v0.1"

    plain = agent_store.create_run("000001", "平安银行", "fake", 1, {})
    assert agent_store.get_run(plain)["skill_version"] is None


# ------------------------------------------------------- 20 条决策陷阱回归
GOOD_PROPOSAL = {
    "action": "买入",
    "position_pct": 40,
    "target_price": 1600,
    "stop_price": 1350,
}
GOOD_VERDICT = {"decision": "调整后批准", "final_position_pct": 30}


def _proposal(**overrides):
    return {**GOOD_PROPOSAL, **overrides}


@pytest.mark.parametrize(
    ("proposal", "last_close", "expected"),
    [
        # 价格
        (_proposal(target_price=1400), 1450.0, "目标价"),
        (_proposal(stop_price=1500), 1450.0, "止损价"),
        (_proposal(target_price=1500, stop_price=1500), 1450.0, "目标价不高于止损价"),
        (_proposal(target_price=1400, stop_price=1300), 1450.0, "目标价"),
        (_proposal(target_price="数据不足", stop_price="数据不足"), 1450.0, None),
        # 仓位
        (_proposal(position_pct=140), 1450.0, "超过 100%"),
        (_proposal(position_pct=-5), 1450.0, "为负"),
        (_proposal(position_pct=None), 1450.0, "仓位缺失"),
        (_proposal(action="买入", position_pct=0), 1450.0, "买入/加仓"),
        (_proposal(action="观望", position_pct=60), 1450.0, "观望却给了"),
        # 正常
        (GOOD_PROPOSAL, 1450.0, None),
        (_proposal(action="观望", position_pct=0), 1450.0, None),
        ({}, 1450.0, "交易员草案缺失"),
        # 拍卖规则：涨停附近也不该给出高于现价的目标价之外的机械错误
        (_proposal(action="减仓", position_pct=10), 1450.0, None),
        (_proposal(action="卖出", position_pct=100), 1450.0, None),
    ],
)
def test_proposal_traps(proposal, last_close, expected):
    issues = "；".join(guards.check_proposal(proposal, last_close))
    if expected is None:
        assert issues == "", issues
    else:
        assert expected in issues


@pytest.mark.parametrize(
    ("verdict", "proposal", "expected"),
    [
        ({"decision": "否决", "final_position_pct": 20}, GOOD_PROPOSAL, "裁定否决"),
        ({"decision": "批准", "final_position_pct": 0}, GOOD_PROPOSAL, "裁定批准"),
        ({"decision": "批准", "final_position_pct": 150}, GOOD_PROPOSAL, "超过 100%"),
        ({"decision": "批准", "final_position_pct": -10}, GOOD_PROPOSAL, "为负"),
        ({"decision": "调整后批准", "final_position_pct": 70}, GOOD_PROPOSAL, "风控不应加仓"),
        ({"decision": "调整后批准", "final_position_pct": 45}, GOOD_PROPOSAL, None),   # 容差内
        ({"decision": "调整后批准"}, GOOD_PROPOSAL, "最终仓位缺失"),
        ({}, GOOD_PROPOSAL, "风控裁定缺失"),
        (GOOD_VERDICT, GOOD_PROPOSAL, None),
    ],
)
def test_verdict_traps(verdict, proposal, expected):
    issues = "；".join(guards.check_verdict(verdict, proposal))
    if expected is None:
        assert issues == "", issues
    else:
        assert expected in issues

# ------------------------------------------------------- 硬约束：sanitize
def test_sanitize_fixes_contradictions_before_persisting():
    proposal = {"action": "买入", "position_pct": 120, "target_price": 1400, "stop_price": 1500}
    verdict = {"decision": "否决", "final_position_pct": 30}

    fixed, fixed_verdict, fixes = guards.sanitize(proposal, verdict, 1450.0)

    assert guards.check_proposal(fixed, 1450.0) == []
    assert guards.check_verdict(fixed_verdict, fixed) == []
    assert fixed["position_pct"] == 100.0
    assert fixed["target_price"] == "数据不足"
    assert fixed_verdict["final_position_pct"] == 0
    assert len(fixes) >= 4


@pytest.mark.parametrize(
    ("proposal", "verdict", "close"),
    [
        ({"action": "买入", "position_pct": 0}, {"decision": "批准", "final_position_pct": 0}, 10.0),
        ({"action": "观望", "position_pct": 60}, {"decision": "调整后批准", "final_position_pct": 90}, 10.0),
        ({"action": "持有", "position_pct": -3}, {"decision": "批准", "final_position_pct": 150}, 10.0),
        ({"action": "加仓", "position_pct": 20, "stop_price": 12}, {"final_position_pct": 20}, 10.0),
        ({"action": "买入", "position_pct": 30, "target_price": 9, "stop_price": 8}, {"decision": "否决", "final_position_pct": 0}, 10.0),
    ],
)
def test_sanitize_always_leaves_zero_issues(proposal, verdict, close):
    fixed, fixed_verdict, _ = guards.sanitize(proposal, verdict, close)
    assert guards.check_proposal(fixed, close) == []
    assert guards.check_verdict(fixed_verdict, fixed) == []


# ------------------------------------------------------- 复核（rubric + 模型）
class FakeUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1

    def as_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "calls": self.calls,
        }


class FakeCommentLLM:
    model = "fake"

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.systems: list[str] = []
        self.usage = FakeUsage()

    def available(self) -> tuple[bool, str]:
        return True, ""

    async def complete_json(self, system: str, user: str, **_: object) -> dict:
        self.systems.append(system)
        self.usage.add(100, 50)
        return self.reply


def test_backtest_rubric_is_deterministic():
    weak = commentary.rubric_backtest(
        {"periods": 12, "excess_cagr_pct": -6.4, "max_drawdown_pct": -46.1, "win_rate_pct": 25}
    )
    assert [item["passed"] for item in weak] == [True, False, False, False]
    assert commentary._verdict(weak) == "不可用"

    strong = commentary.rubric_backtest(
        {"periods": 20, "excess_cagr_pct": 3.0, "max_drawdown_pct": -12.0, "win_rate_pct": 60}
    )
    assert all(item["passed"] for item in strong)
    assert commentary._verdict(strong) == "观察池"


def test_backtest_commentary_uses_the_skill_and_records_it(app_db, monkeypatch):
    fake = FakeCommentLLM(
        {"verdict": "不可用", "why": "超额 -6.4%、回撤 46%，四条门槛只过一条", "risks": ["样本内调参"], "next": "换区间复核"}
    )
    monkeypatch.setattr(commentary, "ChatClient", lambda model="": fake)

    import asyncio

    out = asyncio.run(
        commentary.comment_backtest(
            {"periods": 12, "excess_cagr_pct": -6.4, "max_drawdown_pct": -46.1, "win_rate_pct": 25},
            "低估值组合",
        )
    )

    assert out["skill"] == "strategy-review@v0.1"
    assert out["verdict"] == "不可用"
    assert "strategy-review" in fake.systems[0]
    assert "超额 -6.4%" in out["text"]
    assert out["usage"]["calls"] == 1

    from app.skills import usage

    assert any(row["module"] == "backtest" for row in usage(5))


def test_review_commentary_flags_a_low_hit_rate(app_db, monkeypatch):
    fake = FakeCommentLLM({"verdict": "观察池", "why": "样本 11 条不足 20", "risks": [], "next": "等样本"})
    monkeypatch.setattr(commentary, "ChatClient", lambda model="": fake)

    import asyncio

    out = asyncio.run(
        commentary.comment_review(
            {"accuracy": {"prediction": {"hits": 4, "total": 11, "rate": 36.4}}, "reviews": []}
        )
    )
    assert out["skill"] == "review-desk@v0.1"
    assert [item["passed"] for item in out["rubric"]] == [False, False]
    assert "0/2" in out["summary"]
