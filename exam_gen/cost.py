"""비용·시간 견적 — 지금까지 실행한 배치의 실측값(슬롯당 비용·시간)으로 계산한다.

실측이 없으면 2026-10-21 Claude Sonnet 5.5 실측을 기본값으로 쓴다:
  44슬롯 · 149호출 · 추정 $12.99 · 39분(병렬 3) → 슬롯당 약 $0.30 · 53초
구독 로그인(Claude·Codex)은 별도 청구 대신 구독 사용량에서 차감된다 — 화면에 그렇게 표시한다.
"""
from __future__ import annotations

import json
from datetime import datetime

from .config import PROJECTS_DIR

DEFAULT_USD_PER_SLOT = {"claude_agent_sdk": 0.30, "anthropic": 0.30, "openai_compat": 0.30, "codex_cli": 0.0, "mock": 0.0}
DEFAULT_SEC_PER_SLOT = {"claude_agent_sdk": 53, "anthropic": 53, "openai_compat": 53, "codex_cli": 45, "mock": 0.5}
SUBSCRIPTION = ("claude_agent_sdk", "codex_cli")


def history() -> dict[str, dict]:
    """공급자별 실측: {provider: {"slots", "usd", "sec", "tokens"}} — 한 공급자만 쓴 배치만 집계."""
    out: dict[str, dict] = {}
    if not PROJECTS_DIR.exists():
        return out
    for runs in PROJECTS_DIR.glob("*/runs"):
        for f in runs.glob("batch*_progress.json"):
            try:
                pr = json.loads(f.read_text(encoding="utf-8"))
                by = (pr.get("usage") or {}).get("by_provider") or {}
                if len(by) != 1 or pr.get("status") not in ("done", "partial"):
                    continue
                prov = next(iter(by))
                slots = pr.get("slots_total") or 0
                sec = (datetime.fromisoformat(pr["updated"]) - datetime.fromisoformat(pr["started"])).total_seconds()
                if slots < 5 or sec <= 0:
                    continue
                h = out.setdefault(prov, {"slots": 0, "usd": 0.0, "sec": 0.0, "tokens": 0, "batches": 0})
                h["slots"] += slots
                h["usd"] += pr["usage"].get("cost_usd", 0.0)
                h["sec"] += sec
                h["tokens"] += pr["usage"].get("input_tokens", 0)
                h["batches"] += 1
            except (OSError, ValueError, KeyError):
                continue
    return out


def estimate(settings, n_slots: int) -> dict:
    """반환: {"usd", "minutes", "basis", "subscription"}"""
    hist = history()
    roles = [settings.provider_for(r) for r in ("generate", "solve", "review")]
    usd = 0.0
    sec_per_slot = 0.0
    basis = []
    for prov in sorted(set(roles)):
        share = roles.count(prov) / 3  # 역할 비중(생성·풀이·검수)만큼 비용을 나눠 진다고 근사
        h = hist.get(prov)
        if h and h["slots"]:
            per_usd, per_sec = h["usd"] / h["slots"], h["sec"] / h["slots"]
            basis.append(f"{prov} 실측 {h['batches']}배치·{h['slots']}슬롯")
        else:
            per_usd, per_sec = DEFAULT_USD_PER_SLOT.get(prov, 0.3), DEFAULT_SEC_PER_SLOT.get(prov, 53)
            basis.append(f"{prov} 기본값")
        usd += per_usd * n_slots * share
        sec_per_slot = max(sec_per_slot, per_sec)
    workers = max(1, getattr(settings, "workers", 3))
    minutes = sec_per_slot * n_slots * (3 / workers) / 60  # 실측은 병렬 3 기준
    return {"usd": round(usd, 1), "minutes": round(minutes), "basis": " · ".join(basis),
            "subscription": all(p in SUBSCRIPTION for p in set(roles))}
