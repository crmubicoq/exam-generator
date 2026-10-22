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
