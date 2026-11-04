"""LLM 설정: 역할별 공급자, API 키 보관(C18), Codex 구조화 출력 스키마 변환."""
import json
import sys
import types

from exam_gen.llm import settings as S
from exam_gen.llm.client import LLMClient, strict_schema
from exam_gen.validate.schema import GEN_ITEM


class FakeKeyring(types.ModuleType):
    def __init__(self):
        super().__init__("keyring")
        self.store = {}

    def get_password(self, svc, k):
        return self.store.get((svc, k))

    def set_password(self, svc, k, v):
        self.store[(svc, k)] = v

    def delete_password(self, svc, k):
        self.store.pop((svc, k), None)


def test_api_keys_go_to_keyring_not_file(tmp_path, monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    S.save(S.LLMSettings(provider="anthropic", anthropic_api_key="sk-secret"))
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "sk-secret" not in raw and "anthropic_api_key" not in raw
    assert S.load().anthropic_api_key == "sk-secret"


def test_plaintext_key_from_old_version_is_migrated(tmp_path, monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"provider": "openai_compat", "openai_api_key": "old"}),
                                            encoding="utf-8")
    assert S.load().openai_api_key == "old"
    assert "old" not in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_role_routing():
    s = S.LLMSettings(provider="claude_agent_sdk", provider_solve="codex_cli")
    assert s.provider_for("generate") == "claude_agent_sdk"
    assert s.provider_for("solve") == "codex_cli"
    assert s.provider_for("review") == "claude_agent_sdk"
    assert s.providers_in_use() == ["claude_agent_sdk", "codex_cli"]


def test_role_routing_reaches_provider(monkeypatch):
    seen = []
    c = LLMClient(S.LLMSettings(provider="mock", provider_review="codex_cli"))
    monkeypatch.setattr(c, "_codex", lambda *a, **k: seen.append("codex") or {"results": []})
    c.structured("s", "u", {"type": "object"}, model="", task="review", context={"items": []})
    assert seen == ["codex"] and c.usage["by_provider"] == {"codex_cli": 1}


def test_strict_schema_for_codex():
    st = strict_schema({"type": "object", "properties": {"items": {"type": "array", "items": GEN_ITEM}}})
    item = st["properties"]["items"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
    ev = item["properties"]["evidence"]["items"]
    assert ev["additionalProperties"] is False


def test_short_answer_variants_merge_and_excel_scoring():
    from exam_gen.output.excel import _scoring
    from exam_gen.validate.review_llm import merge_variants
    item = {"type": "short", "answer": "보장 리모델링", "short_variants": ["보장리모델링"],
            "validation": {"G5": {"accept_variants": ["보험 리모델링", "보장 리모델링", " ", "보장 재설계"]}, "warnings": []}}
    assert merge_variants(item) == ["보험 리모델링", "보장 재설계"]  # 기존·중복·빈 값 제외
    assert any("허용 답안 자동 추가" in w for w in item["validation"]["warnings"])
    assert "보험 리모델링" in _scoring(item, {"scoring": "시스템 자동채점"})
    assert merge_variants({"type": "ox", "validation": {}}) == []


def test_last_project_is_remembered(tmp_path, monkeypatch):
    from exam_gen import store
    monkeypatch.setattr(store, "_UI_STATE", tmp_path / "ui_state.json")
    assert store.last_project() == {}
    store.set_last_project("과정A", by="cli")
    st = store.last_project()
    assert st["project"] == "과정A" and st["by"] == "cli" and st["at"]


def test_agent_sdk_records_tokens(monkeypatch):
    """Claude 구독(Agent SDK) 경로도 토큰 사용량을 남긴다 — 모델별(보조 Haiku 포함) 입력·캐시·출력 합산. 실제 호출 없음."""
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from exam_gen.llm.client import LLMClient
    from exam_gen.llm.settings import LLMSettings

    def fake_query(prompt, options):
        async def gen():
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                session_id="t", total_cost_usd=0.5, structured_output={"ok": True},
                                model_usage={"claude-opus-5-5": {"inputTokens": 100, "outputTokens": 40,
                                                                 "cacheReadInputTokens": 1000, "cacheCreationInputTokens": 10},
                                             "claude-haiku-5-5": {"inputTokens": 5, "outputTokens": 2,
                                                                  "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}})
        return gen()
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    c = LLMClient(LLMSettings(provider="claude_agent_sdk"))
    out = c.structured("sys", "user", {"type": "object"}, model="claude-opus-5-5", task="generate")
    assert out == {"ok": True}
    assert c.usage["input_tokens"] == 1115 and c.usage["output_tokens"] == 42 and c.usage["cost_usd"] == 0.5
    assert c.usage["models"]["generate"] == {"claude-opus-5-5": 1}


def test_list_litellm_models(monkeypatch):
    """LiteLLM 모델 목록: /v1/models 주소 만들기(주소가 /v1로 끝나도), 키 헤더, 중복 제거·정렬. 실제 서버 호출 없음."""
    import httpx
    from exam_gen.llm.client import LLMError, list_openai_models
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"data": [{"id": "claude-sonnet-5-5"}, {"id": "deepseek-v4"}, {"id": "claude-sonnet-5-5"}, {"x": 1}]}

    def fake_get(url, headers=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        return R()
    monkeypatch.setattr(httpx, "get", fake_get)
    assert list_openai_models("http://10.0.0.1:4000/", "k") == ["claude-sonnet-5-5", "deepseek-v4"]
    assert seen["url"] == "http://10.0.0.1:4000/v1/models" and seen["headers"] == {"Authorization": "Bearer k"}
    list_openai_models("http://10.0.0.1:4000/v1")
    assert seen["url"] == "http://10.0.0.1:4000/v1/models" and seen["headers"] == {}
    import pytest
    with pytest.raises(LLMError):
        list_openai_models("  ")
