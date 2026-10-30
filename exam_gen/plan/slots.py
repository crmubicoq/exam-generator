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


# 유형별로 실제로 낼 수 있는 난이도 (2026-10-21 Claude 출제 실측: 단답형·진위형 O는 구조상 '하'가 된다)
#   단답형: 용어 하나를 떠올리는 형태라 하·중까지
#   진위형: 최상 불가. '상'은 정답을 X로 두어 다른 항목과 바꾼 진술을 판별하게 한다
ALLOWED = {"mcq": ("최상", "상", "중", "하"), "ox": ("상", "중", "하"), "short": ("중", "하")}
LEVELS = ("최상", "상", "중", "하")


def fit_types(type_counts: dict, diff_counts: dict) -> dict:
    """난이도별 수량을 직접 정한 경우(추가 출제): 배정이 불가능하면 단답형 → 진위형 순으로 선다형에 자리를 넘긴다."""
    tc = dict(type_counts)
    for donor in ("short", "ox"):
        while True:
            try:
                joint_allocation(tc, diff_counts)
                return tc
            except SlotError:
                if tc.get(donor, 0) == 0:
                    break
                tc[donor] -= 1
                tc["mcq"] = tc.get("mcq", 0) + 1
    joint_allocation(tc, diff_counts)  # 여기까지 오면 선다형만으로도 불가능 — 사유와 함께 오류
    return tc


def joint_allocation(type_counts: dict, diff_counts: dict) -> dict:
    """어려운 난이도부터, 그 난이도를 낼 수 있는 유형에 남은 수량 비례로 채운다. 불가능하면 SlotError."""
    cap = dict(type_counts)
    joint = {(t, d): 0 for t in type_counts for d in LEVELS}
    for lv in LEVELS:
        need = diff_counts.get(lv, 0)
        while need > 0:
            avail = {t: cap[t] for t in type_counts if lv in ALLOWED.get(t, LEVELS) and cap[t] > 0}
            if lv == "상" and "ox" in avail:  # 진위형 '상'은 정답 X — 진위형의 절반을 넘으면 O/X 5:5가 깨진다(2026-10-30)
                room = type_counts["ox"] // 2 - joint[("ox", "상")]
                if room > 0:
                    avail["ox"] = min(avail["ox"], room)
                else:
                    avail.pop("ox")
            if not avail:
                names = {"mcq": "선다형", "ox": "진위형", "short": "단답형"}
                ok = [names[t] for t in type_counts if lv in ALLOWED.get(t, LEVELS)]
                raise SlotError(f"'{lv}' {diff_counts[lv]}문항을 배정할 수 없다 — '{lv}'은(는) {', '.join(ok)}만 낼 수 있다. "
                                "난이도 비율을 낮추거나 선다형 비율을 높이세요.")
            share = largest_remainder(min(need, sum(avail.values())), avail)
            for t, v in share.items():
                v = min(v, cap[t])
                joint[(t, lv)] += v
                cap[t] -= v
                need -= v
    return joint


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
    elif n and not out:
        try:
            tc = largest_remainder(n, opts["type_pct"])
            if difficulty_counts:
                fit_types(tc, difficulty_counts)
            else:
                joint_allocation(tc, largest_remainder(n, opts["difficulty_pct"]))
        except SlotError as e:
            out.append(str(e))
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
    type_counts = largest_remainder(n, opts["type_pct"])
    if difficulty_counts is None:
        difficulty_counts = largest_remainder(n, opts["difficulty_pct"])
    else:
        type_counts = fit_types(type_counts, difficulty_counts)

    # 유형 × 난이도 결합 분포 (유형별 허용 난이도 안에서)
    joint = joint_allocation(type_counts, difficulty_counts)
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
    hard = [s for s in ox if s["difficulty"] in ("상", "최상")]
    for s in hard:
        s["answer"] = "X"  # 상: 다른 항목과 바꾼 진술을 판별 — 맞는 문장 하나(O)는 구조상 '하'가 된다
    rest = [s for s in ox if s not in hard]
    x_left = max(len(ox) // 2 - len(hard), 0)
    pool = ["X"] * x_left + ["O"] * (len(rest) - x_left)
    rng.shuffle(pool)
    for s, a in zip(rest, pool):
        s["answer"] = a
    return slots


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
