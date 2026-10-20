"""LLM 입출력 JSON 스키마와 G1(구조·논리) 검사."""
from __future__ import annotations

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}

EVIDENCE = {"type": "object", "required": ["target", "quote", "page_label", "is_true", "trap_note"],
            "properties": {"target": _STR, "quote": _STR, "page_label": _STR, "is_true": _BOOL, "trap_note": _STR}}

GEN_ITEM = {
    "type": "object",
    "required": ["slot_id", "key_point", "stem", "statements", "choices", "answer", "short_variants",
                 "explanation", "evidence", "difficulty_basis"],
    "properties": {
        "slot_id": _STR, "key_point": _STR, "stem": _STR,
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
REVIEW_BLOCKING = {"answer", "dispute"}  # fail이면 탈락, 나머지 fail은 사람 확인(경고)

REVIEW_SCHEMA = {
    "type": "object", "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["id", "checks", "assessed_difficulty", "suggestion"],
        "properties": {
            "id": _STR,
            "assessed_difficulty": {"type": "string", "enum": ["최상", "상", "중", "하"]},
            "checks": {"type": "array", "items": {"type": "object", "required": ["id", "result", "note"],
                                                  "properties": {"id": {"type": "string", "enum": REVIEW_CHECKS},
                                                                 "result": {"type": "string", "enum": ["pass", "warn", "fail"]},
                                                                 "note": _STR}}},
            "suggestion": _STR}}}},
}

LABELS = ["ㄱ", "ㄴ", "ㄷ", "ㄹ"]


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
