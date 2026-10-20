"""G5 LLM 검수 — 지침 [8] 검토 항목 + 평가문항 출제 유의사항 체크리스트."""
from __future__ import annotations

from ..llm.prompts import render
from .schema import REVIEW_BLOCKING, REVIEW_CHECKS, REVIEW_SCHEMA
from .solver import excerpt, present

LEVELS = ["최상", "상", "중", "하"]
CHECK_NAMES = {"answer": "정답", "explanation": "해설", "choices": "보기", "language": "오타·비문",
               "dispute": "이의 제기 여지", "completeness": "조건 누락", "importance": "지엽성",
               "difficulty": "난이도", "fairness": "공정성"}


def review(client, settings, project, items: list[dict], objectives: str = "") -> dict:
    ids = [str(i + 1) for i in range(len(items))]
    user = ("# 학습목표\n" + (objectives or "(제공되지 않음)") + "\n\n# 원고 발췌\n" + excerpt(project, items)
            + "\n\n# 문항 (정답·해설·근거 포함)\n" + "\n\n".join(present(it, i, with_key=True) for it, i in zip(items, ids)))
    out = client.structured(render("review_system.j2"), user, REVIEW_SCHEMA, model=settings.model_review,
                            task="review", context={"items": items})
    res = {r["id"]: r for r in out.get("results", [])}
    verdicts = {}
    for i in ids:
        r = res.get(i)
        fails, warns = [], []
        if r is None:
            fails.append("G5: 검수 결과가 없음")
        else:
            got = {c["id"]: c for c in r.get("checks", [])}
            for cid in REVIEW_CHECKS:
                c = got.get(cid)
                if c is None:
                    warns.append(f"G5: '{CHECK_NAMES[cid]}' 판정 누락")
                    continue
                msg = f"G5[{CHECK_NAMES[cid]}] {c.get('note', '')}".strip()
                if c["result"] == "fail" and cid in REVIEW_BLOCKING:
                    fails.append(msg)
                elif c["result"] in ("fail", "warn"):
                    warns.append(msg)
            want, got_lv = items[ids.index(i)].get("difficulty"), r.get("assessed_difficulty")
            if want in LEVELS and got_lv in LEVELS:
                gap = abs(LEVELS.index(want) - LEVELS.index(got_lv))
                if gap >= 2:
                    fails.append(f"G5[난이도] 표시 '{want}'인데 실제 '{got_lv}' 수준 — 슬롯 난이도에 맞게 다시 출제")
                elif gap == 1:
                    warns.append(f"G5[난이도] 표시 '{want}' / 검수 판정 '{got_lv}'")
            if (fails or warns) and r.get("suggestion"):
                warns.append(f"G5 수정안: {r['suggestion'][:300]}")
        verdicts[i] = (fails, warns, r)
    return verdicts
