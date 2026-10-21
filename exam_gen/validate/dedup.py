"""G6 중복 검사 — xam-local services/authoring/dedupe.py 방식(어절 자카드)에 근거 위치 비교를 더했다.

- 텍스트 유사도: 발문+보기+정답 어절 집합의 자카드. ≥ dedup_block 차단, ≥ dedup_warn 경고
- 판단 포인트(key_point) 유사도: 같은 기준으로 비교
- 같은 유형이면서 근거 줄이 같고 판단 포인트도 비슷하면 차단
"""
from __future__ import annotations

import re

from ..config import criteria

_STOP = set("다음 중 것은 것을 설명으로 옳은 옳지 않은 가장 대한 관한 있다 이다 한다 하는 및 또는 그 이 수 등 의 를 을 에 는 은 가 이 로 으로".split())


def tokens(item: dict) -> set[str]:
    parts = [item.get("stem", ""), str(item.get("answer", ""))]
    parts += [c.get("text", "") for c in item.get("choices") or []]
    parts += [s.get("text", "") for s in item.get("statements") or []]
    words = re.findall(r"[0-9A-Za-z가-힣]+", " ".join(parts))
    return {_stem(w) for w in words if w not in _STOP and len(w) > 1}


def _stem(w: str) -> str:
    return re.sub(r"(은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|이다|한다|된다|이며|하며)$", "", w) or w


def _kp(item: dict) -> set[str]:
    return {_stem(w) for w in re.findall(r"[0-9A-Za-z가-힣]+", item.get("key_point", "")) if len(w) > 1}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _ev_lines(item: dict) -> set[tuple]:
    out = set()
    for ev in item.get("evidence") or []:
        m = ev.get("match")
        if m and ev.get("is_true"):
            out |= {(m["g"], n) for n in m["lines"]}
    return out


def check(item: dict, others: list[dict]) -> tuple[list[str], list[str], dict]:
    th = criteria()["thresholds"]
    fails, warns = [], []
    tk, kp, ev = tokens(item), _kp(item), _ev_lines(item)
    best = {"key": None, "score": 0.0}
    for o in others:
        # 자기 자신 제외는 실제 키(또는 저장 전 임시 ID)가 같을 때만 — 둘 다 None이면 다른 문항이다 (Codex C6)
        same_key = item.get("key") is not None and o.get("key") == item.get("key")
        same_tmp = item.get("_tmp") is not None and o.get("_tmp") == item.get("_tmp")
        if o is item or same_key or same_tmp or o.get("status") in ("deleted", "manual"):
            continue
        s_text = jaccard(tk, o.get("_tokens") or tokens(o))
        s_kp = jaccard(kp, _kp(o))
        s = max(s_text, s_kp * 0.9)
        same_place = o.get("type") == item["type"] and ev and len(ev & _ev_lines(o)) >= max(1, len(ev) // 2)
        if same_place and s_kp >= th["dedup_warn"]:
            s = max(s, th["dedup_block"])
        if s > best["score"]:
            best = {"key": o.get("key"), "score": round(s, 3), "stem": o.get("stem", "")[:60]}
    if best["score"] >= th["dedup_block"]:
        fails.append(f"G6: 기존 문항 {best['key']}와 중복(유사도 {best['score']}) — \"{best['stem']}\"")
    elif best["score"] >= th["dedup_warn"]:
        warns.append(f"G6: 기존 문항 {best['key']}와 유사(유사도 {best['score']})")
    return fails, warns, best
