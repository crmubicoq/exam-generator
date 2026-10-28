"""LLM 입출력 JSON 스키마와 G1(구조·논리) 검사."""
from __future__ import annotations

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}

EVIDENCE = {"type": "object", "required": ["target", "quote", "page_label", "is_true", "trap_note"],
            "properties": {"target": _STR, "quote": _STR, "page_label": _STR, "is_true": _BOOL, "trap_note": _STR}}

GEN_ITEM = {
    "type": "object",
    "required": ["slot_id", "design", "key_point", "stem", "statements", "choices", "answer", "short_variants",
                 "explanation", "evidence", "difficulty_basis"],
    "properties": {
        # 문항을 쓰기 전에 적는 설계(정답 단서·난이도 미달 방지 — 100문항 시험 2026-10-28). 속성 순서대로 생성되므로 stem보다 앞에 둔다
        "slot_id": _STR, "design": _STR, "key_point": _STR, "stem": _STR,
        "statements": {"type": "array", "items": {"type": "object", "required": ["label", "text", "is_true"],
                                                  "properties": {"label": _STR, "text": _STR, "is_true": _BOOL}}},
        "choices": {"type": "array", "items": {"type": "object", "required": ["text", "is_true"],
                                               "properties": {"text": _STR, "is_true": _BOOL}}},
        "answer": _STR,
        "short_variants": {"type": "array", "items": _STR},
        "explanation": _STR,
        "evidence": {"type": "array", "items": EVIDENCE},
        "difficulty_basis": _STR,
    },
}


def gen_schema(n: int) -> dict:
    return {"type": "object", "required": ["items"],
            "properties": {"items": {"type": "array", "items": GEN_ITEM, "minItems": n, "maxItems": n}}}


SOLVE_SCHEMA = {
    "type": "object", "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["id", "answer", "judgments", "unique", "solvable", "note"],
        "properties": {
            "id": _STR, "answer": _STR,
            "judgments": {"type": "array", "items": {"type": "object", "required": ["target", "is_true", "reason"],
                                                     "properties": {"target": _STR, "is_true": _BOOL, "reason": _STR}}},
            "unique": _BOOL, "solvable": _BOOL, "note": _STR}}}},
}

REVIEW_CHECKS = ["answer", "explanation", "choices", "language", "dispute", "completeness",
                 "importance", "difficulty", "fairness"]
REVIEW_BLOCKING = {"answer", "dispute", "choices", "explanation", "completeness"}  # fail이면 탈락(보기: 상식만으로 정답이 보이는 경우,
# 해설: 원고와 반대 뜻 — Codex 모델 비교 검수 X20, 2026-10-27; 조건 누락: 단계 구성 누락 등 — Z15·Z22, 2026-10-28),
# 나머지 fail은 사람 확인(경고)

# 함정 판정: 거짓 보기·진술·X 지문이 원고 문장과 '동시에 성립할 수 없는지'를 따로 판정한다.
# contradiction(모순)이 아니면 정답의 거짓이 입증되지 않으므로 탈락 (Codex 실제 원고 검수 K00011·K00006, 2026-10-22)
TRAP_VERDICTS = {"contradiction": "원고와 모순", "compatible": "원고와 동시에 성립 가능",
                 "not_stated": "원고에 없을 뿐 모순은 아님", "condition_missing": "거짓이 되려면 조건이 더 필요"}

REVIEW_SCHEMA = {
    "type": "object", "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["id", "checks", "traps", "short_term", "assessed_difficulty", "accept_variants",
                                       "suggestion"],
        "properties": {
            "id": _STR,
            # 단답형 정답이 원고가 정의·구분하는 용어인지(Codex 시험 3: '조합', '모든 분께 똑같이' 같은 일반 표현 빈칸)
            "short_term": {"type": "string", "enum": ["concept", "phrase", "not_short"]},
            "traps": {"type": "array", "items": {"type": "object", "required": ["target", "verdict", "conflict"],
                                                 "properties": {"target": _STR,
                                                                "verdict": {"type": "string", "enum": list(TRAP_VERDICTS)},
                                                                "conflict": _STR}}},
            "accept_variants": {"type": "array", "items": _STR},
            "assessed_difficulty": {"type": "string", "enum": ["최상", "상", "중", "하"]},
            "checks": {"type": "array", "items": {"type": "object", "required": ["id", "result", "note"],
                                                  "properties": {"id": {"type": "string", "enum": REVIEW_CHECKS},
                                                                 "result": {"type": "string", "enum": ["pass", "warn", "fail"]},
                                                                 "note": _STR}}},
            "suggestion": _STR}}}},
}

LABELS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ"]


def false_targets(item: dict) -> list[str]:
    """함정 판정 대상: 선다형 거짓 보기 번호, 조합형 거짓 진술 라벨, 정답 X인 진위형('OX'). 단답형은 없음."""
    if item.get("statements"):
        return [s["label"] for s in item["statements"] if not s.get("is_true")]
    if item.get("type") == "mcq":
        return [str(i) for i, c in enumerate(item.get("choices") or [], 1) if not c.get("is_true")]
    if item.get("type") == "ox" and item.get("answer") == "X":
        return ["OX"]
    return []


def check_structure(item: dict, slot: dict) -> list[str]:
    """G1: 슬롯 명세와 문항 구조·정답 논리가 맞는지. 반환: 탈락 사유 목록."""
    f = []
    t = slot["type"]
    if not item.get("stem", "").strip():
        f.append("G1: 발문이 비어 있음")
    if not item.get("explanation", "").strip():
        f.append("G1: 해설이 비어 있음")
    if not item.get("evidence"):
        f.append("G1: 근거(evidence)가 없음")
    if t == "ox":
        if item.get("answer") not in ("O", "X"):
            f.append("G1: 진위형 정답은 O 또는 X여야 함")
        elif item["answer"] != slot["answer"]:
            f.append(f"G1: 진위형 정답이 슬롯 지정값({slot['answer']})과 다름")
    elif t == "short":
        ans = (item.get("answer") or "").strip()
        if not ans:
            f.append("G1: 단답형 정답이 없음")
        elif len(ans) > 30:
            f.append("G1: 단답형 정답이 너무 김(단어 수준이어야 함)")
        if "(" not in item.get("stem", "") and "（" not in item.get("stem", ""):
            f.append("G1: 단답형 발문에 괄호 빈칸이 없음")
    elif slot.get("combo"):
        st = item.get("statements") or []
        if [s.get("label") for s in st] != LABELS:
            f.append("G1: 조합형 진술은 ㄱ, ㄴ, ㄷ, ㄹ 4개여야 함")
        else:
            n_true = sum(1 for s in st if s.get("is_true"))
            if n_true != slot.get("combo_true", n_true):
                f.append(f"G1: 참 진술 수({n_true})가 슬롯 지정값({slot.get('combo_true')})과 다름")
            if n_true == 0:
                f.append("G1: 참 진술이 없음")
            # 최종 보기 검사 (Codex C14): 보기 4개, 서로 다른 조합, 정답 조합 = 참 진술 집합, 정답 위치 = 슬롯
            ch = item.get("choices") or []
            correct = sorted((s["label"] for s in st if s.get("is_true")), key=LABELS.index)
            sets = [sorted(c.get("labels") or [x.strip() for x in c.get("text", "").split(",") if x.strip()],
                           key=lambda x: LABELS.index(x) if x in LABELS else 9) for c in ch]
            if len(ch) != 4:
                f.append("G1: 조합형 보기가 4개가 아님")
            elif len({tuple(x) for x in sets}) != 4:
                f.append("G1: 조합형 보기 조합이 중복됨")
            else:
                hits = [i + 1 for i, x in enumerate(sets) if x == correct]
                if len(hits) != 1:
                    f.append("G1: 참 진술 조합과 일치하는 보기가 정확히 하나가 아님")
                elif str(hits[0]) != str(item.get("answer")) or str(item.get("answer")) != str(slot["answer"]):
                    f.append(f"G1: 조합형 정답 위치 불일치(보기 {hits[0]}, 정답 {item.get('answer')}, 슬롯 {slot['answer']})")
                else:
                    from ..generate.choice_builder import shortcut_problems
                    f += [f"G1: 조합형 보기 — {m}" for m in
                          shortcut_problems(correct, [x for i, x in enumerate(sets) if i + 1 != hits[0]])]
    else:
        ch = item.get("choices") or []
        if len(ch) != 4:
            f.append("G1: 선다형 보기는 4개여야 함")
        else:
            ans = str(item.get("answer"))
            if ans != slot["answer"]:
                f.append(f"G1: 정답 위치({ans})가 슬롯 지정값({slot['answer']})과 다름")
            trues = [i + 1 for i, c in enumerate(ch) if c.get("is_true")]
            if slot.get("polarity") == "positive":
                if len(trues) != 1:
                    f.append(f"G1: 옳은 것 고르기인데 참 보기가 {len(trues)}개")
                elif str(trues[0]) != ans:
                    f.append("G1: 정답 번호와 참 보기 위치가 다름")
            else:
                falses = [i + 1 for i, c in enumerate(ch) if not c.get("is_true")]
                if len(falses) != 1:
                    f.append(f"G1: 옳지 않은 것 고르기인데 거짓 보기가 {len(falses)}개")
                elif str(falses[0]) != ans:
                    f.append("G1: 정답 번호와 거짓 보기 위치가 다름")
            texts = [c.get("text", "").strip() for c in ch]
            if len(set(texts)) != 4:
                f.append("G1: 같은 보기가 중복됨")
    return f
