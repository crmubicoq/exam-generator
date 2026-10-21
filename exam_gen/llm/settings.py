"""LLM 설정 — 사이드바에서 입력, data/settings.json에 저장 (tsbookmaker user_settings 방식).

- 공급자는 역할별(생성 / G4 독립 풀이 / G5 검수)로 고를 수 있다. 비워 두면 생성 공급자를 따른다.
  예) 생성은 Claude, 독립 풀이는 Codex → 다른 회사 모델이 정답을 교차 검증한다.
- 구독 로그인(OAuth) 방식 두 가지: Claude Agent SDK(Claude Code 로그인), Codex CLI(ChatGPT 로그인).
  로그인 정보는 각 CLI가 관리하며 이 툴은 토큰을 저장하지 않는다.
- API 키는 settings.json에 쓰지 않고 Windows 자격 증명 저장소(keyring)에 보관한다 (Codex C18).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from ..config import DATA_DIR

PROVIDERS = {
    "mock": "데모(모의 생성기 · LLM 없이 화면 흐름 확인용)",
    "claude_agent_sdk": "Claude 구독 로그인 (OAuth · Claude Agent SDK)",
    "codex_cli": "Codex 구독 로그인 (OAuth · Codex CLI, ChatGPT 계정)",
    "anthropic": "Anthropic API (API 키)",
    "openai_compat": "사내 LiteLLM / OpenAI 호환 (API 키)",
}
OAUTH_PROVIDERS = ("claude_agent_sdk", "codex_cli")
ROLES = {"generate": "생성", "solve": "G4 독립 풀이", "review": "G5 검수"}

MODEL_PRESETS = {
    "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
    "claude_agent_sdk": ["claude-sonnet-5-5", "claude-opus-5-5"],
    "codex_cli": [""],  # 비워 두면 Codex CLI 설정의 기본 모델
    "openai_compat": ["claude-sonnet-5-5", "claude-opus-5-5", "deepseek-v4-flash"],
    "mock": ["mock"],
}
_SECRET_FIELDS = ("anthropic_api_key", "openai_api_key")
_KEYRING_SERVICE = "exam-generator"


@dataclass
class LLMSettings:
    provider: str = "mock"                      # 생성 공급자
    provider_solve: str = ""                    # G4 공급자("" = 생성과 같음)
    provider_review: str = ""                   # G5 공급자("" = 생성과 같음)
    model_generate: str = "claude-sonnet-5-5"
    model_solve: str = "claude-sonnet-5-5"
    model_review: str = "claude-sonnet-5-5"
    anthropic_api_key: str = ""                 # 저장 시 keyring으로 이동
    openai_base_url: str = ""
    openai_api_key: str = ""                    # 저장 시 keyring으로 이동
    max_tokens: int = 16000
    timeout_sec: int = 600
    chunk_size: int = 8                         # 한 번의 생성 호출에 넣는 슬롯 수 상한
    workers: int = 3                            # 페이지 병렬 처리 수
    extra: dict = field(default_factory=dict)

    def provider_for(self, role: str) -> str:
        return {"solve": self.provider_solve, "review": self.provider_review}.get(role) or self.provider

    def model_for(self, role: str) -> str:
        return {"generate": self.model_generate, "solve": self.model_solve, "review": self.model_review}.get(
            role, self.model_generate)

    def providers_in_use(self) -> list[str]:
        return sorted({self.provider_for(r) for r in ROLES})

    def api_key(self, provider: str | None = None) -> str:
        provider = provider or self.provider
        if provider == "anthropic":
            return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if provider == "openai_compat":
            return self.openai_api_key or os.environ.get("LITELLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        return ""

    def base_url(self) -> str:
        return self.openai_base_url or os.environ.get("LITELLM_BASE_URL", "")


def _path():
    return DATA_DIR / "settings.json"


def _keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def load() -> LLMSettings:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    p = _path()
    s = LLMSettings()
    raw = {}
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        s = LLMSettings(**{k: v for k, v in raw.items() if k in LLMSettings.__dataclass_fields__})
    kr = _keyring()
    for f in _SECRET_FIELDS:
        if kr and not getattr(s, f):
            try:
                setattr(s, f, kr.get_password(_KEYRING_SERVICE, f) or "")
            except Exception:  # noqa: BLE001 — 자격 증명 저장소 접근 실패 시 환경변수로 대체
                pass
    if any(raw.get(f) for f in _SECRET_FIELDS):
        save(s)  # 예전 버전이 settings.json에 평문으로 남긴 키를 자격 증명 저장소로 옮긴다
    return s


def save(s: LLMSettings):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(s)
    kr = _keyring()
    for f in _SECRET_FIELDS:
        val = data.pop(f, "")
        if kr is None:
            if val:
                raise RuntimeError("keyring 패키지가 없어 API 키를 안전하게 저장할 수 없다. 환경변수(.env)를 쓰세요.")
            continue
        try:
            if val:
                kr.set_password(_KEYRING_SERVICE, f, val)
            elif kr.get_password(_KEYRING_SERVICE, f):
                kr.delete_password(_KEYRING_SERVICE, f)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"자격 증명 저장소에 키를 저장하지 못했다: {e}") from e
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
