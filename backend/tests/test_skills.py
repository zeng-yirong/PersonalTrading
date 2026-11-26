"""技能层：注册表约束、注入、版本记录，以及 20 条决策陷阱回归。"""

from __future__ import annotations

import json

import pytest

from app.agents import guards
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
