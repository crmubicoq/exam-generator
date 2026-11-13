"""출력용 번호 부여 — 엑셀과 근거PDF가 같은 번호를 쓰도록 한 곳에서 정한다.

정렬: 차시 → 유형(선다·진위·단답, 양식 예시 순서) → 원고 페이지 → 문항 키
번호 방식: continuous(양식처럼 전체 연번) | per_type(지침처럼 유형별 1번부터)
근거PDF 라벨은 항상 전체 연번 Q{n}을 쓴다(per_type이면 엑셀 숨김 열에 라벨을 함께 기록).
"""
from __future__ import annotations

from .. import integrity
from ..config import criteria


# 출력 대상(담당자 협의 2026-10-23: 검수 없이도 출력 — 탈락 문항까지)
SCOPES = {"all": "전체(검수 없이, 탈락 문항 포함)", "passed": "자동 검증 통과 문항", "approved": "승인한 문항만(납품 확정용)",
          "selected": "최종 선정 문항만(자동 선정 결과)"}


def select(items: list[dict], draft: bool = False, batch: int | None = None, scope: str | None = None) -> list[dict]:
    """scope
    - all: 삭제·기출을 뺀 모든 문항(자동통과·경고·수동확인·보류·승인)
    보류(hold)는 어느 대상이든 함께 나간다(빼려면 items에서 미리 뺀다)
    - passed: 현재 내용이 G1~G6을 통과한 문항(승인 전 포함)
    - approved: 승인 + 현재 내용 검증·승인 해시 일치(integrity.is_exportable)
    scope를 주지 않으면 예전 방식(draft → passed, 아니면 approved)."""
    scope = scope or ("passed" if draft else "approved")
    if scope == "all":
        out = [it for it in items if it.get("status") not in ("deleted", "prior")]
    elif scope == "selected":
        out = [it for it in items if it.get("selected") and it.get("status") not in ("deleted", "prior")]
    else:
        ok = integrity.is_draftable if scope == "passed" else integrity.is_exportable
        out = [it for it in items if ok(it)]
        # 보류 문항은 출력 대상과 상관없이 넣는다 — 빼려면 부르는 쪽이 items에서 미리 뺀다(출력 화면 '보류 문항 포함')
        have = {it["key"] for it in out}
        out += [it for it in items if it.get("status") == "hold" and it["key"] not in have]
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
