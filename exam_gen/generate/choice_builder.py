"""조합형(ㄱㄴㄷㄹ) 보기를 코드로 만든다 (인계 docs/02 3절 구현 메모).

- 정답 조합은 슬롯이 지정한 위치(①~④)에 둔다.
- 각 라벨이 4개 보기에 2~3회 등장하도록(자음 분포),
- 정답과 라벨 수가 같은 오답을 최소 1개 둔다(가장 긴 보기 = 정답 회피).
- 거짓 진술 하나만 찾아도 정답이 나오지 않게 한다(2026-10-21 자체 평가 K00016):
  · 정답 조합의 일부만 고른 오답(거짓 진술 없음)을 최소 1개 둔다 — 참 진술도 모두 확인해야 고를 수 있다.
  · 거짓 진술마다, 그 진술이 들어 있지 않은 오답을 최소 1개 둔다.
"""
from __future__ import annotations

import itertools
import random

LABELS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ"]


def build(correct: list[str], answer_pos: int, seed: int = 0) -> list[list[str]]:
    rng = random.Random(seed)
    correct = sorted(correct, key=LABELS.index)
    all_sets = [list(c) for r in range(1, 5) for c in itertools.combinations(LABELS, r)]
    pool = [s for s in all_sets if s != correct]
    best, best_score = None, None
    for trio in itertools.combinations(pool, 3):
        sets = [correct, *trio]
        counts = [sum(lb in s for s in sets) for lb in LABELS]
        score = sum(0 if 2 <= c <= 3 else abs(c - 2.5) * 2 for c in counts)
        if len(correct) < 4 and not any(len(s) == len(correct) for s in trio):
            score += 3
        if len(correct) == 4 and not any(len(s) == 3 for s in trio):
            score += 1
        if any(len(s) == 4 for s in trio):
            score += 2  # 전체 라벨 보기는 패턴 D(모두 정답)에서만
        score += sum(abs(len(s) - len(correct)) for s in trio) * 0.3
        score += 20 * len(shortcut_problems(correct, trio))  # 지름길 차단은 다른 조건보다 우선
        score += rng.random() * 0.01
        if best_score is None or score < best_score:
            best, best_score = list(trio), score
    best.sort(key=lambda s: (len(s), [LABELS.index(x) for x in s]))
    out = list(best)
    out.insert(answer_pos - 1, correct)
    return out


def shortcut_problems(correct: list[str], distractors: list[list[str]]) -> list[str]:
    """보기 구성만으로 정답을 고를 수 있는 지름길이 있으면 사유를 반환한다."""
    out = []
    false = [lb for lb in LABELS if lb not in correct]
    if len(correct) >= 2 and not any(set(d) < set(correct) for d in distractors):
        out.append("정답 조합의 일부만 고른 오답이 없음 — 거짓 진술만 찾으면 정답이 나옴")
    for f in false:
        if all(f in d for d in distractors):
            out.append(f"모든 오답에 거짓 진술 {f}가 들어 있음 — {f}만 판별하면 정답이 나옴")
    return out


def fmt(labels: list[str]) -> str:
    return ", ".join(labels)
