"""비용·시간 견적 — 지금까지 실행한 배치의 실측값(슬롯당 비용·시간)으로 계산한다.

실측이 없으면(새 설치) 개발 시험 출제 실측을 기본값으로 쓴다:
  비용 — 2026-10-21 Claude 44슬롯 · 추정 $12.99 → 슬롯당 약 $0.30
  시간 — 2026-10-27 Opus 5.5 새 규칙 32슬롯 · 123호출 · 11.9분(병렬 3) → 슬롯당 약 23초
         Codex 32슬롯 · 24.0분 → 슬롯당 약 45초
구독 로그인(Claude·Codex)은 별도 청구 대신 구독 사용량에서 차감된다 — 화면에 그렇게 표시한다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .config import PROJECTS_DIR

DEFAULT_USD_PER_SLOT = {"claude_agent_sdk": 0.30, "anthropic": 0.30, "openai_compat": 0.30, "codex_cli": 0.0, "mock": 0.0}
DEFAULT_SEC_PER_SLOT = {"claude_agent_sdk": 23, "anthropic": 23, "openai_compat": 53, "codex_cli": 45, "mock": 0.5}
SUBSCRIPTION = ("claude_agent_sdk", "codex_cli")
# 상·최상 슬롯은 사례형·재출제가 많아 더 오래 걸린다: 2026-10-28 상·최상만 20슬롯 20.2분(병렬 3) → 슬롯당 약 60초,
# 보통 혼합(상·최상 20%)은 약 23초. 난이도 보정으로 늘어난 상·최상 슬롯 하나당 약 37초를 더한다
HARD_EXTRA_SEC = 37


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
                # 상·최상이 보통(20%)보다 많은 배치는 그만큼 걸린 시간을 빼서 보통 혼합 기준으로 맞춘다(난이도 보정 시간은 따로 더함)
                hard = _hard_slots(runs.parent, f.name)
                if hard is not None:
                    sec = max(sec * 0.3, sec - max(0.0, hard - 0.2 * slots) * HARD_EXTRA_SEC)
                h = out.setdefault(prov, {"slots": 0, "usd": 0.0, "sec": 0.0, "tokens": 0, "batches": 0})
                h["slots"] += slots
                h["usd"] += pr["usage"].get("cost_usd", 0.0)
                h["sec"] += sec
                h["tokens"] += pr["usage"].get("input_tokens", 0)
                h["batches"] += 1
            except (OSError, ValueError, KeyError):
                continue
    return out


def _hard_slots(pdir, progress_name: str) -> int | None:
    """배치의 상·최상 슬롯 수(문항 DB의 batches 표). 읽지 못하면 None."""
    try:
        n = int(progress_name.split("_")[0].removeprefix("batch"))
        with sqlite3.connect(pdir / "items.sqlite") as c:
            row = c.execute("SELECT slots FROM batches WHERE batch=?", (n,)).fetchone()
        return sum(1 for sl in json.loads(row[0]) if sl.get("difficulty") in ("최상", "상")) if row else None
    except (OSError, ValueError, sqlite3.Error, TypeError):
        return None


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


def hard_extra_minutes(extra_hard_slots: int, settings) -> int:
    """난이도 보정으로 기본 비율보다 늘어난 상·최상 슬롯 수만큼 더 걸리는 시간(분)."""
    workers = max(1, getattr(settings, "workers", 3))
    return round(max(0, extra_hard_slots) * HARD_EXTRA_SEC * (3 / workers) / 60)
