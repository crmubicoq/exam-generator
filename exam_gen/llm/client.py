"""LLM 공급자 추상화. 모든 호출은 structured(system, user, schema) → dict 하나로 통일한다.

- anthropic        : Messages API + 강제 tool_use로 JSON 스키마 출력
- openai_compat    : 사내 LiteLLM 등 /v1/chat/completions (tsbookmaker core/llm.py 방식: JSON 추출 + 복구 재호출)
- claude_agent_sdk : Claude 구독 로그인(OAuth) — Claude Agent SDK (xam-local provider.py 방식: output_format=json_schema)
- codex_cli        : Codex 구독 로그인(OAuth, ChatGPT 계정) — `codex exec --output-schema`
- mock             : LLM 없이 원고 문장으로 만든 데모 문항 (generate/mock.py)

공급자는 역할(task: generate / solve / review)마다 settings.provider_for(role)로 고른다.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .settings import ROLES, LLMSettings


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.s = settings
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0,
                      "by_provider": {}}

    def structured(self, system: str, user: str, schema: dict, *, model: str, task: str,
                   context: dict | None = None, provider: str | None = None) -> dict:
        t0 = time.time()
        provider = provider or self.s.provider_for(task if task in ROLES else "generate")
        fn = {"anthropic": self._anthropic, "openai_compat": self._openai, "claude_agent_sdk": self._agent_sdk,
              "codex_cli": self._codex, "mock": self._mock}.get(provider)
        if fn is None:
            raise LLMError(f"알 수 없는 공급자: {provider}")
        if task in ROLES:
            model = self.s.model_for(task)
        if provider == "codex_cli" and model.lower().startswith("claude"):
            model = ""  # 다른 공급자용 모델명이 남아 있으면 Codex CLI 기본 모델을 쓴다
        if provider in ("claude_agent_sdk", "anthropic") and model and not model.lower().startswith("claude"):
            model = "claude-sonnet-5-5"
        bp = self.usage["by_provider"]
        bp[provider] = bp.get(provider, 0) + 1
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
        key = self.s.api_key("anthropic")
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
        key = self.s.api_key("openai_compat")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
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
    # ---------- Codex CLI (ChatGPT 계정 OAuth) ----------
    def _codex(self, system, user, schema, *, model, task, context):
        exe = codex_executable()
        if not exe:
            raise LLMError("Codex CLI를 찾을 수 없다. `npm install -g @openai/codex` 후 `codex login`으로 로그인하세요.")
        with tempfile.TemporaryDirectory(prefix="examgen_codex_") as td:
            # 빈 작업 폴더 + 읽기 전용 샌드박스: 원고·프로젝트 파일에 접근하지 못하고 프롬프트 내용만으로 답하게 한다
            sp, op = Path(td) / "schema.json", Path(td) / "out.json"
            sp.write_text(json.dumps(strict_schema(schema), ensure_ascii=False), encoding="utf-8")
            prompt = (system + "\n\n# 작업 지시\n파일을 읽거나 명령을 실행하지 말고, 아래 내용만으로 지정된 JSON 스키마에 맞게 답한다.\n\n"
                      + user)
            cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--color", "never",
                   "--output-schema", str(sp), "-o", str(op), "-C", td]
            if model and model not in ("mock", "default"):
                cmd += ["-m", model]
            cmd.append("-")
            try:
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=self.s.timeout_sec)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Codex 응답 시간 초과({self.s.timeout_sec}초)") from e
            log = (r.stdout or "") + (r.stderr or "")
            m = re.search(r"tokens used\s*\n?\s*([\d,]+)", log)
            if m:
                self.usage["input_tokens"] += int(m.group(1).replace(",", ""))
            if r.returncode != 0 or not op.exists():
                if re.search(r"log ?in|auth|401|unauthori", log, re.I):
                    raise LLMError("Codex 로그인이 필요하다. 터미널에서 `codex login`을 실행하세요.")
                raise RuntimeError(f"Codex 실행 실패(코드 {r.returncode}): {log[-400:]}")
            return _extract_json(op.read_text(encoding="utf-8"))

    def _mock(self, system, user, schema, *, model, task, context):
        from ..generate import mock
        return mock.respond(task, context)


def codex_executable() -> str | None:
    return shutil.which("codex") or shutil.which("codex.cmd")


def strict_schema(schema: dict) -> dict:
    """OpenAI 구조화 출력(strict) 규칙: 모든 객체에 additionalProperties=false, 모든 속성을 required로."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(s)
    return s


def login_status(provider: str, settings: LLMSettings | None = None) -> tuple[bool, str]:
    """구독 로그인(OAuth) 상태 확인. Codex는 CLI 상태 명령, Claude는 최소 호출로 확인한다."""
    if provider == "codex_cli":
        exe = codex_executable()
        if not exe:
            return False, "Codex CLI 미설치 — `npm install -g @openai/codex`"
        r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        msg = ((r.stdout or "") + (r.stderr or "")).strip()
        ok = r.returncode == 0 and "logged in" in msg.lower() and "not logged" not in msg.lower()
        return ok, msg or "상태를 확인하지 못함"
    if provider == "claude_agent_sdk":
        try:
            c = LLMClient(settings or LLMSettings(provider=provider))
            c.structured("JSON으로만 답한다.", "ok에 true를 넣어라.",
                         {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                         model=(settings.model_generate if settings else "claude-sonnet-5-5"), task="ping",
                         provider=provider)
            return True, "Claude 로그인 확인됨(Agent SDK 호출 성공)"
        except Exception as e:  # noqa: BLE001
            return False, f"Claude 호출 실패 — Claude Code에서 로그인(/login)했는지 확인: {e}"
    return True, "구독 로그인 방식이 아님"


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("JSON 없음")
    return json.loads(text[i:j + 1])
