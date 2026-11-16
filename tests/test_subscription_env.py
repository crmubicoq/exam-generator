"""구독 출제에서 PC의 ANTHROPIC_API_KEY 등을 쓰지 않는지(API 과금 방지, 2026-11-16). AI 호출 없음."""
import json
import sys

import pytest

from exam_gen import claude_account as CA
from exam_gen.llm import client as C
from exam_gen.llm.settings import LLMSettings

FAKE = "sk-ant-api03-FAKE-for-test"


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fake-token")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


def test_subscription_env_blanks_only_set_vars(fake_key):
    env = CA.subscription_env()
    assert env == {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}
    assert CA.ignored_env() == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]


def test_login_script_clears_vars():
    s = CA.login_script(__import__("pathlib").Path("claude.exe"))
    assert s.index('set "ANTHROPIC_API_KEY="') < s.index("auth login")


def test_agent_sdk_call_overrides_env(fake_key, monkeypatch):
    seen = {}

    class Result:  # ResultMessage 흉내
        total_cost_usd = 0
        model_usage = {}
        usage = {}
        structured_output = {"ok": True}
        result = json.dumps({"ok": True})
        is_error = False
        num_turns = 1

    async def fake_query(prompt, options):
        seen["env"] = options.env
        yield Result()

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude_agent_sdk, "ResultMessage", Result)
    c = C.LLMClient(LLMSettings(provider="claude_agent_sdk"))
    try:
        c._agent_sdk("sys", "user", {"type": "object"}, model="claude-opus-5-5", task="t", context={})
    except Exception:  # noqa: BLE001 — 응답 해석은 이 테스트의 관심이 아니다
        pass
    assert seen["env"].get("ANTHROPIC_API_KEY") == "" and seen["env"].get("ANTHROPIC_AUTH_TOKEN") == ""


@pytest.mark.skipif(sys.platform != "win32" or CA.claude_exe() is None, reason="Claude 프로그램이 있는 윈도우에서만")
def test_claude_program_ignores_key_when_overridden(fake_key):
    """실제 Claude 프로그램: 계정 상태 확인(AI 호출 없음)에서 API 키 출처가 잡히지 않는다."""
    r = CA._run(["auth", "status", "--json"], timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    d = json.loads(out[out.find("{"): out.rfind("}") + 1])
    assert d.get("apiKeySource") in (None, "")
    assert d.get("authMethod") != "oauth_token"
