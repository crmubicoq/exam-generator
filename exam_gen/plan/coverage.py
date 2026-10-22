"""★1 원고 전반 출제 — 페이지별 문항 수 배분.

출제 가능한 페이지의 내용량(글자 수)에 비례해 최대잔여법으로 나눈다.
- 최초 출제는 차시별 합계를 먼저 내용량 비례로 정하고, 차시 안에서 페이지에 나눈다(실제 원고 시험에서
  문항이 페이지보다 적을 때 글자가 많은 페이지가 몰린 3절에 쏠리고 4·5절이 부족했다 — Codex 검수 2026-10-22)
- 문항이 페이지보다 적으면 페이지 순서를 따라 내용량 간격으로 고르게 뽑는다(한 구간에 몰리지 않게)
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
    elig = [p for p in pages if not p["excluded"]]
    if not elig or total <= 0:
        return {}
    chasis = sorted({p.get("chasi") for p in elig}, key=lambda c: (c is None, c))
    if existing or len(chasis) <= 1:
        return _allocate_pages(elig, total, existing, cap_factor)
    per = largest_remainder(total, {c: sum(max(p["chars"], 1) for p in elig if p.get("chasi") == c) for c in chasis})
    out: dict[int, int] = {}
    for c in chasis:
        out.update(_allocate_pages([p for p in elig if p.get("chasi") == c], per[c], None, cap_factor))
    return out


def _spread(elig: list[dict], total: int) -> dict[int, int]:
    """문항 < 페이지: 페이지 순서대로 누적 글자 수를 total등분한 지점의 페이지를 고른다(계통 표집)."""
    elig = sorted(elig, key=lambda p: p["g"])
    W = sum(max(p["chars"], 1) for p in elig)
    marks = [(k + 0.5) * W / total for k in range(total)]
    out: dict[int, int] = {}
    acc, i = 0.0, 0
    for p in elig:
        acc += max(p["chars"], 1)
        while i < len(marks) and marks[i] <= acc:
            out[p["g"]] = out.get(p["g"], 0) + 1
            i += 1
    return out


def _allocate_pages(elig: list[dict], total: int, existing: dict[int, int] | None,
                    cap_factor: float | None) -> dict[int, int]:
    cap_factor = cap_factor or criteria()["thresholds"]["page_cap_factor"]
    if not elig or total <= 0:
        return {}
    if not existing and total < len(elig):
        return _spread(elig, total)
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
