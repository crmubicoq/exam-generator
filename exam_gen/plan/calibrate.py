"""난이도 보정 출제 — AI 검수(G5)가 출제 의도보다 낮게 판정하는 경향을 반영해 슬롯 난이도를 정한다. 토큰 0.

배경(2026-10-28 실측): 상 슬롯은 약 90%가 '중', 최상(사례형) 슬롯은 약 90%가 '상', 중 슬롯은 약 40%가 '하'로 판정된다.
그래서 목표 비율(예: 최상 5·상 15·중 60·하 20)대로 슬롯을 만들면 실제 판정 분포는 '하'로 쏠린다.

방법
1. 판정 비율표(슬롯 난이도 → 실제 판정 난이도 확률)를 만든다. 이 PC의 출제 기록(G5 판정이 있는 문항)이 있으면
   criteria.yaml의 기본표(개발 시험 실측)와 섞어 쓴다 — 기록이 쌓일수록 기록 쪽 비중이 커진다.
2. 슬롯 수만큼 하나씩, '예상 판정 분포'가 목표에 가장 가까워지는 난이도의 슬롯을 더한다(모자람을 넘침보다 무겁게 본다).
   유형별 허용 난이도(최상은 선다형만, 상은 선다형·진위형만)를 넘지 않는다.
3. 출제가 끝나면 최종 문항 자동 선정(select_final)이 실제 판정 난이도로 목표 비율에 맞춰 고른다.
"""
from __future__ import annotations

import math

from collections import Counter, defaultdict

from ..config import PROJECTS_DIR, criteria
from .slots import ALLOWED, LEVELS

PRIOR_WEIGHT = 20  # 기본표를 문항 20개 분량의 기록으로 본다


def default_table() -> dict[str, dict[str, float]]:
    return {k: dict(v) for k, v in criteria()["difficulty_calibration"]["table"].items()}


def history_counts(projects_dir=None) -> dict[str, Counter]:
    """이 PC 출제 기록에서 슬롯 난이도별 실제 판정 개수. 기준 프롬프트 버전 이후 문항만 센다."""
    from ..store import Project, list_projects  # 순환 import 방지
    since = criteria()["difficulty_calibration"].get("since", "")
    out: dict[str, Counter] = defaultdict(Counter)
    for slug in list_projects():
        try:
            items = Project(slug).items()
        except Exception:  # noqa: BLE001 — 손상된 프로젝트는 건너뛴다
            continue
        for it in items:
            # 담당자가 판정한 문항은 그 판정을 쓴다 — 담당자 판정이 쌓일수록 보정이 담당자 기준으로 맞춰진다
            lv = ((it.get("human_difficulty") or {}).get("level")
                  or ((it.get("validation") or {}).get("G5") or {}).get("assessed_difficulty"))
            slot_lv = (it.get("slot") or {}).get("difficulty")
            if lv in LEVELS and slot_lv in LEVELS and (it.get("gen") or {}).get("prompt_version", "") >= since:
                out[slot_lv][lv] += 1
    return out


def table(use_history: bool = True) -> tuple[dict[str, dict[str, float]], dict[str, int]]:
    """판정 비율표와 슬롯 난이도별 기록 문항 수."""
    base = default_table()
    hist = history_counts() if use_history else {}
    tab, n_hist = {}, {}
    for s in LEVELS:
        cnt = hist.get(s, Counter())
        n = sum(cnt.values())
        n_hist[s] = n
        row = {a: (base.get(s, {}).get(a, 0.0) * PRIOR_WEIGHT + cnt.get(a, 0)) / (PRIOR_WEIGHT + n) for a in LEVELS}
        tot = sum(row.values()) or 1.0
        tab[s] = {a: v / tot for a, v in row.items()}
    return tab, n_hist


def expected(slot_counts: dict[str, int], tab: dict[str, dict[str, float]]) -> dict[str, float]:
    return {a: sum(slot_counts.get(s, 0) * tab[s].get(a, 0.0) for s in LEVELS) for a in LEVELS}


def plan(n: int, target_pct: dict[str, int], type_counts: dict[str, int],
         tab: dict[str, dict[str, float]]) -> dict[str, int]:
    """슬롯 n개의 난이도 수량. 예상 판정 분포가 목표(n × 목표 비율)에 가장 가깝게."""
    goal = {lv: n * target_pct.get(lv, 0) / 100 for lv in LEVELS}
    cap_mcq = type_counts.get("mcq", 0)
    # 상을 낼 수 있는 자리: 선다형 전부 + 진위형 절반(진위형 '상'은 정답이 X라 절반을 넘으면 O/X 5:5가 깨진다)
    cap_hard = type_counts.get("mcq", 0) + type_counts.get("ox", 0) // 2
    counts = {lv: 0 for lv in LEVELS}
    # 최상(사례형) 슬롯은 '상'의 주된 공급원이지만(실측 93%가 상) 출제 시간이 길어(문항당 약 1분) 전체의 20%까지만 쓴다
    cap_top = min(cap_mcq, math.ceil(0.2 * n)) if goal["최상"] + goal["상"] > 0 else 0

    def loss(c):
        e = expected(c, tab)
        # 모자람은 최종 선정으로 메울 수 없으므로 넘침보다 무겁게 본다. 어려운 난이도일수록 더 무겁게
        w = {"최상": 3.0, "상": 2.0, "중": 1.0, "하": 1.0}
        return sum(w[lv] * max(0.0, goal[lv] - e[lv]) ** 2 + 0.3 * max(0.0, e[lv] - goal[lv]) ** 2 for lv in LEVELS)

    for _ in range(n):
        best, best_loss = None, None
        for lv in LEVELS:
            c = {**counts, lv: counts[lv] + 1}
            if c["최상"] > cap_top or c["최상"] + c["상"] > cap_hard:
                continue
            ls = loss(c)
            if best_loss is None or ls < best_loss - 1e-9:
                best, best_loss = lv, ls
        counts[best or "하"] += 1
    return counts
