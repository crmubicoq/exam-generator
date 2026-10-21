"""G5 LLM 검수 — 지침 [8] 검토 항목 + 평가문항 출제 유의사항 체크리스트."""
from __future__ import annotations

from ..llm.prompts import render
from .schema import REVIEW_BLOCKING, REVIEW_CHECKS, REVIEW_SCHEMA
from .solver import ask_until_complete, excerpt, present

LEVELS = ["최상", "상", "중", "하"]
CHECK_NAMES = {"answer": "정답", "explanation": "해설", "choices": "보기", "language": "오타·비문",
               "dispute": "이의 제기 여지", "completeness": "조건 누락", "importance": "지엽성",
               "difficulty": "난이도", "fairness": "공정성"}


def review(client, settings, project, items: list[dict], objectives: str = "") -> dict:
    ids = [str(i + 1) for i in range(len(items))]

    def call(sub_items, sub_ids):
        user = ("# 학습목표\n" + (objectives or "(제공되지 않음)") + "\n\n# 원고 발췌\n" + excerpt(project, sub_items)
                + "\n\n# 문항 (정답·해설·근거 포함)\n"
                + "\n\n".join(present(it, i, with_key=True) for it, i in zip(sub_items, sub_ids)))
        return client.structured(render("review_system.j2"), user, REVIEW_SCHEMA, model=settings.model_review,
                                 task="review", context={"items": sub_items, "ids": sub_ids})

    res = ask_until_complete(call, items, ids)
    verdicts = {}
    for i in ids:
        r = res.get(i)
        fails, warns = [], []
        if r is None:
            fails.append("G5: 검수 결과가 없음")
        else:
            got = {}
            for c in r.get("checks", []):
                if c.get("id") not in REVIEW_CHECKS:
                    fails.append(f"G5: 알 수 없는 검수 항목 '{c.get('id')}'")
                elif c["id"] in got:
                    fails.append(f"G5: '{CHECK_NAMES[c['id']]}' 판정이 중복됨")
                elif c.get("result") not in ("pass", "warn", "fail"):
                    fails.append(f"G5: '{CHECK_NAMES[c['id']]}' 판정값이 잘못됨({c.get('result')})")
                else:
                    got[c["id"]] = c
            for cid in REVIEW_CHECKS:
                c = got.get(cid)
                if c is None:
                    # Codex C11: 검수 항목 누락은 통과로 볼 수 없다
                    fails.append(f"G5: '{CHECK_NAMES[cid]}' 판정 누락")
                    continue
                msg = f"G5[{CHECK_NAMES[cid]}] {c.get('note', '')}".strip()
                if c["result"] == "fail" and cid in REVIEW_BLOCKING:
                    fails.append(msg)
                elif c["result"] in ("fail", "warn"):
                    warns.append(msg)
            want, got_lv = items[ids.index(i)].get("difficulty"), r.get("assessed_difficulty")
            if got_lv not in LEVELS:
                fails.append("G5: 실제 난이도 판정 누락")
            elif want in LEVELS:
                gap = abs(LEVELS.index(want) - LEVELS.index(got_lv))
                if gap >= 2:
                    fails.append(f"G5[난이도] 표시 '{want}'인데 실제 '{got_lv}' 수준 — 슬롯 난이도에 맞게 다시 출제")
                elif gap == 1:
                    warns.append(f"G5[난이도] 표시 '{want}' / 검수 판정 '{got_lv}'")
            if (fails or warns) and r.get("suggestion"):
                warns.append(f"G5 수정안: {r['suggestion'][:300]}")
        verdicts[i] = (fails, warns, r)
    return verdicts


def relabel_difficulty(item: dict) -> None:
    """G5 판정 난이도가 표시와 한 단계 다르면 그 난이도로 매긴다. 슬롯 목표는 difficulty_target에 남긴다."""
    item["difficulty_target"] = item.get("difficulty_target") or item["slot"]["difficulty"]
    lv = (item["validation"].get("G5") or {}).get("assessed_difficulty")
    warns = [w for w in item["validation"].get("warnings", []) if not w.startswith(("G5[난이도] 표시", "난이도 재분류"))]
    if lv in LEVELS and lv != item["difficulty"]:
        item["difficulty"] = lv
    if item["difficulty"] != item["difficulty_target"]:
        warns.append(f"난이도 재분류: 목표 '{item['difficulty_target']}' → 실제 '{item['difficulty']}'")
    item["validation"]["warnings"] = warns


def merge_variants(item: dict) -> list[str]:
    """단답형: G5가 제안한 허용 답안을 short_variants에 합친다. 추가한 표현을 반환(검수 화면·경고에 표시)."""
    if item.get("type") != "short":
        return []
    from ..ingest.pdf_text import normalize
    cand = [v.strip() for v in (item["validation"].get("G5") or {}).get("accept_variants") or [] if v and v.strip()]
    have = {normalize(x) for x in [item.get("answer", ""), *(item.get("short_variants") or [])]}
    new = []
    for v in cand:
        if len(v) <= 30 and normalize(v) and normalize(v) not in have:
            have.add(normalize(v))
            new.append(v)
    if new:
        item["short_variants"] = list(item.get("short_variants") or []) + new
        item["validation"].setdefault("warnings", []).append(f"허용 답안 자동 추가(검수 시 확인): {', '.join(new)}")
    return new

