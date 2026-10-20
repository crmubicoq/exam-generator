"""엑셀 출력 — `엑셀_출력 형식(양식).xlsx`를 복사해 머리글(1~3행, 병합)을 유지하고 4행부터 채운다.

시트 구성 (평가문항 출제 유의사항)
  1) 최종 제출 시트(양식 시트 이름 그대로): 승인 문항만, 수정 표시 없음
  2) '검수용(수정표시)' 시트 — 숨김: 삭제=회색, 수정=노란 셀+빨간 글씨, 수동 확인 포함
  3) '검증리포트' 시트: 분포·커버리지·관문 통계
양식 오른쪽 숨김 열(R~Y): 문항키·난이도·근거 원문·배치·상태·검증 메모·근거PDF 라벨·허용 정답
"""
from __future__ import annotations

from collections import Counter
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..config import TEMPLATE_XLSX, criteria, type_name
from ..ingest.page_map import format_pages
from ..plan.coverage import coverage_stats
from . import numbering

HIDDEN_COLS = ["문항키", "난이도", "근거 원문", "배치", "상태", "검증 메모", "근거PDF 라벨", "허용 정답"]
FIRST_HIDDEN = 18  # R
GRAY = PatternFill("solid", fgColor="D9D9D9")
YELLOW = PatternFill("solid", fgColor="FFFF00")
RED = Font(color="FF0000")
STATUS_KO = {"approved": "승인", "auto_pass": "자동통과", "warn": "경고", "manual": "수동확인", "deleted": "삭제"}


def stem_text(item: dict, prefix: str = "") -> str:
    s = item.get("stem", "")
    if prefix and not s.startswith("["):
        s = f"[{prefix}] {s}"
    if item.get("statements"):
        s += "\n" + "\n".join(f"{st['label']}. {st['text']}" for st in item["statements"])
    return s


def answer_cell(item: dict):
    if item["type"] == "ox":
        return 1 if item["answer"] == "O" else 2
    if item["type"] == "mcq":
        return int(item["answer"])
    return item["answer"]


def page_cell(project, item: dict) -> str:
    gs = item.get("source_pages") or [item["page"]]
    return format_pages([project.pages[g]["label"] for g in gs])


def row_values(project, item: dict, opts: dict) -> list:
    choices = [c["text"] for c in item.get("choices") or []]
    if item["type"] == "ox":
        choices = ["O", "X", None, None]
    elif item["type"] == "short":
        choices = [None] * 4
    choices = (choices + [None] * 4)[:4]
    prefix = project.meta.get("subject_tag", "") if opts.get("subject_prefix") else ""
    return [item.get("chasi"), type_name(item["type"]), page_cell(project, item), item["no"],
            stem_text(item, prefix), *choices, answer_cell(item), item.get("explanation", ""),
            opts.get("scoring", "시스템 자동채점"), opts.get("score", 5), None, None, None, None]


def hidden_values(project, item: dict) -> list:
    quotes = []
    for ev in item.get("evidence") or []:
        m = ev.get("match")
        if m:
            quotes.append(f"[p.{m['label']}·{ev.get('target')}·{'참' if ev.get('is_true') else '원문'}] {ev.get('quote', '')}")
    v = item.get("validation", {})
    memo = "; ".join(v.get("fails", []) + v.get("warnings", []))[:2000]
    return [item["key"], item.get("difficulty"), "\n".join(quotes)[:4000], item.get("batch"),
            STATUS_KO.get(item.get("status"), item.get("status")), memo, item.get("qlabel"),
            "{^}".join(item.get("short_variants") or [])]


def write(project, items_all: list[dict], out_path: Path, *, draft: bool = False, batch: int | None = None) -> dict:
    opts = {**criteria()["defaults"], **project.meta.get("options", {})}
    final = numbering.number(numbering.select(items_all, draft=draft, batch=batch), opts.get("numbering", "continuous"))
    no_by_key = {it["key"]: it for it in final}

    wb = load_workbook(TEMPLATE_XLSX)
    ws = wb.worksheets[0]
    styles = [copy(ws.cell(row=4, column=c)._style) for c in range(1, 18)]
    head_style = copy(ws.cell(row=2, column=1)._style)
    sub_style = copy(ws.cell(row=3, column=5)._style)
    height = ws.row_dimensions[4].height
    if ws.max_row >= 4:
        ws.delete_rows(4, ws.max_row - 3)
    for i, name in enumerate(HIDDEN_COLS):
        col = FIRST_HIDDEN + i
        ws.cell(row=2, column=col, value=name)._style = copy(head_style)
        ws.cell(row=3, column=col)._style = copy(sub_style)
        ws.column_dimensions[get_column_letter(col)].width = 18
    review = wb.copy_worksheet(ws)
    review.title = "검수용(수정표시)"

    # 1) 최종 시트
    for r, it in enumerate(final, start=4):
        _write_row(ws, r, row_values(project, it, opts) + hidden_values(project, it), styles, height)
    for i in range(len(HIDDEN_COLS)):
        ws.column_dimensions[get_column_letter(FIRST_HIDDEN + i)].hidden = True
    if draft:
        ws["A1"] = f"{ws['A1'].value or ''}  ※ 초안 — 사람 승인 전 문항 포함"

    # 2) 검수용 시트: 최종 문항 + 삭제·수동확인 문항(맨 뒤)
    others = [it for it in items_all if it["key"] not in no_by_key and (batch is None or it.get("batch") == batch)]
    others = numbering.number(others, "continuous")
    r = 4
    for it in final + others:
        vals = row_values(project, it, opts) + hidden_values(project, it)
        if it["key"] not in no_by_key:
            vals[3] = None
        _write_row(review, r, vals, styles, height)
        if it.get("status") in ("deleted", "manual"):
            for c in range(1, 14):
                review.cell(row=r, column=c).fill = GRAY
        else:
            for c in _changed_cols(it):
                cell = review.cell(row=r, column=c)
                cell.fill = YELLOW
                cell.font = Font(name=cell.font.name, size=cell.font.size, color="FF0000")
        r += 1
    review.sheet_state = "hidden"

    _report(wb.create_sheet("검증리포트"), project, items_all, final)
    wb.active = 0
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"path": str(out_path), "count": len(final), "numbered": final}


def _write_row(ws, r, vals, styles, height):
    for c, v in enumerate(vals, start=1):
        cell = ws.cell(row=r, column=c, value=v)
        if c <= len(styles):
            cell._style = copy(styles[c - 1])
        else:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    if height:
        ws.row_dimensions[r].height = None  # 줄바꿈 내용에 맞게 자동 높이


def _changed_cols(it: dict) -> list[int]:
    o = it.get("original")
    if not o:
        return []
    cols = []
    if o.get("stem") != it.get("stem") or o.get("statements") != it.get("statements"):
        cols.append(5)
    oc, nc = o.get("choices") or [], it.get("choices") or []
    for i in range(4):
        a = oc[i]["text"] if i < len(oc) else None
        b = nc[i]["text"] if i < len(nc) else None
        if a != b:
            cols.append(6 + i)
    if str(o.get("answer")) != str(it.get("answer")):
        cols.append(10)
    if o.get("explanation") != it.get("explanation"):
        cols.append(11)
    return cols


def _report(ws, project, items_all, final):
    bold = Font(bold=True)
    rows = [["검증 리포트", project.meta["name"]], []]
    st = Counter(it.get("status") for it in items_all)
    rows.append(["상태별 문항 수"])
    rows += [[STATUS_KO.get(k, k), v] for k, v in st.items()]
    rows += [["최종 출력 문항", len(final)], []]
    opts = {**criteria()["defaults"], **project.meta.get("options", {})}
    n = len(final) or 1
    rows.append(["난이도", "목표 %", "실제 수", "실제 %"])
    dc = Counter(it["difficulty"] for it in final)
    for d in criteria()["difficulty_levels"]:
        rows.append([d, opts["difficulty_pct"].get(d, 0), dc.get(d, 0), round(dc.get(d, 0) * 100 / n, 1)])
    rows.append([])
    rows.append(["유형", "목표 %", "실제 수", "실제 %"])
    tc = Counter(it["type"] for it in final)
    for t in criteria()["type_order"]:
        rows.append([type_name(t), opts["type_pct"].get(t, 0), tc.get(t, 0), round(tc.get(t, 0) * 100 / n, 1)])
    rows.append([])
    rows.append(["선다형 정답 위치", *[f"{k}번" for k in "1234"]])
    ac = Counter(str(it["answer"]) for it in final if it["type"] == "mcq")
    rows.append(["", *[ac.get(k, 0) for k in "1234"]])
    oc = Counter(it["answer"] for it in final if it["type"] == "ox")
    rows.append(["진위형 O / X", oc.get("O", 0), oc.get("X", 0)])
    lv = Counter(it.get("evidence_level") for it in final)
    rows.append(["근거 매칭 수준", f"L2(원문 일치) {lv.get('L2', 0)}", f"L1(부분 일치) {lv.get('L1', 0)}"])
    rows.append([])
    counts = Counter(it["page"] for it in final)
    cov = coverage_stats(project.pages, counts)
    rows.append(["페이지 커버리지", "책 쪽수", "차시", "글자 수", "기대 문항", "실제 문항", "판정"])
    for p in project.pages:
        if p["excluded"]:
            rows.append(["", p["label"], p["chasi"], p["chars"], "-", "-", "출제 제외"])
            continue
        flag = "0문항" if p["g"] in cov["zero"] else "과밀" if p["g"] in cov["dense"] else ""
        rows.append(["", p["label"], p["chasi"], p["chars"], round(cov["expected"].get(p["g"], 0), 1),
                     counts.get(p["g"], 0), flag])
    rows.append([])
    rows.append(["배치", "종류", "상태", "통과", "수동확인", "관문별 탈락(시도 기준)"])
    for b in project.batches():
        s = b["summary"] or {}
        rows.append([b["batch"], b["kind"], b["status"], s.get("accepted"), s.get("manual"),
                     ", ".join(f"{k}:{v}" for k, v in (s.get("gate_fail") or {}).items() if v)])
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows():
        if row and isinstance(row[0].value, str) and row[0].value and len(row) > 1 and row[1].value in ("목표 %", "책 쪽수", "종류"):
            for c in row:
                c.font = bold
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["G"].width = 40
