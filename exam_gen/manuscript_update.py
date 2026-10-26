"""원고 업데이트 반영(담당자 협의 2026-10-23).

원칙: 원고를 새 판으로 교체할 때는 **토큰 0** — 쪽 짝짓기와 근거 문장 재검색으로 쪽수를 고치고, 바뀐 문항은 표시만 한다.
AI는 사용자가 '변경 문항 일괄 처리'(또는 문항별 AI 수정)를 누를 때만 쓴다. 비교 기준은 언제나 **바로 전 판**이다
(문항의 근거 인용문이 그 문항이 맞춰진 판의 문장이므로, 이미 고친 문항은 다시 표시되지 않는다).

판 기록: 원고 source에 version(1, 2, 3 …)과 versions(이력), 문항에 manuscript_version.
이전 판은 pdf/{sid}_v{n}.pdf, runs/manuscript/{sid}_v{n}_pages.json으로 보관한다.
"""
from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import pymupdf

from .ingest import convert, page_map
from .ingest.pdf_text import detect_ocr, extract_lines, normalize
from .store import _atomic_write, _sha256, now

FLAG_KO = {"changed": "내용 변경", "removed": "내용 삭제"}
CALLS = {"changed": 3, "removed": 4}  # 문항당 예상 AI 호출(수정: 생성1+풀이1+검수1, 재출제: 재시도 포함 추정)


def _norm_lines(lines: list[dict]) -> list[str]:
    return [n for n in (normalize(l["text"]) for l in lines if not l.get("margin")) if len(n) >= 4]


def pair_pages(old: list[list[str]], new: list[list[str]], threshold: float = 0.3) -> dict[int, int]:
    """이전 판 쪽 ↔ 새 판 쪽을 본문(정규화한 줄 묶음) 유사도로 짝짓는다. 쪽이 밀리거나 당겨져도 따라간다.
    순서를 거스르는 짝은 피하도록, 바로 앞 짝 이후의 쪽을 먼저 본다."""
    def sim(a, b):
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    out, used, last = {}, set(), -1
    for i, a in enumerate(old):
        scores = sorted(((sim(a, b), j) for j, b in enumerate(new) if j not in used), reverse=True)
        if not scores or scores[0][0] < threshold:
            continue
        best = scores[0]
        ahead = [x for x in scores if x[1] > last and x[0] >= best[0] - 0.05]  # 거의 같은 점수면 순서에 맞는 쪽
        s, j = ahead[0] if ahead else best
        out[i], last = j, j
        used.add(j)
    return out


def _candidate(project, g: int | None, quote: str) -> str:
    """사라진 근거 문장과 가장 비슷한 새 판 문장(짝지은 쪽 ±1에서, 비슷한 것이 없으면 '')."""
    if g is None:
        return ""
    q = normalize(quote)
    best, score = "", 0.0
    for gg in (g, g - 1, g + 1):
        if not 0 <= gg < len(project.pages):
            continue
        lines = [l["text"] for l in project.pages[gg]["lines"] if not l.get("margin")]
        for i in range(len(lines)):
            for w in (1, 2, 3):
                cand = " ".join(lines[i:i + w])
                r = difflib.SequenceMatcher(None, q, normalize(cand)).ratio()
                if r > score:
                    best, score = cand, r
    return best.strip() if score >= 0.55 else ""


def relocate_item(project, it: dict, remap) -> bool:
    """새 판에서 문항의 근거 인용문을 다시 찾는다(store._remap이 부른다). 반환: '원고 변경'으로 표시했는가.
    - 모두 찾으면: 쪽수·근거 위치만 고친다. 문항 내용이 그대로라 검증·승인 유지.
    - 못 찾은 근거가 있으면: 비슷한 새 문장이 있으면 '내용 변경', 없으면 '내용 삭제'로 표시하고 검증·승인을 푼다."""
    from .validate import evidence
    version = max((s.get("version", 1) for s in project.meta["sources"]), default=1)
    lost = []
    new_page = remap(it.get("page"))
    for ev in it.get("evidence") or []:
        m = ev.get("match")
        quote = ev.get("quote") or ""
        if not m or not quote:
            continue
        hint = remap(m.get("g"))
        hint = hint if hint is not None else new_page
        nm = evidence.locate(project.pages, quote, hint)
        if nm and (nm["level"] == "L2" or m.get("level") == "L1"):
            ev["match"] = nm
            continue
        cand = " ".join(l["text"] for l in project.pages[nm["g"]]["lines"] if l["no"] in nm["lines"]) if nm else ""
        cand = cand or _candidate(project, hint, quote)
        ev["match"] = None
        lost.append({"target": ev.get("target"), "is_true": bool(ev.get("is_true")), "old": quote, "new": cand,
                     "g": hint})
    it["source_pages"] = sorted({e["match"]["g"] for e in it.get("evidence") or [] if e.get("match")})
    if new_page is None and it["source_pages"]:
        new_page = it["source_pages"][0]
    if new_page is None:  # 출제 쪽이 통째로 사라짐 → 사라진 근거의 짝 쪽이나 원래 쪽 번호 근처
        new_page = next((x["g"] for x in lost if x["g"] is not None), None)
    it["page"] = new_page
    if it.get("slot"):
        it["slot"]["page"] = new_page
        if new_page is not None:
            it["slot"]["label"] = project.pages[new_page]["label"]
    v = it.setdefault("validation", {})
    if not lost:
        it["manuscript_version"] = version
        return False
    prev = (v.get("ms_change") or {}).get("prev_status") or it.get("status")
    kind = "changed" if all(x["new"] for x in lost) else "removed"
    v["ms_change"] = {"version": version, "kind": kind, "details": lost, "prev_status": prev, "at": now()}
    msg = f"원고 변경({version}판): " + "; ".join(
        f"근거[{x['target']}] " + (f"'{x['old'][:30]}…' → '{x['new'][:30]}…'" if x["new"] else f"'{x['old'][:30]}…' 삭제됨")
        for x in lost)
    v["fails"] = [f for f in v.get("fails") or [] if not f.startswith("원고 변경")] + [msg]
    v.pop("verified", None)
    it.pop("approved", None)
    it["status"] = "manual"
    return True


def replace_source(project, sid: str, new_file: Path, filename: str | None = None) -> dict:
    """원고를 새 판으로 교체한다(원고 ID 유지). 토큰 0. 반환: 변경 보고서 요약."""
    from .backup import backup
    filename = filename or Path(new_file).name
    backup(project, "원고 교체 전")
    meta = project.meta
    s = next(x for x in meta["sources"] if x["id"] == sid)
    v_old = s.get("version", 1)
    v_new = v_old + 1
    vdir = project.dir / "runs" / "manuscript"
    vdir.mkdir(parents=True, exist_ok=True)
    old_pages = [p for p in project.pages if p["src"] == sid]
    old_lines = [_norm_lines(p["lines"]) for p in old_pages]
    _atomic_write(vdir / f"{sid}_v{v_old}_pages.json",
                  json.dumps([{"idx": p["idx"], "label": p["label"], "chasi": p["chasi"], "excluded": p["excluded"],
                               "text": [l["text"] for l in p["lines"] if not l.get("margin")]} for p in old_pages],
                             ensure_ascii=False))
    shutil.copyfile(project.dir / s["pdf"], project.dir / "pdf" / f"{sid}_v{v_old}.pdf")  # 이전 판 보관

    dst = project.dir / "sources" / f"{sid}_v{v_new}_{filename}"
    shutil.copyfile(new_file, dst)
    tmp_pdf = project.dir / "pdf" / f"{sid}_v{v_new}_new.pdf"
    convert.to_pdf(dst, tmp_pdf)
    doc = pymupdf.open(tmp_pdf)
    new_lines_raw = [extract_lines(pg) for pg in doc]
    new_lines = [_norm_lines(ls) for ls in new_lines_raw]
    mapping = page_map.detect_offset(new_lines_raw)
    mapping["mode"] = "printed" if mapping["confidence"] >= 0.6 else "pdf"
    if mapping["mode"] == "pdf":
        mapping["offset"] = 1
    n_pages, ocr = len(doc), detect_ocr(doc)
    doc.close()
    shutil.move(str(tmp_pdf), str(project.dir / s["pdf"]))

    pairs = pair_pages(old_lines, new_lines)
    old_cfg = s.get("page_map") or {}
    same_map = old_cfg.get("mode") == mapping["mode"] and old_cfg.get("offset") == mapping["offset"]
    s.update(filename=filename, pages=n_pages, ocr=ocr, sha256=_sha256(project.dir / s["pdf"]), version=v_new,
             page_map=mapping if not same_map else old_cfg,
             page_map_confirmed=bool(s.get("page_map_confirmed")) and same_map)
    s.setdefault("versions", [{"version": v_old, "filename": None, "at": meta.get("created")}])
    s["versions"].append({"version": v_new, "filename": filename, "at": now()})
    project.save_meta(meta)

    flagged = project.rebuild_manuscript(idx_map={sid: {old_pages[i]["idx"]: j for i, j in pairs.items()}}, relocate=sid)

    # 변경 보고서(토큰 0)
    new_pages = [p for p in project.pages if p["src"] == sid]
    changed_pages, deleted_pages = [], []
    for i, op in enumerate(old_pages):
        if i not in pairs:
            deleted_pages.append(op["label"])
            continue
        a, b = old_lines[i], new_lines[pairs[i]]
        removed = [x for x in a if x not in set(b)]
        added = [x for x in b if x not in set(a)]
        if removed or added:
            changed_pages.append({"old": op["label"], "new": new_pages[pairs[i]]["label"],
                                  "removed": len(removed), "added": len(added)})
    added_pages = [new_pages[j]["label"] for j in range(len(new_pages)) if j not in set(pairs.values())]
    items = project.items()
    kinds = {"changed": 0, "removed": 0}
    for it in items:
        ms = (it.get("validation") or {}).get("ms_change")
        if ms and ms.get("version") == v_new:
            kinds[ms["kind"]] += 1
    report = {"sid": sid, "version": v_new, "filename": filename, "at": now(), "pages_old": len(old_pages),
              "pages_new": len(new_pages), "changed_pages": changed_pages, "deleted_pages": deleted_pages,
              "added_pages": added_pages, "flagged": flagged, "items_changed": kinds["changed"],
              "items_removed": kinds["removed"], "page_map_reconfirm": not same_map}
    _atomic_write(vdir / f"{sid}_v{v_new}_report.json", json.dumps(report, ensure_ascii=False, indent=1))
    project.log("manuscript_replace", {k: report[k] for k in ("sid", "version", "flagged", "items_changed",
                                                                "items_removed")})
    return report


def last_report(project) -> dict | None:
    vdir = project.dir / "runs" / "manuscript"
    files = sorted(vdir.glob("*_report.json"), key=lambda f: f.stat().st_mtime) if vdir.exists() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def flagged_items(project) -> list[dict]:
    return [it for it in project.items() if (it.get("validation") or {}).get("ms_change")
            and it.get("status") != "deleted"]


def estimate(items: list[dict]) -> dict:
    n = {"changed": 0, "removed": 0}
    for it in items:
        n[it["validation"]["ms_change"]["kind"]] += 1
    calls = n["changed"] * CALLS["changed"] + n["removed"] * CALLS["removed"]
    return {"revise": n["changed"], "regenerate": n["removed"], "calls": calls, "minutes": max(1, round(calls * 10 / 60))}


def _render_old(it: dict) -> str:
    lines = [f"발문: {it.get('stem', '')}"]
    lines += [f"  {s['label']}. {s['text']}" for s in it.get("statements") or []]
    lines += [f"  {i}) {c['text']}" for i, c in enumerate(it.get("choices") or [], 1)]
    lines.append(f"정답: {it.get('answer')}")
    lines.append(f"해설: {it.get('explanation', '')}")
    return "\n".join(lines)


def revise_slots(project, keys: list[str]) -> list[dict]:
    """'원고 변경' 문항을 같은 슬롯(유형·난이도·정답 위치)으로 다시 출제할 슬롯 목록. 문항 키는 유지된다.
    내용 변경 → 바뀐 부분만 고치는 수정, 내용 삭제 → 같은 슬롯으로 재출제."""
    out = []
    start = project.next_sid_number()
    for n, key in enumerate(keys):
        it = project.item(key)
        ms = (it.get("validation") or {}).get("ms_change")
        if not ms or it.get("page") is None or not it.get("slot"):
            continue
        s = dict(it["slot"])
        s.update(sid=f"S{start + n:04d}", page=it["page"], label=project.pages[it["page"]]["label"],
                 chasi=project.pages[it["page"]]["chasi"], revise_of=key)
        s["revise"] = {"mode": "revise" if ms["kind"] == "changed" else "regenerate", "version": ms["version"],
                       "old": _render_old(it),
                       "changes": [{"old": d["old"], "new": d["new"]} for d in ms["details"]]}
        out.append(s)
    return out


def start_revise(project, settings, keys: list[str], background: bool = True) -> int | None:
    from .config import criteria
    from .generate.generator import start_batch
    slots = revise_slots(project, keys)
    if not slots:
        return None
    opts = {**criteria()["defaults"], **project.meta.get("options", {})}
    return start_batch(project, settings, slots, opts, kind="revise", background=background)

