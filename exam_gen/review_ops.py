"""G7 사람 검수 작업 — 승인·수정·삭제·재검증. 모든 조작은 이벤트로 남긴다(감사 추적).

승인 조건: 코드 관문(G1 구조·G2 근거·G3 규칙)을 현재 내용으로 다시 통과해야 한다.
          근거를 원고에서 찾지 못한 문항은 승인할 수 없다(★2 출처 표시 보장).
"""
from __future__ import annotations

import copy

from .llm.client import LLMClient
from .store import Project, now
from .validate import dedup, evidence, review_llm, rules, schema, solver


def revalidate_code(project: Project, item: dict) -> tuple[list[str], list[str]]:
    rules.autofix(item)
    fails = schema.check_structure(item, item["slot"])
    ef, ew = evidence.check_item(project.pages, item)
    rf, rw = rules.check(item)
    others = [o for o in project.items() if o["key"] != item["key"]]
    df, dw, best = dedup.check(item, others)
    return fails + ef + rf + df, ew + rw + dw


def save_edit(project: Project, item: dict, note: str = "") -> dict:
    """사람이 고친 내용을 저장. 코드 관문을 다시 돌리고, LLM 관문(G4·G5)은 재검증 전 상태로 표시한다."""
    prev = project.item(item["key"])
    fails, warns = revalidate_code(project, item)
    item.setdefault("history", []).append({"at": now(), "action": "edit", "note": note,
                                           "before": {k: prev.get(k) for k in ("stem", "statements", "choices", "answer",
                                                                                "short_variants", "explanation", "evidence")}})
    item["validation"]["fails"] = fails
    item["validation"]["warnings"] = [w for w in warns] + ["사람 수정 — G4·G5 재검증 전"]
    item["validation"]["llm_stale"] = True
    item["status"] = "manual" if fails else "warn"
    return project.save_item(item, "human_edit", {"fails": fails, "note": note})


def approve(project: Project, key: str, who: str = "") -> tuple[bool, list[str]]:
    item = project.item(key)
    fails, warns = revalidate_code(project, item)
    if fails:
        item["validation"]["fails"] = fails
        item["status"] = "manual"
        project.save_item(item, "approve_blocked", {"fails": fails})
        return False, fails
    item["status"] = "approved"
    item["approved"] = {"at": now(), "by": who, "warnings_at_approval": item["validation"].get("warnings", [])}
    project.save_item(item, "approve", {"by": who})
    return True, []


def bulk_approve(project: Project, keys: list[str], who: str = "") -> dict:
    ok, blocked = [], {}
    for k in keys:
        good, f = approve(project, k, who)
        (ok.append(k) if good else blocked.__setitem__(k, f))
    project.log("bulk_approve", {"by": who, "approved": len(ok), "blocked": len(blocked)})
    return {"approved": ok, "blocked": blocked}


def set_status(project: Project, key: str, status: str, reason: str = ""):
    item = project.item(key)
    item.setdefault("history", []).append({"at": now(), "action": status, "note": reason, "from": item.get("status")})
    if status == "restore":
        status = "warn" if item["validation"].get("warnings") else "auto_pass"
        if item["validation"].get("fails"):
            status = "manual"
    item["status"] = status
    project.save_item(item, f"set_{status}", {"reason": reason})


def rerun_llm_gates(project: Project, settings, key: str) -> dict:
    """한 문항에 대해 G4 독립 풀이·G5 LLM 검수를 다시 돌린다."""
    item = project.item(key)
    client = LLMClient(settings)
    work = copy.deepcopy(item)
    evidence.check_item(project.pages, work)
    f4, w4, raw4 = next(iter(solver.solve(client, settings, project, [work]).values()))
    f5, w5, raw5 = next(iter(review_llm.review(client, settings, project, [work],
                                               project.meta.get("objectives", "")).values()))
    item["validation"]["G4"], item["validation"]["G5"] = raw4, raw5
    item["validation"]["llm_stale"] = False
    code_f, code_w = revalidate_code(project, item)
    item["validation"]["fails"] = code_f + f4 + f5
    item["validation"]["warnings"] = code_w + w4 + w5
    if item["status"] != "deleted":
        item["status"] = "manual" if item["validation"]["fails"] else ("warn" if item["validation"]["warnings"] else "auto_pass")
    project.save_item(item, "llm_recheck", {"fails": item["validation"]["fails"]})
    return item
