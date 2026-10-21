"""출력용 번호 부여 — 엑셀과 근거PDF가 같은 번호를 쓰도록 한 곳에서 정한다.

정렬: 차시 → 유형(선다·진위·단답, 양식 예시 순서) → 원고 페이지 → 문항 키
번호 방식: continuous(양식처럼 전체 연번) | per_type(지침처럼 유형별 1번부터)
근거PDF 라벨은 항상 전체 연번 Q{n}을 쓴다(per_type이면 엑셀 숨김 열에 라벨을 함께 기록).
"""
from __future__ import annotations

from .. import integrity
from ..config import criteria


def select(items: list[dict], draft: bool = False, batch: int | None = None) -> list[dict]:
    """최종: 승인 + 현재 내용 검증·승인 해시 일치(integrity.is_exportable).
    초안: 현재 내용이 G1~G6을 통과한 문항(승인 전 포함). 수동확인·재검증 대기 문항은 어느 쪽에도 들어가지 않는다."""
    ok = integrity.is_draftable if draft else integrity.is_exportable
    out = [it for it in items if ok(it)]
    if batch is not None:
        out = [it for it in out if it.get("batch") == batch]
    return out


def number(items: list[dict], mode: str = "continuous") -> list[dict]:
    order = criteria()["type_order"]
    srt = sorted(items, key=lambda it: (it.get("chasi") or 0, order.index(it["type"]), it.get("page") or 0, it["key"]))
    per = {t: 0 for t in order}
    out = []
    for i, it in enumerate(srt, 1):
        per[it["type"]] += 1
        out.append({**it, "no": i if mode == "continuous" else per[it["type"]], "qlabel": f"Q{i}"})
    return out


def target_label(item: dict, target: str) -> str:
    """근거PDF 라벨 접미사(기존 추출본 매핑표: ㄱ→A, ①→1)."""
    if item["type"] == "ox":
        return "OX"
    if item["type"] == "short":
        return "A"
    return {"ㄱ": "A", "ㄴ": "B", "ㄷ": "C", "ㄹ": "D"}.get(target, target)
