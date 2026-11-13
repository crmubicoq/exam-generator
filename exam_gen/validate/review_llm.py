"""G5 LLM 검수 — 지침 [8] 검토 항목 + 평가문항 출제 유의사항 체크리스트."""
from __future__ import annotations

from .. import difficulty_policy
from ..llm.prompts import render
from .schema import REVIEW_BLOCKING, REVIEW_CHECKS, REVIEW_SCHEMA, TRAP_VERDICTS, false_targets
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
        return client.structured(render("review_system.j2", rubric=difficulty_policy.rubric(project),
                                        policy_status=_policy_status(project)), user, REVIEW_SCHEMA, model=settings.model_review,
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
            fails += _trap_fails(items[ids.index(i)], r.get("traps"))
            if items[ids.index(i)].get("type") == "short" and r.get("short_term") != "concept":
                fails.append("G5[단답 용어] 빈칸 정답이 원고가 정의·구분하는 용어가 아니라 일반 표현임 — 다른 말도 들어갈 수 있으므로 "
                             "원고의 개념·분류·역할 이름을 묻도록 다시 출제")
            want, got_lv = items[ids.index(i)].get("difficulty"), r.get("assessed_difficulty")
            if got_lv not in LEVELS:
                fails.append("G5: 실제 난이도 판정 누락")
            elif want in LEVELS:
                gap = abs(LEVELS.index(want) - LEVELS.index(got_lv))
                if gap >= 2:
                    # 난이도 차이는 문항 오류가 아니므로 승인을 막지 않는다(담당부서 답변 2026-11-13 #3). 출제 중에는
                    # generator가 이 경고를 보고 재출제 기회가 남아 있으면 다시 낸다(DIFF_GAP_WARN)
                    warns.append(f"{DIFF_GAP_WARN} 표시 '{want}'인데 AI 판정 '{got_lv}' 수준 — 참고(승인은 막지 않음)")
                elif gap == 1:
                    warns.append(f"G5[난이도] 표시 '{want}' / 검수 판정 '{got_lv}'")
            if (fails or warns) and r.get("suggestion"):
                warns.append(f"G5 수정안: {r['suggestion'][:300]}")
        verdicts[i] = (fails, warns, r)
    return verdicts


DIFF_GAP_WARN = "G5[난이도 두 단계 차이]"
DIFF_GAP_FAIL = "G5[난이도] 표시"  # v1.1.x까지 실패로 저장된 난이도 차이(이제는 경고로 옮긴다)


def is_difficulty_only(msg: str) -> bool:
    return msg.startswith((DIFF_GAP_WARN, DIFF_GAP_FAIL)) and ("실제" in msg or "AI 판정" in msg) and "누락" not in msg


def _norm_target(t) -> str:
    t = str(t or "").strip().strip("[]().")
    return {"①": "1", "②": "2", "③": "3", "④": "4", "O/X": "OX", "ox": "OX"}.get(t, t)


def _trap_fails(item: dict, traps) -> list[str]:
    """거짓이어야 할 대상마다 '원고와 모순'으로 판정됐는지 확인한다. 판정 누락·모순 아님은 탈락."""
    got = {_norm_target(t.get("target")): t for t in traps or [] if isinstance(t, dict)}
    out = []
    for tgt in false_targets(item):
        t = got.get(tgt)
        if t is None:
            out.append(f"G5[함정] '{tgt}' 판정 누락 — 거짓 보기가 원고와 모순되는지 확인되지 않음")
        elif t.get("verdict") != "contradiction":
            out.append(f"G5[함정] '{tgt}' {TRAP_VERDICTS.get(t.get('verdict'), t.get('verdict'))} — 거짓이 입증되지 않음: "
                       f"{(t.get('conflict') or '')[:200]}")
    return out


def _policy_status(project=None) -> str:
    p = difficulty_policy.load(project)
    return "담당자 확정 기준" if p["status"] == "확정" else "임시 기준안 — 담당자 확인 전"


def relabel_difficulty(item: dict) -> None:
    """G5 판정 난이도가 표시와 한 단계 다르면 그 난이도로 매긴다. 슬롯 목표는 difficulty_target에 남긴다."""
    item["difficulty_target"] = item.get("difficulty_target") or item["slot"]["difficulty"]
    lv = (item["validation"].get("G5") or {}).get("assessed_difficulty")
    warns = [w for w in item["validation"].get("warnings", []) if not w.startswith(("G5[난이도] 표시", DIFF_GAP_WARN, "난이도 재분류"))]
    item["difficulty"] = lv if lv in LEVELS else item["difficulty_target"]
    human = (item.get("human_difficulty") or {}).get("level")
    if human in LEVELS:  # 담당자가 판정한 난이도가 AI 판정보다 우선한다
        item["difficulty"] = human
    if item["difficulty"] != item["difficulty_target"]:
        warns.append(f"난이도 재분류: 목표 '{item['difficulty_target']}' → 실제 '{item['difficulty']}'")
    item["validation"]["warnings"] = warns


def merge_variants(item: dict) -> list[str]:
    """단답형: G5가 제안한 허용 답안을 short_variants에 합친다. 추가한 표현을 반환(검수 화면·경고에 표시)."""
    if item.get("type") != "short":
        return []
    from ..ingest.pdf_text import normalize
    from .rules import _variant_key
    cand = [v.strip() for v in (item["validation"].get("G5") or {}).get("accept_variants") or [] if v and v.strip()]
    have = {normalize(x) for x in [item.get("answer", ""), *(item.get("short_variants") or [])]}
    keys = {_variant_key(x) for x in [item.get("answer", ""), *(item.get("short_variants") or [])]}
    new = []
    for v in cand:
        # 정답과 띄어쓰기·끝 조사만 다른 표현('사후리뷰를')은 채점상 같은 답이므로 넣지 않는다
        if len(v) <= 30 and normalize(v) and normalize(v) not in have and _variant_key(v) not in keys:
            have.add(normalize(v))
            keys.add(_variant_key(v))
            new.append(v)
    if new:
        item["short_variants"] = list(item.get("short_variants") or []) + new
        item["validation"].setdefault("warnings", []).append(f"허용 답안 자동 추가(검수 시 확인): {', '.join(new)}")
    return new

