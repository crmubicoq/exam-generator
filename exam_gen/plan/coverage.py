"""★1 원고 전반 출제 — 페이지별 문항 수 배분.

출제 가능한 페이지의 내용량(글자 수)에 비례해 최대잔여법으로 나눈다.
- 문항 수가 페이지 수 이상이면 페이지마다 최소 1문항
- 페이지당 상한 = 기대값 × page_cap_factor (지엽 편중 방지)
- 추가 출제: 기존 문항이 기대보다 적은 페이지에 더 많이 배분
- 강제 할당: 출제자가 페이지별 수를 직접 지정
"""
from __future__ import annotations

import math

from ..config import criteria


def largest_remainder(total: int, weights: dict) -> dict:
    """weights 비율대로 total을 정수로 나눈다(합계 보장)."""
    keys = [k for k, w in weights.items() if w > 0]
    if total <= 0 or not keys:
        return {k: 0 for k in weights}
    s = sum(weights[k] for k in keys)
    raw = {k: total * weights[k] / s for k in keys}
    out = {k: math.floor(v) for k, v in raw.items()}
    rest = total - sum(out.values())
    for k in sorted(keys, key=lambda k: (raw[k] - out[k], weights[k]), reverse=True)[:rest]:
        out[k] += 1
    return {k: out.get(k, 0) for k in weights}


def allocate(pages: list[dict], total: int, existing: dict[int, int] | None = None,
             cap_factor: float | None = None) -> dict[int, int]:
    """반환: {페이지 g: 문항 수}"""
    cap_factor = cap_factor or criteria()["thresholds"]["page_cap_factor"]
    elig = [p for p in pages if not p["excluded"]]
    if not elig or total <= 0:
        return {}
    w = {p["g"]: max(p["chars"], 1) for p in elig}
    W = sum(w.values())

    if existing:
        # 추가 출제 후 전체가 내용량 비례가 되도록, 기대보다 부족한 만큼을 가중치로 쓴다
        grand = sum(existing.values()) + total
        need = {g: max(grand * w[g] / W - existing.get(g, 0), 0) for g in w}
        weights = need if sum(need.values()) > 0 else w
    else:
        weights = w

    floor_min = 1 if (not existing and total >= len(elig)) else 0
    base = {g: floor_min for g in w}
    alloc = largest_remainder(total - floor_min * len(w), weights)
    out = {g: base[g] + alloc.get(g, 0) for g in w}

    if existing:
        return {g: v for g, v in out.items() if v > 0}  # 추가 출제는 부족한 페이지를 채우는 것이 목적이므로 상한 없음
    expected = total / len(elig)
    cap = max(math.ceil(expected * cap_factor), floor_min + 1)
    for _ in range(len(out)):
        over = sum(max(v - cap, 0) for v in out.values())
        if not over:
            break
        for g in out:
            out[g] = min(out[g], cap)
        room = {g: weights.get(g, 0) for g, v in out.items() if v < cap}
        if not room:
            break
        add = largest_remainder(over, room)
        for g, a in add.items():
            out[g] += a
    return {g: v for g, v in out.items() if v > 0}


def coverage_stats(pages: list[dict], counts: dict[int, int]) -> dict:
    """커버리지 리포트용: 0문항 페이지, 과밀 페이지."""
    elig = [p for p in pages if not p["excluded"]]
    if not elig:
        return {"zero": [], "dense": [], "expected": {}}
    total = sum(counts.get(p["g"], 0) for p in elig)
    W = sum(max(p["chars"], 1) for p in elig)
    expected = {p["g"]: total * max(p["chars"], 1) / W for p in elig}
    cap = criteria()["thresholds"]["page_cap_factor"]
    zero = [p["g"] for p in elig if counts.get(p["g"], 0) == 0]
    dense = [g for g, e in expected.items() if counts.get(g, 0) > max(e * cap, e + 2)]
    return {"zero": zero, "dense": dense, "expected": expected}
