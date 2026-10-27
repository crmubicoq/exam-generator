"""기출문제 등록 — 이미 출제·서비스한 문항을 받아 추가 출제에서 제외한다(담당자 협의 2026-10-23).

- 제출 양식 엑셀(이 도구가 출력하는 양식): 프로그램이 바로 읽는다(토큰 0).
- 그 밖의 형식(다른 엑셀·CSV·한글·워드·PDF): AI가 읽어 표준 문항 형식으로 통일한다(구조화 출력).
- 저장 전에 화면에서 사람이 확인·수정한다. 저장한 문항은 상태 'prior'(기출)로, 출력·검수·통계에서는 빠지고
  추가 출제의 중복 검사(G6)와 같은 쪽 회피 목록에만 쓰인다.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from ..llm.prompts import render
from ..store import now
from .pdf_text import normalize

TYPE_FROM_KO = {"선다형": "mcq", "진위형": "ox", "단답형": "short"}
CHUNK_CHARS = 6000  # AI 한 번에 넘기는 글자 수(문항 약 20~30개)

STD_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "required": ["type", "stem", "statements", "choices", "answer", "explanation"],
    "properties": {
        "type": {"type": "string", "enum": ["mcq", "ox", "short"]},
        "stem": {"type": "string"},
        "statements": {"type": "array", "items": {"type": "string"}},
        "choices": {"type": "array", "items": {"type": "string"}},
        "answer": {"type": "string"},
        "explanation": {"type": "string"}}}}}}


# ---------- 제출 양식 엑셀 ----------
def is_template_excel(path: Path) -> bool:
    if Path(path).suffix.lower() not in (".xlsx", ".xlsm"):
        return False
    from openpyxl import load_workbook
    try:
        ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        head = " ".join(str(c or "") for row in ws.iter_rows(min_row=1, max_row=3, values_only=True) for c in row)
    except Exception:  # noqa: BLE001
        return False
    return all(k in head for k in ("문항유형", "평가문항")) and ("보기1" in head or "문제" in head)


_STMT = re.compile(r"^\s*([ㄱㄴㄷㄹㅁ])\s*[.)]\s*(.+)$")
_VAR = re.compile(r"\s*\((?:유사답변|허용 답안)\s*:\s*([^)]*)\)\s*$")


def parse_template_excel(path: Path) -> list[dict]:
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True).worksheets[0]
    out = []
    for r, row in enumerate(ws.iter_rows(min_row=4, values_only=True), start=4):
        cells = list(row) + [None] * 13
        tko, stem = str(cells[1] or "").strip(), str(cells[4] or "").strip()
        if not stem:
            continue
        t = TYPE_FROM_KO.get(tko, "mcq" if any(cells[5:9]) else "short")
        lines = stem.splitlines()
        stmts = [m.group(2).strip() for ln in lines[1:] if (m := _STMT.match(ln))]
        if stmts:
            stem = lines[0].strip()
        ans = str(cells[9] if cells[9] is not None else "").strip()
        variants: list[str] = []
        if t == "ox":
            ans = {"1": "O", "2": "X"}.get(ans, ans.upper())
        elif t == "short" and (m := _VAR.search(ans)):
            variants = [v.strip() for v in m.group(1).split(",") if v.strip()]
            ans = _VAR.sub("", ans).strip()
        out.append({"type": t, "stem": stem, "statements": stmts,
                    "choices": [str(c).strip() for c in cells[5:9] if c not in (None, "")] if t == "mcq" else [],
                    "answer": ans, "short_variants": variants, "explanation": str(cells[10] or "").strip(),
                    "chasi_given": cells[0], "row": r})
    return out


# ---------- 그 밖의 형식: 텍스트로 펼친 뒤 AI가 통일 ----------
def file_to_text(path: Path) -> str:
    path = Path(path)
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xlsm", ".xls"):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        rows = []
        for ws in wb.worksheets:
            for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                vals = [str(v).strip() for v in row if v not in (None, "")]
                if vals:
                    rows.append(f"[{ws.title} {r}행] " + " | ".join(vals))
        return "\n".join(rows)
    if suf in (".csv", ".txt"):
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if suf == ".txt":
            return text
        return "\n".join(f"[{i}행] " + " | ".join(c.strip() for c in row if c.strip())
                         for i, row in enumerate(csv.reader(text.splitlines()), start=1) if any(c.strip() for c in row))
    # 한글·워드·PDF 등: 기존 변환기로 PDF를 만든 뒤 글자를 뽑는다
    import pymupdf

    from .convert import to_pdf
    pdf = path if suf == ".pdf" else to_pdf(path, path.with_suffix(".prior.pdf"))
    with pymupdf.open(pdf) as d:
        return "\n".join(pg.get_text() for pg in d)


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """줄 단위로 끊어 size 글자 안팎으로 나눈다(문항이 중간에서 잘리지 않게 빈 줄·행 경계 우선)."""
    out, cur = [], ""
    for ln in text.splitlines():
        if len(cur) + len(ln) > size and cur:
            out.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur.strip():
        out.append(cur)
    return out


def estimate_calls(text: str) -> int:
    return max(1, math.ceil(len(text) / CHUNK_CHARS))


def ai_extract(client, settings, text: str, progress=None) -> list[dict]:
    parts = chunks(text)
    out = []
    for i, part in enumerate(parts, 1):
        res = client.structured(render("prior_extract_system.j2"), "# 자료\n" + part, STD_SCHEMA,
                                model=settings.model_for("generate"), task="prior", context={"text": part})
        for it in res.get("items") or []:
            if not (it.get("stem") or "").strip():
                continue
            out.append({"type": it.get("type", "mcq"), "stem": it["stem"].strip(),
                        "statements": [s.strip() for s in it.get("statements") or [] if s.strip()],
                        "choices": [c.strip() for c in it.get("choices") or [] if c.strip()],
                        "answer": str(it.get("answer", "")).strip(), "short_variants": [],
                        "explanation": (it.get("explanation") or "").strip()})
        if progress:
            progress(i, len(parts))
    return out


# ---------- 원고 쪽 연결·저장 ----------
def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")}


def best_page(project, item: dict) -> int | None:
    """문제·보기·정답 단어가 가장 많이 겹치는 출제 대상 쪽(겹침이 너무 적으면 None)."""
    q = _tokens(" ".join([item.get("stem", ""), *item.get("statements", []), *item.get("choices", []),
                          item.get("answer", ""), item.get("explanation", "")]))
    if not q:
        return None
    best, score = None, 0.0
    for p in project.pages:
        if p["excluded"]:
            continue
        hit = len(q & _tokens(project.page_text(p["g"]))) / len(q)
        if hit > score:
            best, score = p["g"], hit
    return best if score >= 0.35 else None


def relink(project, only_missing: bool = False) -> dict:
    """기출 문항의 원고 쪽 연결을 다시 계산한다(AI 사용 없음).
    원고보다 기출을 먼저 올렸거나, 원고를 추가·교체한 뒤에 쓴다. only_missing이면 쪽이 없는 기출만.
    반환: {"total", "linked", "changed"}"""
    priors = project.items(status=["prior"])
    changed = 0
    for it in priors:
        if only_missing and it.get("page") is not None:
            continue
        g = best_page(project, it)
        chasi = project.pages[g]["chasi"] if g is not None else None
        if (g, chasi) != (it.get("page"), it.get("chasi")):
            it.update(page=g, chasi=chasi, source_pages=[g] if g is not None else [])
            project.save_item(it, "prior_relink", {"page": g})
            changed += 1
    return {"total": len(priors), "linked": sum(1 for it in project.items(status=["prior"]) if it.get("page") is not None),
            "changed": changed}


def problems(item: dict) -> list[str]:
    out = []
    if not item.get("stem"):
        out.append("발문 없음")
    if not str(item.get("answer", "")).strip():
        out.append("정답 없음")
    if item.get("type") == "mcq" and not item.get("statements") and len(item.get("choices") or []) < 2:
        out.append("보기 부족")
    return out


def save(project, items: list[dict], source_name: str) -> int:
    """기출 문항 저장(상태 prior, 키 P00001…). 반환: 저장 수."""
    existing = project.items(status=["prior"])
    seen = {normalize(it.get("stem", "")) for it in existing}
    n = len(existing)
    saved = 0
    for it in items:
        if normalize(it.get("stem", "")) in seen:
            continue  # 같은 기출을 두 번 올린 경우
        seen.add(normalize(it.get("stem", "")))
        n += 1
        g = best_page(project, it)
        choices = [{"text": c, "is_true": None} for c in it.get("choices") or []]
        stmts = [{"label": "ㄱㄴㄷㄹㅁ"[i], "text": s, "is_true": None} for i, s in enumerate(it.get("statements") or [])]
        item = {"key": f"P{n:05d}", "status": "prior", "type": it.get("type", "mcq"), "batch": None,
                "page": g, "chasi": project.pages[g]["chasi"] if g is not None else None,
                "source_pages": [g] if g is not None else [], "stem": it.get("stem", ""), "statements": stmts,
                "choices": choices, "answer": it.get("answer", ""), "short_variants": it.get("short_variants") or [],
                "explanation": it.get("explanation", ""), "key_point": it.get("stem", "")[:120],
                "evidence": [], "difficulty": None, "prior_source": source_name, "created": now(),
                "validation": {"fails": [], "warnings": []}}
        project.save_item(item, "prior_import", {"source": source_name})
        saved += 1
    project.log("prior_import", {"source": source_name, "saved": saved})
    return saved
