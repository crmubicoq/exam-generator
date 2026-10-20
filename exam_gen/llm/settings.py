"""LLM 설정 (tsbookmaker core/user_settings.py 방식: 사이드바에서 입력 → data/settings.json 저장).

키는 settings.json 또는 환경변수(.env)에서 읽는다. settings.json은 git에 올리지 않는다.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from ..config import DATA_DIR

PROVIDERS = {
    "mock": "데모(모의 생성기 · LLM 없이 화면 흐름 확인용)",
    "anthropic": "Anthropic API (API 키)",
    "openai_compat": "사내 LiteLLM / OpenAI 호환",
    "claude_agent_sdk": "Claude Code 구독 로그인 (Agent SDK)",
}

MODEL_PRESETS = {
    "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
    "claude_agent_sdk": ["claude-sonnet-5-5", "claude-opus-5-5"],
    "openai_compat": ["claude-sonnet-5-5", "claude-opus-5-5", "deepseek-v4-flash"],
    "mock": ["mock"],
}


@dataclass
class LLMSettings:
    provider: str = "mock"
    model_generate: str = "claude-sonnet-5-5"
    model_solve: str = "claude-sonnet-5-5"     # G4 독립 풀이 — 다른 모델로 바꾸면 교차 검증이 된다
    model_review: str = "claude-sonnet-5-5"
    anthropic_api_key: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""
    max_tokens: int = 16000
    timeout_sec: int = 600
    chunk_size: int = 8                         # 한 번의 생성 호출에 넣는 슬롯 수 상한
    workers: int = 3                            # 페이지 병렬 처리 수
    extra: dict = field(default_factory=dict)

    def api_key(self) -> str:
        if self.provider == "anthropic":
            return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if self.provider == "openai_compat":
            return self.openai_api_key or os.environ.get("LITELLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        return ""

    def base_url(self) -> str:
        return self.openai_base_url or os.environ.get("LITELLM_BASE_URL", "")


def _path():
    return DATA_DIR / "settings.json"


def load() -> LLMSettings:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    p = _path()
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in LLMSettings.__dataclass_fields__}
        return LLMSettings(**known)
    return LLMSettings()


def save(s: LLMSettings):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
