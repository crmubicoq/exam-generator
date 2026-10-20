"""조합형(ㄱㄴㄷㄹ) 보기를 코드로 만든다 (인계 docs/02 3절 구현 메모).

- 정답 조합은 슬롯이 지정한 위치(①~④)에 둔다.
- 각 라벨이 4개 보기에 2~3회 등장하도록(자음 분포),
- 정답과 라벨 수가 같은 오답을 최소 1개 둔다(가장 긴 보기 = 정답 회피).
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
        score += rng.random() * 0.01
        if best_score is None or score < best_score:
            best, best_score = list(trio), score
    best.sort(key=lambda s: (len(s), [LABELS.index(x) for x in s]))
    out = list(best)
    out.insert(answer_pos - 1, correct)
    return out


def fmt(labels: list[str]) -> str:
    return ", ".join(labels)
