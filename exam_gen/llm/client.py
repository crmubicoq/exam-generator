"""LLM 공급자 추상화. 모든 호출은 structured(system, user, schema) → dict 하나로 통일한다.

- anthropic        : Messages API + 강제 tool_use로 JSON 스키마 출력
- openai_compat    : 사내 LiteLLM 등 /v1/chat/completions (tsbookmaker core/llm.py 방식: JSON 추출 + 복구 재호출)
- claude_agent_sdk : Claude Code 구독 로그인 (xam-local provider.py 방식: output_format=json_schema)
- mock             : LLM 없이 원고 문장으로 만든 데모 문항 (generate/mock.py)
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from .settings import LLMSettings


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.s = settings
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0}

    def structured(self, system: str, user: str, schema: dict, *, model: str, task: str,
                   context: dict | None = None) -> dict:
        t0 = time.time()
        fn = {"anthropic": self._anthropic, "openai_compat": self._openai,
              "claude_agent_sdk": self._agent_sdk, "mock": self._mock}.get(self.s.provider)
        if fn is None:
            raise LLMError(f"알 수 없는 공급자: {self.s.provider}")
        last = None
        for attempt in range(3):
            try:
                out = fn(system, user, schema, model=model, task=task, context=context or {})
                self.usage["calls"] += 1
                self.usage["seconds"] += time.time() - t0
                return out
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001 — 네트워크·일시 오류는 재시도
                last = e
                time.sleep(2 * (attempt + 1))
        raise LLMError(f"LLM 호출 실패({task}): {last}")

    # ---------- Anthropic ----------
    def _anthropic(self, system, user, schema, *, model, task, context):
        import anthropic
        key = self.s.api_key()
        if not key:
            raise LLMError("Anthropic API 키가 없습니다. 사이드바에서 입력하거나 ANTHROPIC_API_KEY를 설정하세요.")
        client = anthropic.Anthropic(api_key=key, timeout=self.s.timeout_sec)
        msg = client.messages.create(
            model=model, max_tokens=self.s.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": "submit", "description": "결과를 제출한다", "input_schema": schema}],
            tool_choice={"type": "tool", "name": "submit"},
            messages=[{"role": "user", "content": user}],
        )
        u = msg.usage
        self.usage["input_tokens"] += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
        self.usage["output_tokens"] += u.output_tokens or 0
        if msg.stop_reason == "max_tokens":
            raise LLMError("출력이 max_tokens에서 잘렸습니다. 슬롯 묶음 크기(chunk_size)를 줄이세요.")
        for b in msg.content:
            if b.type == "tool_use":
                return b.input
        raise LLMError("구조화 출력(tool_use)이 없습니다.")

    # ---------- OpenAI 호환 (LiteLLM) ----------
    def _openai(self, system, user, schema, *, model, task, context):
        import httpx
        base = self.s.base_url().rstrip("/")
        if not base:
            raise LLMError("LiteLLM 주소가 없습니다. 사이드바에서 입력하세요.")
        url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
        sys2 = system + "\n\n# 출력 형식\n반드시 아래 JSON 스키마를 만족하는 JSON 객체 하나만 출력한다.\n" + \
            json.dumps(schema, ensure_ascii=False)
        body = {"model": model, "max_tokens": self.s.max_tokens, "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": sys2}, {"role": "user", "content": user}]}
        headers = {"Authorization": f"Bearer {self.s.api_key()}"} if self.s.api_key() else {}
        r = httpx.post(url, json=body, headers=headers, timeout=self.s.timeout_sec)
        if r.status_code >= 400:
            raise LLMError(f"LiteLLM 오류 {r.status_code}: {r.text[:300]}")
        data = r.json()
        u = data.get("usage") or {}
        self.usage["input_tokens"] += u.get("prompt_tokens", 0)
        self.usage["output_tokens"] += u.get("completion_tokens", 0)
        text = data["choices"][0]["message"]["content"] or ""
        try:
            return _extract_json(text)
        except ValueError:
            fix = {"model": model, "max_tokens": self.s.max_tokens, "temperature": 0,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": "다음 텍스트를 유효한 JSON 객체로만 고쳐서 출력하라."},
                                {"role": "user", "content": text}]}
            r2 = httpx.post(url, json=fix, headers=headers, timeout=self.s.timeout_sec)
            return _extract_json(r2.json()["choices"][0]["message"]["content"] or "")

    # ---------- Claude Agent SDK ----------
    def _agent_sdk(self, system, user, schema, *, model, task, context):
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        opts = ClaudeAgentOptions(
            system_prompt=system, model=model, max_turns=4, setting_sources=[],
            allowed_tools=[], output_format={"type": "json_schema", "schema": schema},
            disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebFetch", "WebSearch",
                              "Task", "NotebookEdit", "TodoWrite"],
        )

        async def run():
            result = None
            async for m in query(prompt=user, options=opts):
                if isinstance(m, ResultMessage):
                    result = m
            return result

        res = asyncio.run(run())
        if res is None:
            raise LLMError("Agent SDK 응답이 없습니다.")
        if res.total_cost_usd:
            self.usage["cost_usd"] += res.total_cost_usd
        if res.is_error:
            raise LLMError(f"Agent SDK 오류: {res.result or res.errors}")
        if res.structured_output is not None:
            return res.structured_output
        return _extract_json(res.result or "")

    # ---------- 데모 ----------
    def _mock(self, system, user, schema, *, model, task, context):
        from ..generate import mock
        return mock.respond(task, context)


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("JSON 없음")
    return json.loads(text[i:j + 1])
