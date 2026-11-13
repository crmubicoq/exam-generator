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

ACCEPTED = ("approved", "auto_pass", "warn", "hold", "prior")  # G6 비교 대상: 채택된 문항·보류 + 기출(수동확인·삭제 후보 제외, Codex C7)


def revalidate_code(project: Project, item: dict) -> tuple[list[str], list[str]]:
    rules.autofix(item)
    fails = schema.check_structure(item, item["slot"])
    ef, ew = evidence.check_item(project.pages, item)
    rf, rw = rules.check(item, project.item_source_text(item))
    others = [o for o in project.items(status=list(ACCEPTED)) if o["key"] != item["key"]]
    df, dw, best = dedup.check(item, others)
    item["validation"]["G6"] = best
    return fails + ef + rf + df, ew + rw + dw


PENDING = ("manual", "warn", "auto_pass", "hold")  # 사람이 아직 승인·삭제하지 않은 문항(보류 포함)
HOLD_KINDS = ["중복 의심", "내용 확인", "기타"]


def pending_counts(items: list[dict]) -> dict[str, int]:
    """검수 남은 문항 수(상태별). 승인·삭제·기출은 빠진다."""
    out = {s: 0 for s in PENDING}
    for it in items:
        if it.get("status") in out:
            out[it["status"]] += 1
    return out


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
    if item.get("status") in ("deleted", "hold"):
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
    item.pop("hold", None)
    item["approved"] = {"at": now(), "by": who, "hash": integrity.content_hash(item),
                        "evidence_confirmed": bool(evidence_confirmed),
                        "warnings_at_approval": item["validation"].get("warnings", [])}
    project.save_item(item, "approve", {"by": who, "evidence_confirmed": bool(evidence_confirmed)})
    return True, []


def can_skip_reverify(item: dict) -> bool:
    """'AI 재검증 없이 승인'을 보여 줄 문항: 사람이 고쳐서 AI 검증이 무효가 된 문항만(빠른 출제 문항은 해당 없음)."""
    v = item.get("validation") or {}
    return bool(v.get("llm_stale")) and item.get("status") not in ("deleted", "approved")


def approve_without_reverify(project: Project, key: str, who: str, evidence_confirmed: bool = False) -> tuple[bool, list[str]]:
    """수정 후 AI 재검증을 건너뛰고 사람 책임으로 승인한다(급할 때만, 설정에서 켠 경우).
    무료 검사(형식·근거 대조·규칙·중복)는 다시 돌려 통과해야 하고, 검수자 이름이 필요하다.
    문항은 'AI 검증 생략' 대기열·품질 리포트에 남아 나중에 '생략한 AI 검증 실행'으로 한꺼번에 검증할 수 있다."""
    if not who:
        return False, ["검수자 이름이 필요합니다"]
    item = project.item(key)
    if not can_skip_reverify(item):
        return False, ["사람이 고친 뒤 AI 재검증 전인 문항만 건너뛸 수 있습니다"]
    fails, warns = revalidate_code(project, item)
    item["validation"]["fails"] = fails
    if fails:
        item["status"] = "manual"
        project.save_item(item, "approve_blocked", {"by": who, "reasons": fails, "override": True})
        return False, fails
    v = item["validation"]
    v["human_override"] = {"hash": integrity.content_hash(item), "by": who, "at": now(), "reason": "수정 후 AI 재검증 생략"}
    v["llm_skipped"] = True
    note = f"수정 후 AI 재검증 생략 — 검수자 {who} 확인({now()[:10]}). 나중에 '생략한 AI 검증 실행'으로 검증하세요"
    v["warnings"] = [w for w in v.get("warnings", []) if not w.startswith("수정 후 AI 재검증 생략")] + [note]
    project.save_item(item, "human_override", {"by": who})
    ok, reasons = approve(project, key, who, evidence_confirmed=evidence_confirmed)
    return ok, reasons


def approve_by_judgment(project: Project, key: str, who: str, note: str = "") -> tuple[bool, list[str]]:
    """검수자 판단으로 승인(담당부서 피드백 2026-11-13 #1): 승인을 막는 사유(자동 검사·AI 검증·근거 확인 등)가 있어도 검수자가
    사유를 확인하고 책임지고 승인한다. 막혔던 사유·검수자·메모를 기록하고, 출력·품질 리포트에 '검수자 판단 승인'으로 표시한다.
    내용을 고치면 이 승인은 무효가 된다(내용 해시)."""
    if not who:
        return False, ["검수자 이름이 필요합니다"]
    item = project.item(key)
    if item.get("status") == "deleted":
        return False, ["삭제된 문항"]
    fails, _ = revalidate_code(project, item)
    v = item["validation"]
    v["fails"] = list(dict.fromkeys(fails + [f for f in v.get("fails", []) if f.startswith(("G4", "G5"))]))
    reasons = integrity.block_reasons({**item, "validation": {**v, "judgment": None}})
    if integrity.needs_evidence_confirm(item):
        reasons.append("근거 부분 일치(L1) — 검수자 확인")
    h = integrity.content_hash(item)
    v["judgment"] = {"hash": h, "by": who, "at": now(), "reasons": reasons, "note": note}
    item.pop("hold", None)
    item["status"] = "approved"
    item["approved"] = {"at": now(), "by": who, "hash": h, "evidence_confirmed": True, "judgment": True,
                        "warnings_at_approval": v.get("warnings", [])}
    project.save_item(item, "approve_judgment", {"by": who, "reasons": reasons, "note": note})
    return True, []


def set_hold(project: Project, key: str, kind: str, memo: str = "", who: str = "") -> dict:
    """보류(담당부서 피드백 2026-11-13 #7): 일단 두고 나중에 판단할 문항. 승인은 풀리고, 출력할 때 포함 여부를 고른다."""
    item = project.item(key)
    item.setdefault("history", []).append({"at": now(), "action": "hold", "note": f"{kind} {memo}".strip(),
                                           "from": item.get("status")})
    item.pop("approved", None)
    item["hold"] = {"kind": kind, "memo": memo, "by": who, "at": now()}
    item["status"] = "hold"
    return project.save_item(item, "set_hold", {"kind": kind, "memo": memo, "by": who})


def refresh_rules(project: Project) -> dict:
    """검사 기준이 바뀐 뒤(v1.2.0) 아직 승인·삭제하지 않은 문항을 AI 없이 다시 확인한다.
    - 코드 관문(G1·G2·G3·G6)을 새 기준으로 다시 돌리고, 난이도 차이 실패는 경고로 옮긴다.
    - 저장된 AI 검증(G4·G5) 실패가 없고 지금 내용에 대한 AI 검증 결과가 있으면 '검증 통과'로 바꾼다.
    반환: {"checked", "released"} — released는 수동확인에서 풀린 문항 수."""
    checked = released = 0
    for item in project.items(status=["manual", "warn", "auto_pass", "hold"]):
        v = item.get("validation") or {}
        if v.get("llm_skipped"):
            continue
        before = list(v.get("fails") or [])
        diff = [f for f in before if review_llm.is_difficulty_only(f)]
        # 코드 관문(G1·G2·G3·G6) 결과만 새로 계산하고, AI 검증(G4·G5)·원고 변경 등 나머지 사유는 그대로 둔다
        llm = [f for f in before if not f.startswith(("G1", "G2", "G3", "G6")) and f not in diff]
        cf, cw = revalidate_code(project, item)
        fails = list(dict.fromkeys(cf + llm))
        v["fails"] = fails
        v["warnings"] = list(dict.fromkeys([w for w in v.get("warnings", []) if not w.startswith(("G1", "G2", "G3", "G6"))]
                                           + cw + [f.split(" — ")[0] + " — 참고(승인은 막지 않음)" for f in diff]))
        checked += 1
        st0 = item["status"]
        if not fails and v.get("G4") and v.get("G5") and not v.get("llm_stale") and not v.get("ms_change") and item["status"] in ("manual", "warn", "auto_pass"):
            was_manual = item["status"] == "manual"
            review_llm.relabel_difficulty(item)
            integrity.mark_verified(item)
            item["status"] = "warn" if v["warnings"] else "auto_pass"
            released += was_manual
        elif fails and item["status"] in ("warn", "auto_pass"):
            item["status"] = "manual"
        if fails != before or item["status"] != st0:
            project.save_item(item, "rules_refresh", {"fails": fails[:5]})
    project.log("rules_refresh", {"checked": checked, "released": released})
    return {"checked": checked, "released": released}


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
        # 복원(보류 풀기 포함)은 승인을 되살리지 않는다 — 다시 승인받아야 한다
        item.pop("approved", None)
        item.pop("hold", None)
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


def verify_skipped(project: Project, settings, keys: list[str], progress=None, should_stop=None) -> dict:
    """빠른 출제·'수정 후 AI 재검증 생략'으로 미룬 G4·G5를 묶음 단위로 실행한다. 통과하면 일반 문항과 같아지고(승인한 문항은
    승인 당시 내용 그대로면 승인 유지), 탈락하면 수동확인. should_stop()이 참이면 지금 묶음까지만 하고 멈춘다."""
    client = LLMClient(settings)
    size = max(1, getattr(settings, "chunk_size", 8))
    ok = bad = 0
    stopped = False
    for i in range(0, len(keys), size):
        if should_stop and should_stop():
            stopped = True
            break
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
            v["warnings"] = [w for w in cw + w4 + w5 if not w.startswith(("AI 검증 생략", "수정 후 AI 재검증 생략"))]
            v["llm_skipped"] = False
            v["llm_stale"] = False
            was_approved = (it["status"] == "approved"
                            and (it.get("approved") or {}).get("hash") == integrity.content_hash(it))
            if v["fails"]:
                v.pop("verified", None)
                if it["status"] not in ("deleted", "hold"):
                    it["status"] = "manual"  # 사람 책임으로 승인했던 문항도 AI 검증에서 탈락하면 승인이 풀린다
                bad += 1
            else:
                review_llm.relabel_difficulty(it)
                review_llm.merge_variants(it)
                integrity.mark_verified(it)
                if it["status"] not in ("deleted", "hold"):
                    if was_approved and (it.get("approved") or {}).get("hash") == integrity.content_hash(it):
                        it["status"] = "approved"  # 승인 당시 내용 그대로 통과 — 승인 유지
                    else:
                        it["status"] = "warn" if v["warnings"] else "auto_pass"
                ok += 1
            project.save_item(it, "verify_skipped", {"fails": v["fails"][:5]})
        if progress:
            progress(min(i + size, len(keys)), len(keys))
    project.log("verify_skipped", {"ok": ok, "manual": bad, "stopped": stopped})
    return {"ok": ok, "manual": bad, "stopped": stopped}


# ---------- 생략한 AI 검증을 뒤에서 실행(화면을 옮겨도 계속, 중지 가능 — 담당자 피드백 2026-11-06) ----------
VERIFY_JOBS: dict[str, dict] = {}


def start_verify_job(project: Project, settings, keys: list[str]) -> dict:
    import threading
    job = VERIFY_JOBS.get(project.slug)
    if job and job["status"] == "running":
        return job
    job = {"status": "running", "done": 0, "total": len(keys), "ok": 0, "manual": 0, "stop": False, "error": ""}
    VERIFY_JOBS[project.slug] = job

    def run():
        try:
            r = verify_skipped(project, settings, keys,
                               progress=lambda i, t: job.update(done=i),
                               should_stop=lambda: job["stop"])
            job.update(ok=r["ok"], manual=r["manual"], status="stopped" if r["stopped"] else "done")
        except Exception as e:  # noqa: BLE001
            job.update(status="error", error=str(e)[:300])
    threading.Thread(target=run, daemon=True, name=f"verify_{project.slug}").start()
    return job


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
    if item["status"] not in ("deleted", "hold"):
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
