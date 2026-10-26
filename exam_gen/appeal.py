"""이의신청 대응 — 서비스된 문항을 빨리 찾고(모든 프로젝트), 근거·검증 기록을 한 번에 보고, 처리 결과를 남긴다. 토큰 0.

찾는 방법
- 문항 내용 일부 붙여넣기: 발문·진술·보기·정답·해설을 띄어쓰기·문장부호 없이 비교한다(현재 내용 + 출력 당시 스냅숏).
- 문항키(K00012): 엑셀 숨김 R열 — 서비스 시스템에 함께 보관하면 가장 빠르다.
- 출력 이력 + 문항번호: '그 파일의 7번'.
처리 기록은 프로젝트 runs/appeals.jsonl과 문항 이벤트에 남는다.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pymupdf

from . import review_ops
from .config import type_name
from .integrity import content_hash
from .output import export_log
from .output.evidence_pdf import A4, _archive, _css, _e, render_item_page
from .store import _LOCK, Project, list_projects, now
from .validate import pool_review

OUTCOMES = {"accept": "정답 인정(이의 기각 아님 — 신청자 답도 정답)", "multi": "복수 정답 인정",
            "revise": "문항 수정", "discard": "문항 폐기", "reject": "기각(원래 정답 유지)"}
OUTCOME_SHORT = {"accept": "정답 인정", "multi": "복수 정답", "revise": "문항 수정", "discard": "문항 폐기", "reject": "기각"}
ALL_STATUS = ["approved", "auto_pass", "warn", "manual", "deleted", "prior"]
KEY_RE = re.compile(r"^[KP]\d{5}$")
MIN_SCORE = 0.5
VERDICT_KO = {"contradiction": "원고와 모순(오답 확실)", "compatible": "원고와 함께 성립 가능", "not_stated": "원고에 없음",
              "condition_missing": "해석을 더해야 거짓"}


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", str(s or "")).lower()


def item_text(it: dict) -> str:
    parts = [it.get("stem", ""), it.get("answer", ""), it.get("explanation", "")]
    parts += [s.get("text", "") for s in it.get("statements") or []]
    parts += [c.get("text", "") for c in it.get("choices") or []]
    parts += list(it.get("short_variants") or [])
    return " ".join(str(x) for x in parts)


def _grams(s: str, n: int = 3) -> set[str]:
    return {s[i:i + n] for i in range(max(1, len(s) - n + 1))}


def score(query: str, text: str) -> float:
    """붙여넣은 문장이 문항 안에 얼마나 들어 있는지(0~1). 그대로 들어 있으면 1."""
    q, t = _norm(query), _norm(text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    gq = _grams(q)
    return len(gq & _grams(t)) / len(gq)


def search(query: str, slugs: list[str] | None = None, limit: int = 20) -> list[dict]:
    """모든 프로젝트(또는 지정한 프로젝트)에서 문항을 찾는다. 문항키면 정확히, 아니면 내용 유사도로."""
    query = (query or "").strip()
    if not query:
        return []
    is_key = bool(KEY_RE.match(query.upper()))
    out = []
    for slug in slugs or list_projects():
        try:
            p = Project(slug)
            items = p.items(status=ALL_STATUS)
            hist = export_log.history(p)
        except Exception:  # noqa: BLE001 — 읽을 수 없는 프로젝트는 건너뛴다
            continue
        name = p.meta.get("name", slug)
        n_exp = {}
        for e in hist:
            for r in e["items"]:
                n_exp[r["key"]] = n_exp.get(r["key"], 0) + 1
        best: dict[str, tuple[float, str]] = {}
        for it in items:
            s = 1.0 if is_key and it["key"] == query.upper() else (0.0 if is_key else score(query, item_text(it)))
            if s > best.get(it["key"], (0, ""))[0]:
                best[it["key"]] = (s, "현재 내용")
        if not is_key:  # 출력 뒤에 고친 문항은 '출력 당시 내용'으로도 찾는다
            for e in hist:
                for r in e["items"]:
                    if r.get("snap") and r["key"] in best:
                        s = score(query, item_text(r["snap"]))
                        if s > best[r["key"]][0]:
                            best[r["key"]] = (s, f"출력 당시 내용({e['at'][:10]})")
        by_key = {it["key"]: it for it in items}
        for k, (s, where) in best.items():
            if s >= (1.0 if is_key else MIN_SCORE):
                it = by_key[k]
                out.append({"slug": slug, "project": name, "key": k, "score": round(s, 2), "where": where,
                            "status": it.get("status"), "type": it.get("type"), "stem": it.get("stem", ""),
                            "exports": n_exp.get(k, 0)})
    out.sort(key=lambda r: (-r["score"], -r["exports"], r["project"], r["key"]))
    return out[:limit]


def by_number(project, export_id: int, no) -> str | None:
    """출력 이력 id의 문항번호 → 문항키."""
    for e in export_log.history(project):
        if e["id"] == export_id:
            for r in e["items"]:
                if str(r["no"]) == str(no):
                    return r["key"]
    return None


def _path(project) -> Path:
    return project.dir / "runs" / "appeals.jsonl"


def records(project, key: str | None = None) -> list[dict]:
    f = _path(project)
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if key is None or r["key"] == key:
            out.append(r)
    return out[::-1]


def record(project, key: str, outcome: str, memo: str = "", handler: str = "", export_ref: str = "") -> dict:
    """처리 결과를 남긴다. '문항 폐기'는 문항을 삭제 상태로 바꿔 다음 출력부터 빠지게 한다."""
    if outcome not in OUTCOMES:
        raise ValueError(f"알 수 없는 처리 결과: {outcome}")
    it = project.item(key)
    if it is None:
        raise KeyError(key)
    rec = {"at": now(), "key": key, "outcome": outcome, "memo": memo.strip(), "handler": handler.strip(),
           "export_ref": export_ref, "hash": content_hash(it)}
    f = _path(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    project.log("appeal", {k: rec[k] for k in ("outcome", "memo", "handler", "export_ref")}, key=key)
    if outcome == "discard" and it.get("status") != "deleted":
        review_ops.set_status(project, key, "deleted", f"이의신청 폐기: {memo}".strip())
    return rec


def related(project, key: str) -> list[str]:
    """최종 풀 점검에서 같은 원리 묶음으로 묶인 다른 문항 — 함께 점검해야 할 수 있다."""
    for g in (pool_review.last(project) or {}).get("groups", []):
        if key in g.get("keys", []):
            return [k for k in g["keys"] if k != key]
    return []


# ---------- 검토서 PDF ----------
def _item_html(it: dict) -> str:
    out = [f"<div class='box'><b>[{_e(type_name(it['type']))} · {_e(it.get('difficulty'))}]</b> {_e(it.get('stem'))}"]
    for s in it.get("statements") or []:
        out.append(f"<br/>{_e(s['label'])}. {_e(s['text'])}")
    for i, c in enumerate(it.get("choices") or [], 1):
        out.append(f"<br/>{'①②③④⑤'[i - 1]} {_e(c['text'])}")
    ans = it.get("answer")
    if it["type"] == "mcq" and str(ans).isdigit():
        ans = "①②③④⑤"[int(ans) - 1]
    out.append(f"<br/><span class='ans'>정답: {_e(ans)}</span>")
    if it.get("short_variants"):
        out.append(f" (유사답변: {_e(', '.join(it['short_variants']))})")
    if it.get("explanation"):
        out.append(f"<br/>해설: {_e(it['explanation'])}")
    out.append("</div>")
    return "".join(out)


def _validation_html(it: dict) -> str:
    v = it.get("validation") or {}
    rows = []
    g4 = v.get("G4") or {}
    if g4:
        rows.append(f"<tr><td class='k'>G4 독립 풀이</td><td>AI가 정답 없이 푼 답: {_e(g4.get('answer'))}</td></tr>")
        for j in g4.get("judgments") or []:
            rows.append(f"<tr><td class='k'>&nbsp;· {_e(j.get('target'))}</td><td>{'옳음' if j.get('is_true') else '틀림'} — "
                        f"{_e(j.get('reason'))}</td></tr>")
    g5 = v.get("G5") or {}
    for t in g5.get("traps") or []:
        verdict = VERDICT_KO.get(t.get("verdict"), t.get("verdict"))
        rows.append(f"<tr><td class='k'>G5 함정 판정 {_e(t.get('target'))}</td><td>{_e(verdict)} — "
                    f"{_e(t.get('conflict') or t.get('reason'))}</td></tr>")
    if g5.get("assessed_difficulty"):
        rows.append(f"<tr><td class='k'>G5 판정 난이도</td><td>{_e(g5['assessed_difficulty'])}</td></tr>")
    for w in v.get("warnings") or []:
        rows.append(f"<tr><td class='k'>경고</td><td>{_e(w)}</td></tr>")
    for w in v.get("fails") or []:
        rows.append(f"<tr><td class='k'>탈락 사유</td><td>{_e(w)}</td></tr>")
    if v.get("llm_skipped"):
        rows.append("<tr><td class='k'>AI 검증</td><td>빠른 출제로 생략됨</td></tr>")
    return "<table>" + ("".join(rows) or "<tr><td class='k'>검증 기록이 없습니다.</td></tr>") + "</table>"


def review_pdf(project, key: str, out_path: Path) -> dict:
    """이의신청 검토서: 문항, 출력 이력, 검증·판정 근거, 처리 기록, 원고 근거 쪽(하이라이트 이미지)."""
    it = project.item(key)
    h = content_hash(it)
    pages = project.pages
    exps = export_log.item_exports(project, key, h)
    recs = records(project, key)
    labels = [pages[g]["label"] for g in it.get("source_pages") or ([it["page"]] if it.get("page") is not None else [])]
    blocks = [f"<h1>이의신청 검토서</h1><p class='muted'>{_e(project.meta.get('name'))} · 문항키 {_e(key)} · "
              f"원고 p.{_e(', '.join(labels))} · 작성 {_e(now()[:16].replace('T', ' '))}</p>",
              "<h2>문항(현재 내용)</h2>", _item_html(it)]
    changed = [e for e in exps if not e["same"]]
    if changed and changed[0].get("snap"):
        blocks += [f"<h2>출력 당시 내용({_e(changed[0]['at'][:10])} {_e(changed[0]['file'])}) — 현재와 다릅니다</h2>",
                   _item_html({**changed[0]["snap"], "type": changed[0]["snap"].get("type") or it["type"]})]
    blocks.append("<h2>출력 이력</h2><table><tr><th>일시</th><th>출력물</th><th>파일</th><th>번호</th><th>현재와 같음</th></tr>"
                  + "".join(f"<tr><td class='k'>{_e(e['at'][:16].replace('T', ' '))}</td><td>{_e(export_log.KIND_KO.get(e['kind'], e['kind']))}</td>"
                            f"<td>{_e(e['file'])}</td><td>{_e(e['no'])}</td><td>{'예' if e['same'] else '아니요'}</td></tr>"
                            for e in exps)
                  + ("" if exps else "<tr><td colspan='5'>출력 기록이 없습니다.</td></tr>") + "</table>")
    blocks += ["<h2>자동 검증·판정 근거</h2>", _validation_html(it)]
    ap = it.get("approved") or {}
    blocks.append(f"<h2>이력</h2><p>출제 {_e((it.get('created') or '')[:10])} · 원고 {_e(it.get('manuscript_version', 1))}판 · "
                  f"승인 {_e(ap.get('by') or '-')} {_e((ap.get('at') or '')[:10])}</p>")
    rel = related(project, key)
    if rel:
        blocks.append(f"<p>같은 원리로 묶인 다른 문항: {_e(', '.join(rel))} — 함께 점검이 필요할 수 있습니다.</p>")
    blocks.append("<h2>처리 기록</h2><table><tr><th>일시</th><th>결과</th><th>처리자</th><th>메모</th></tr>"
                  + "".join(f"<tr><td class='k'>{_e(r['at'][:16].replace('T', ' '))}</td><td>{_e(OUTCOME_SHORT[r['outcome']])}</td>"
                            f"<td>{_e(r['handler'])}</td><td>{_e(r['memo'])}</td></tr>" for r in recs)
                  + ("" if recs else "<tr><td colspan='4'>아직 처리 기록이 없습니다.</td></tr>") + "</table>")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    story = pymupdf.Story(html="".join(blocks), user_css=_css() + ".k{width:85pt}", archive=_archive())
    mem = io.BytesIO()
    writer = pymupdf.DocumentWriter(mem)
    rect = pymupdf.Rect(0, 0, A4[0], A4[1])
    more = 1
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(rect + (45, 50, -45, -50))
        story.draw(dev)
        writer.end_page()
    writer.close()
    buf = pymupdf.open("pdf", mem.getvalue())
    # 원고 근거 쪽: 정답 근거(노랑)·함정 원문(파랑) 하이라이트를 그린 이미지
    for g in it.get("source_pages") or ([it["page"]] if it.get("page") is not None else []):
        png = render_item_page(project, it, g, dpi=130)
        pg = buf.new_page(width=A4[0], height=A4[1])
        pg.insert_htmlbox(pymupdf.Rect(45, 25, A4[0] - 45, 46),
                          f"<b>원고 p.{_e(pages[g]['label'])}</b> — 노랑: 정답 근거 · 파랑: 함정이 바꾼 원문",
                          css=_css(), archive=_archive())
        pg.insert_image(pymupdf.Rect(45, 48, A4[0] - 45, A4[1] - 40), stream=png, keep_proportion=True)
    buf.save(out_path, garbage=3, deflate=True)
    n = len(buf)
    buf.close()
    project.log("appeal_pdf", {"file": out_path.name}, key=key)
    return {"path": str(out_path), "pages": n}
