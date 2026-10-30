"""G7 사람 검수 작업 — 승인·수정·삭제·재검증. 모든 조작은 이벤트로 남긴다(감사 추적).

승인 조건 (integrity.py)
  - 현재 내용으로 코드 관문(G1 구조·G2 근거·G3 규칙·G6 중복)을 다시 통과
  - 현재 내용 해시에 대한 G4·G5 통과 기록이 있음(수정 후에는 'LLM 재검증' 필요)
  - 근거가 L1(부분 일치)이면 검수자가 근거를 직접 확인했다고 표시
"""
from __future__ import annotations

import copy

from . import integrity
from .llm.client import LLMClient
from .store import Project, now
from .validate import dedup, evidence, review_llm, rules, schema, solver

ACCEPTED = ("approved", "auto_pass", "warn", "prior")  # G6 비교 대상: 채택된 문항 + 기출(수동확인·삭제 후보 제외, Codex C7)


def revalidate_code(project: Project, item: dict) -> tuple[list[str], list[str]]:
    rules.autofix(item)
    fails = schema.check_structure(item, item["slot"])
    ef, ew = evidence.check_item(project.pages, item)
    rf, rw = rules.check(item)
    others = [o for o in project.items(status=list(ACCEPTED)) if o["key"] != item["key"]]
    df, dw, best = dedup.check(item, others)
    item["validation"]["G6"] = best
    return fails + ef + rf + df, ew + rw + dw


def save_edit(project: Project, item: dict, note: str = "") -> dict:
    """사람이 고친 내용을 저장. 코드 관문을 다시 돌리고, 내용이 바뀌었으면 G4·G5 재검증 전 상태로 둔다."""
    prev = project.item(item["key"])
    fails, warns = revalidate_code(project, item)
    changed = integrity.content_hash(prev) != integrity.content_hash(item)
    item.setdefault("history", []).append({"at": now(), "action": "edit", "note": note,
                                           "before": {k: prev.get(k) for k in ("stem", "statements", "choices", "answer",
                                                                                "short_variants", "explanation", "evidence")}})
    item["validation"]["fails"] = fails
    item["validation"]["warnings"] = warns + (["사람 수정 — G4·G5 재검증 전"] if changed else [])
    if changed:
        item["validation"]["llm_stale"] = True
        item.pop("approved", None)
    if item.get("status") == "deleted":
        pass
    elif fails:
        item["status"] = "manual"
    else:
        item["status"] = "approved" if integrity.is_exportable(item) else "warn"
    return project.save_item(item, "human_edit", {"fails": fails, "note": note, "content_changed": changed})


def approve(project: Project, key: str, who: str = "", evidence_confirmed: bool = False) -> tuple[bool, list[str]]:
    item = project.item(key)
    if item.get("status") == "deleted":
        return False, ["삭제된 문항"]
    fails, warns = revalidate_code(project, item)
    item["validation"]["fails"] = fails
    reasons = integrity.block_reasons(item)
    if integrity.needs_evidence_confirm(item) and not evidence_confirmed:
        reasons.append("근거가 L1(부분 일치) — 검수자가 원고 하이라이트를 확인하고 '근거 확인'에 체크해야 함")
    if reasons:
        if fails:
            item["status"] = "manual"
        project.save_item(item, "approve_blocked", {"by": who, "reasons": reasons})
        return False, reasons
    item["status"] = "approved"
    item["approved"] = {"at": now(), "by": who, "hash": integrity.content_hash(item),
                        "evidence_confirmed": bool(evidence_confirmed),
                        "warnings_at_approval": item["validation"].get("warnings", [])}
    project.save_item(item, "approve", {"by": who, "evidence_confirmed": bool(evidence_confirmed)})
    return True, []


def bulk_approve(project: Project, keys: list[str], who: str = "") -> dict:
    """일괄 승인은 근거 확인 표시를 줄 수 없으므로 L1 문항은 자동으로 제외(차단)된다."""
    ok, blocked = [], {}
    for k in keys:
        good, f = approve(project, k, who, evidence_confirmed=False)
        (ok.append(k) if good else blocked.__setitem__(k, f))
    project.log("bulk_approve", {"by": who, "approved": len(ok), "blocked": len(blocked)})
    return {"approved": ok, "blocked": blocked}


def set_status(project: Project, key: str, status: str, reason: str = ""):
    item = project.item(key)
    item.setdefault("history", []).append({"at": now(), "action": status, "note": reason, "from": item.get("status")})
    if status == "restore":
        # 복원은 승인을 되살리지 않는다 — 다시 승인받아야 한다
        item.pop("approved", None)
        v = item["validation"]
        status = "manual" if v.get("fails") or not integrity.is_verified(item) else ("warn" if v.get("warnings") else "auto_pass")
    elif status == "deleted":
        item.pop("approved", None)
    item["status"] = status
    project.save_item(item, f"set_{status}", {"reason": reason})


def skipped_items(project: Project) -> list[dict]:
    return [it for it in project.items() if (it.get("validation") or {}).get("llm_skipped")
            and it.get("status") != "deleted"]


def estimate_verify(settings, n: int) -> int:
    """생략한 AI 검증의 예상 호출 수: 묶음(chunk_size)마다 G4 1회 + G5 1회."""
    size = max(1, getattr(settings, "chunk_size", 8))
    return 2 * ((n + size - 1) // size)


def verify_skipped(project: Project, settings, keys: list[str], progress=None) -> dict:
    """빠른 출제로 생략한 G4·G5를 묶음 단위로 실행한다. 통과하면 일반 문항과 같아지고, 탈락하면 수동확인."""
    client = LLMClient(settings)
    size = max(1, getattr(settings, "chunk_size", 8))
    ok = bad = 0
    for i in range(0, len(keys), size):
        items = [project.item(k) for k in keys[i:i + size]]
        items = [it for it in items if it]
        code = {it["key"]: revalidate_code(project, it) for it in items}
        works = [copy.deepcopy(it) for it in items]
        v4 = list(solver.solve(client, settings, project, works).values())
        v5 = list(review_llm.review(client, settings, project, works, project.meta.get("objectives", "")).values())
        for it, (f4, w4, raw4), (f5, w5, raw5) in zip(items, v4, v5):
            cf, cw = code[it["key"]]
            v = it["validation"]
            v["G4"], v["G5"] = raw4, raw5
            v["fails"] = cf + f4 + f5
            v["warnings"] = [w for w in cw + w4 + w5 if not w.startswith("AI 검증 생략")]
            v["llm_skipped"] = False
            v["llm_stale"] = False
            if v["fails"]:
                v.pop("verified", None)
                if it["status"] != "deleted":
                    it["status"] = "manual"
                bad += 1
            else:
                review_llm.relabel_difficulty(it)
                review_llm.merge_variants(it)
                integrity.mark_verified(it)
                if it["status"] != "deleted":
                    it["status"] = "warn" if v["warnings"] else "auto_pass"
                ok += 1
            project.save_item(it, "verify_skipped", {"fails": v["fails"][:5]})
        if progress:
            progress(min(i + size, len(keys)), len(keys))
    project.log("verify_skipped", {"ok": ok, "manual": bad})
    return {"ok": ok, "manual": bad}


def rerun_llm_gates(project: Project, settings, key: str) -> dict:
    """한 문항에 대해 코드 관문 + G4 독립 풀이 + G5 LLM 검수를 다시 돌리고, 모두 통과하면 검증 기록을 갱신한다."""
    item = project.item(key)
    client = LLMClient(settings)
    code_f, code_w = revalidate_code(project, item)
    work = copy.deepcopy(item)
    f4, w4, raw4 = next(iter(solver.solve(client, settings, project, [work]).values()))
    f5, w5, raw5 = next(iter(review_llm.review(client, settings, project, [work],
                                               project.meta.get("objectives", "")).values()))
    item["validation"]["G4"], item["validation"]["G5"] = raw4, raw5
    item["validation"]["fails"] = code_f + f4 + f5
    item["validation"]["warnings"] = code_w + w4 + w5
    item["validation"]["llm_stale"] = False
    if not item["validation"]["fails"]:
        review_llm.relabel_difficulty(item)
        review_llm.merge_variants(item)
        integrity.mark_verified(item)
    else:
        item["validation"].pop("verified", None)
    if item["status"] != "deleted":
        if item["validation"]["fails"]:
            item["status"] = "manual"
        elif not integrity.is_exportable(item):
            item.pop("approved", None)
            item["status"] = "warn" if item["validation"]["warnings"] else "auto_pass"
    project.save_item(item, "llm_recheck", {"fails": item["validation"]["fails"]})
    return item


def set_human_difficulty(project: Project, key: str, level: str | None, who: str = "") -> dict:
    """담당자 판정 난이도를 기록한다(None이면 지우고 AI 판정으로 되돌림). 문항 내용은 바뀌지 않아 승인은 유지된다."""
    item = project.item(key)
    if level:
        item["human_difficulty"] = {"level": level, "by": who, "at": now()}
    else:
        item.pop("human_difficulty", None)
    review_llm.relabel_difficulty(item)
    project.save_item(item, "human_difficulty", {"level": level, "by": who})
    return item
