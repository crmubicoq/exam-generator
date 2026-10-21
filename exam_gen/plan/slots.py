"""슬롯 사전 배정 — 생성 전에 분포를 코드로 확정한다 (인계 docs/04 설계 원칙 3).

슬롯 = 페이지 × 유형 × 난이도 × 정답 방향(옳은/옳지 않은) × 조합형 여부 × 정답 위치
- 난이도·유형 비율은 최대잔여법으로 정확한 개수를 만든 뒤, 페이지 순서를 따라 고르게 흩뿌린다.
- 선다형 정답 번호는 ①~④를 4개 단위로 섞어 배정 → 각 25%
- 진위형 O/X는 2개 단위로 섞어 배정 → 5:5
"""
from __future__ import annotations

import random

from ..config import criteria
from .coverage import largest_remainder


def _spread(counts: dict, n: int, rng: random.Random) -> list:
    """목표 개수를 연속 구간에 고르게 퍼지도록 배치(누적 비율이 가장 뒤처진 범주를 먼저)."""
    keys = [k for k, c in counts.items() if c > 0]
    rng.shuffle(keys)
    used = {k: 0 for k in keys}
    out = []
    for i in range(n):
        k = min(keys, key=lambda k: ((used[k] + 0.5) / counts[k], -counts[k]) if used[k] < counts[k] else (9e9, 0))
        used[k] += 1
        out.append(k)
    return out


def _blocks(values: list, n: int, rng: random.Random) -> list:
    out = []
    while len(out) < n:
        b = list(values)
        rng.shuffle(b)
        out += b
    return out[:n]


class SlotError(ValueError):
    pass


def check_options(opts: dict, n: int | None = None, difficulty_counts: dict | None = None) -> list[str]:
    """설정끼리 모순되면 사유 목록을 반환한다 (Codex C12·C13). 화면과 build_slots가 같은 검사를 쓴다."""
    out = []
    if sum(opts["difficulty_pct"].values()) != 100:
        out.append("난이도 비율 합계가 100이 아니다.")
    if sum(opts["type_pct"].values()) != 100:
        out.append("유형 비율 합계가 100이 아니다.")
    if opts.get("combo_pct", 0) > opts["mcq_positive_pct"]:
        out.append(f"조합형 비율({opts.get('combo_pct')}%)이 '옳은 것 고르기' 비율({opts['mcq_positive_pct']}%)보다 크다 "
                   "— 조합형은 '옳은 것을 모두 고른 것'이라 옳은 것 고르기에 포함된다.")
    if difficulty_counts is not None and n is not None and sum(difficulty_counts.values()) != n:
        out.append(f"난이도별 수량 합계({sum(difficulty_counts.values())})와 페이지 할당 합계({n})가 다르다.")
    return out


def build_slots(pages: list[dict], page_counts: dict[int, int], opts: dict,
                difficulty_counts: dict | None = None, seed: int = 7, start_index: int = 1) -> list[dict]:
    rng = random.Random(seed)
    c = criteria()
    n = sum(page_counts.values())
    if n == 0:
        return []
    problems = check_options(opts, n, difficulty_counts)
    if problems:
        raise SlotError(" / ".join(problems))
    if difficulty_counts is None:
        difficulty_counts = largest_remainder(n, opts["difficulty_pct"])
    type_counts = largest_remainder(n, opts["type_pct"])

    # 유형 × 난이도 결합 분포
    joint_w = {(t, d): type_counts[t] * difficulty_counts.get(d, 0)
               for t in c["type_order"] for d in c["difficulty_levels"]}
    joint = largest_remainder(n, joint_w)
    _fix_margins(joint, type_counts, difficulty_counts)
    seq = _spread(joint, n, rng)

    by_g = {p["g"]: p for p in pages}
    order = [g for g in sorted(page_counts) for _ in range(page_counts[g])]
    slots = []
    for i, (g, (t, d)) in enumerate(zip(order, seq)):
        slots.append({"sid": f"S{start_index + i:04d}", "page": g, "chasi": by_g[g]["chasi"],
                      "label": by_g[g]["label"], "type": t, "difficulty": d})

    mcq = [s for s in slots if s["type"] == "mcq"]
    combo_n = round(len(mcq) * opts.get("combo_pct", 0) / 100)
    rank = {"최상": 0, "상": 1, "중": 2, "하": 3}
    pick = sorted(mcq, key=lambda s: (rank[s["difficulty"]], rng.random()))[:combo_n]
    for s in mcq:
        s["combo"] = s in pick
    # 조합형 진술 패턴(인계 docs/02): A 정답3+함정1 50% / B 2+2 30% / C 1+3 10% / D 4+0 10%
    combos = [s for s in mcq if s["combo"]]
    pattern = largest_remainder(len(combos), {3: 50, 2: 30, 1: 10, 4: 10})
    for s, k in zip(combos, _spread(pattern, len(combos), rng)):
        s["combo_true"] = k
        s["polarity"] = "positive"  # 조합형은 "옳은 것을 모두 고른 것은?"
    # 지침: 옳은 것 고르기 20% / 옳지 않은 것 고르기 80% (조합형은 옳은 것 쪽으로 센다)
    single = [s for s in mcq if not s["combo"]]
    pos_n = max(round(len(mcq) * opts["mcq_positive_pct"] / 100) - len(combos), 0)
    pos_n = min(pos_n, len(single))
    for s, pol in zip(single, _spread({"positive": pos_n, "negative": len(single) - pos_n}, len(single), rng)):
        s["polarity"] = pol
    for s, a in zip(mcq, _blocks(["1", "2", "3", "4"], len(mcq), rng)):
        s["answer"] = a
    ox = [s for s in slots if s["type"] == "ox"]
    for s, a in zip(ox, _blocks(["O", "X"], len(ox), rng)):
        s["answer"] = a
    return slots


def _fix_margins(joint: dict, type_counts: dict, diff_counts: dict):
    """반올림으로 생긴 유형·난이도 주변 합계 오차를 1개씩 옮겨 맞춘다."""
    for _ in range(50):
        tsum = {t: sum(v for (tt, _), v in joint.items() if tt == t) for t in type_counts}
        dsum = {d: sum(v for (_, dd), v in joint.items() if dd == d) for d in diff_counts}
        t_over = [t for t in tsum if tsum[t] > type_counts[t]]
        t_under = [t for t in tsum if tsum[t] < type_counts[t]]
        d_over = [d for d in dsum if dsum[d] > diff_counts.get(d, 0)]
        d_under = [d for d in dsum if dsum[d] < diff_counts.get(d, 0)]
        if not (t_over or d_over):
            return
        moved = False
        for to in t_over or list(type_counts):
            for do in d_over or list(diff_counts):
                if joint.get((to, do), 0) <= 0:
                    continue
                tu = t_under[0] if t_under else to
                du = d_under[0] if d_under else do
                if (tu, du) == (to, do):
                    continue
                joint[(to, do)] -= 1
                joint[(tu, du)] = joint.get((tu, du), 0) + 1
                moved = True
                break
            if moved:
                break
        if not moved:
            return


def summarize(slots: list[dict]) -> dict:
    out = {"total": len(slots), "type": {}, "difficulty": {}, "answer": {}, "polarity": {}, "combo": 0, "pages": {}}
    for s in slots:
        out["type"][s["type"]] = out["type"].get(s["type"], 0) + 1
        out["difficulty"][s["difficulty"]] = out["difficulty"].get(s["difficulty"], 0) + 1
        if s.get("answer"):
            out["answer"][s["answer"]] = out["answer"].get(s["answer"], 0) + 1
        if s.get("polarity"):
            out["polarity"][s["polarity"]] = out["polarity"].get(s["polarity"], 0) + 1
        out["combo"] += 1 if s.get("combo") else 0
        out["pages"][s["page"]] = out["pages"].get(s["page"], 0) + 1
    return out
