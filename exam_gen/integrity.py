"""검증 무결성 — '현재 내용'이 G1~G6을 통과했는지를 내용 해시로 판정한다 (Codex C1).

- G1~G6을 모두 통과한 순간의 내용 해시를 validation.verified에 기록한다.
- 사람이 내용을 고치면 해시가 달라져 검증이 자동으로 무효가 된다(G4·G5 재검증 필요).
- 승인은 검증된 해시에 대해서만 가능하고, 승인 시점 해시도 함께 남긴다.
- 최종 출력은 '승인 해시 == 검증 해시 == 현재 해시'인 문항만 허용한다.
- 근거가 L1(부분 일치)인 문항은 사람이 근거를 확인했다는 표시가 있어야 승인·출력된다(Codex C3).
"""
from __future__ import annotations

import hashlib
import json

from .store import now

_FIELDS = ("type", "stem", "statements", "choices", "answer", "short_variants", "explanation")


def content_hash(item: dict) -> str:
    body = {k: item.get(k) for k in _FIELDS}
    body["choices"] = [{"text": c.get("text"), "is_true": bool(c.get("is_true"))} for c in item.get("choices") or []]
    body["evidence"] = [{"target": str(e.get("target")), "quote": e.get("quote", ""), "is_true": bool(e.get("is_true"))}
                        for e in item.get("evidence") or []]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def mark_verified(item: dict, gates=("G1", "G2", "G3", "G4", "G5", "G6")):
    v = item.setdefault("validation", {})
    v["verified"] = {"hash": content_hash(item), "gates": list(gates), "at": now()}
    v["llm_stale"] = False


def is_verified(item: dict) -> bool:
    v = item.get("validation") or {}
    rec = v.get("verified") or {}
    return (rec.get("hash") == content_hash(item) and not v.get("llm_stale") and not v.get("fails")
            and set(rec.get("gates", [])) >= {"G1", "G2", "G3", "G4", "G5", "G6"})


AI_STALE = "현재 내용에 대한 AI 검증(G4·G5) 기록이 없음 — 'AI 재검증'을 먼저 누르세요"


def has_override(item: dict) -> bool:
    """사람 책임 승인(수정 후 AI 재검증 생략, 담당자 피드백 2026-11-06): 지금 내용 그대로에 대해 검수자가 확인한 기록이 있고
    무료 검사(G1·G2·G3·G6)를 통과한 상태. 내용을 다시 고치면 해시가 달라져 무효가 된다."""
    v = item.get("validation") or {}
    o = v.get("human_override") or {}
    return bool(o) and o.get("hash") == content_hash(item) and not v.get("fails")


def needs_evidence_confirm(item: dict) -> bool:
    return any((e.get("match") or {}).get("level") == "L1" for e in item.get("evidence") or [])


def block_reasons(item: dict) -> list[str]:
    """승인 전 확인: 비어 있으면 승인 가능."""
    out = []
    v = item.get("validation") or {}
    if v.get("fails"):
        out += v["fails"]
    if (v.get("llm_stale") or (v.get("verified") or {}).get("hash") != content_hash(item)) and not has_override(item):
        out.append(AI_STALE)
    if not item.get("source_pages"):
        out.append("원고에서 확인된 근거가 없음")
    return out


def is_exportable(item: dict) -> bool:
    appr = item.get("approved") or {}
    return (item.get("status") == "approved" and (is_verified(item) or has_override(item)) and bool(item.get("source_pages"))
            and appr.get("hash") == content_hash(item)
            and (not needs_evidence_confirm(item) or appr.get("evidence_confirmed")))


def is_draftable(item: dict) -> bool:
    return (item.get("status") in ("approved", "auto_pass", "warn") and (is_verified(item) or has_override(item))
            and bool(item.get("source_pages")))
