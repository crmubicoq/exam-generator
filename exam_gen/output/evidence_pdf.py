"""★2 원고 위 출처 표시 — 기존 `2026_E-TEST_30문항_근거PDF_추출본.pdf` 양식을 재현한다.

extract(문항별 추출본): 표지 → 문항 인덱스 → 사용 안내 → [문항마다 분리자 페이지 + 근거 원본 페이지]
overview(원고 전체 표시본): 원고 각 페이지를 한 번씩 넣고 그 페이지에서 출제된 모든 문항의 근거를 표시

원본 페이지에는 줄 단위 하이라이트(노랑=참 진술의 근거, 주황=함정 진술이 바꾼 원문)와
왼쪽 여백 라벨 Q{번호}-{대상}을 단다 (인계 docs/06 draw_segment 이식).
"""
from __future__ import annotations

import html
from pathlib import Path

import pymupdf

from ..config import criteria, type_name
from ..ingest.page_map import format_pages
from . import export_log, numbering

A4 = (595, 842)


def _css() -> str:
    f = criteria()["fonts"]
    reg, bold = Path(f["regular"]).name, Path(f["bold"]).name
    return (f"@font-face{{font-family:kr;src:url({reg});}}"
            f"@font-face{{font-family:kr;src:url({bold});font-weight:bold;}}"
            "*{font-family:kr;font-size:9.5pt;line-height:1.45;}"
            "h1{font-size:20pt;margin:0 0 6pt 0} h2{font-size:13pt;margin:10pt 0 4pt 0;color:#333}"
            ".muted{color:#666} .box{border:1px solid #999;padding:6pt;margin:4pt 0}"
            "table{border-collapse:collapse;width:100%} td,th{border:1px solid #999;padding:2pt 4pt;font-size:8.5pt}"
            "th{background:#eee} .y{background:#fff3a0} .o{background:#c9ddff} .ans{font-weight:bold;color:#b00}")


def _archive():
    return pymupdf.Archive(str(Path(criteria()["fonts"]["regular"]).parent))


def _html_page(doc, body: str):
    page = doc.new_page(width=A4[0], height=A4[1])
    spare, scale = page.insert_htmlbox(pymupdf.Rect(40, 40, A4[0] - 40, A4[1] - 40), body,
                                       css=_css(), archive=_archive(), scale_low=0.6)
    return page


_GLYPH_FIX = str.maketrans({"․": "·", "‧": "·", "∙": "·", "": "·"})


def _e(s) -> str:
    """HTML 이스케이프 + 맑은고딕에 없는 글리프(원고의 '․' 등)를 가운뎃점으로 바꾼다."""
    return html.escape(str(s or "").translate(_GLYPH_FIX))


def _ml(s) -> str:
    """여러 줄 텍스트(사례 블록이 있는 발문 등) — 줄바꿈을 그대로 살린다."""
    return _e(s).replace(chr(10), "<br>")


def _cover(doc, title: str, items: list[dict], project, mode: str):
    pages = sorted({g for it in items for g in it.get("source_pages") or []})
    labels = format_pages([project.pages[g]["label"] for g in pages])
    kind = "문항별 근거 추출본" if mode == "extract" else "원고 전체 출제 표시본"
    _html_page(doc, f"""<div style="margin-top:220pt;text-align:center">
      <h1>{_e(title)}</h1><h2>근거PDF — {kind}</h2>
      <p>총 {len(items)}문항 · 근거 원고 쪽수 {_e(labels)}</p>
      <p class="muted">{'각 문항별 분리자 페이지 + 해당 원고 원본 페이지' if mode == 'extract'
                        else '원고 각 페이지에 출제된 모든 문항의 근거 위치 표시'}</p></div>""")


def _index(doc, items: list[dict], project):
    per = 32
    for i in range(0, len(items), per):
        rows = "".join(
            f"<tr><td>{_e(it['qlabel'])}</td><td>{_e(it['no'])}</td><td>{_e(it.get('chasi'))}</td>"
            f"<td>{_e(type_name(it['type']))}</td><td>{_e(it.get('difficulty'))}</td>"
            f"<td>{_e(format_pages([project.pages[g]['label'] for g in it.get('source_pages') or []]))}</td>"
            f"<td>{_e(it.get('evidence_level'))}</td></tr>" for it in items[i:i + per])
        _html_page(doc, f"<h2>문항 인덱스 ({i + 1}~{min(i + per, len(items))} / {len(items)})</h2>"
                        "<table><tr><th>라벨</th><th>문항번호</th><th>차시</th><th>유형</th><th>난이도</th>"
                        f"<th>근거 페이지(책 쪽수)</th><th>매칭</th></tr>{rows}</table>")


def _guide(doc):
    _html_page(doc, """<h2>근거PDF 사용 안내</h2>
    <p><b>1. 하이라이트 색상</b></p>
    <table><tr><td class="y" style="width:70pt">노란색</td><td>참인 진술의 근거 — 문항의 옳은 진술(정답 근거)이 원고의 어느 부분에서 나왔는지 표시</td></tr>
    <tr><td class="o">파란색</td><td>함정 진술의 원문 — 원고는 옳은 내용이지만, 함정 보기·X 문항에서 일부를 바꿔 사용한 위치</td></tr></table>
    <p><b>2. 좌측 여백 라벨</b> — 라벨 형식: Q[문항 라벨]-[대상]</p>
    <table><tr><th>문항 유형</th><th>대상 표기</th><th>예시</th></tr>
    <tr><td>선다형(①②③④)</td><td>보기 번호 1~4</td><td>Q12-3 → 12번 문항 ③번 보기의 근거</td></tr>
    <tr><td>선다형 조합형(ㄱㄴㄷㄹ)</td><td>ㄱ→A, ㄴ→B, ㄷ→C, ㄹ→D</td><td>Q28-A → 28번 문항 ㄱ 진술의 근거</td></tr>
    <tr><td>진위형</td><td>OX</td><td>Q40-OX</td></tr><tr><td>단답형</td><td>A</td><td>Q55-A</td></tr></table>
    <p><b>3. 사용 방법</b><br/>① 엑셀의 문항번호(또는 숨김 열 '근거PDF 라벨')로 문항을 찾는다.<br/>
    ② 이 PDF의 문항 분리자 페이지에서 근거 쪽수를 확인한다.<br/>③ 이어지는 원본 페이지에서 라벨과 하이라이트로 정확한 근거 문장을 확인한다.</p>
    <p><b>4. 유의사항</b><br/>• 라벨이 영문·숫자인 것은 PDF 텍스트 호환성 때문이다.<br/>
    • 주황색 부분은 그 자체가 거짓이 아니라, 그 내용을 일부 바꿔 함정 진술을 만들었다는 의미이다.<br/>
    • 매칭 L2는 원문과 글자 단위 일치, L1은 띄어쓰기·줄바꿈·OCR 차이를 제외하고 80% 이상 일치를 뜻한다.</p>""")


def _separator(doc, it: dict, project):
    pages = format_pages([project.pages[g]["label"] for g in it.get("source_pages") or []])
    st = "".join(f"<div>{_e(s['label'])}. {_e(s['text'])}</div>" for s in it.get("statements") or [])
    if it["type"] == "mcq":
        ch = "".join(f"<div{' class=\"ans\"' if str(i) == str(it['answer']) else ''}>{'①②③④'[i - 1]} {_e(c['text'])}</div>"
                     for i, c in enumerate(it.get("choices") or [], 1))
    elif it["type"] == "ox":
        ch = "<div>O / X</div>"
    else:
        ch = ""
    ans = {"ox": it["answer"], "short": it["answer"]}.get(it["type"], "①②③④"[int(it["answer"]) - 1] if it["type"] == "mcq" else "")
    if it["type"] == "short" and it.get("short_variants"):
        ans += f" (유사답변: {', '.join(it['short_variants'])})"
    warn = ""
    if it.get("status") == "manual":
        warn = "<p class='ans'>※ 자동 검증 탈락(수동확인) 문항 — 검수 전</p>"
    if not any(ev.get("match") for ev in it.get("evidence") or []):
        warn += "<p class='ans'>※ 근거 미확인 — 원고에서 근거 문장을 찾지 못했다</p>"
    evs = []
    for ev in it.get("evidence") or []:
        m = ev.get("match")
        if not ev.get("quote"):
            continue
        cls = "y" if ev.get("is_true") else "o"
        where = f"p.{m['label']} ({m['level']})" if m else "찾지 못함"
        lab = f"{it['qlabel']}-{numbering.target_label(it, str(ev.get('target')))}"
        note = f" — {_e(ev.get('trap_note'))}" if ev.get("trap_note") and not ev.get("is_true") else ""
        evs.append(f"<tr><td>{_e(lab)}</td><td class='{cls}'>{_e(ev['quote'])}{note}</td><td>{_e(where)}</td></tr>")
    _html_page(doc, f"""<h1>{_e(it['qlabel'])} · 문항 {_e(it['no'])}</h1>
      <p class="muted">{_e(it.get('chasi'))}차시 · {_e(type_name(it['type']))} · 난이도 {_e(it.get('difficulty'))} · 문항키 {_e(it['key'])}</p>
      {warn}<h2>출처</h2><p>원고 p.{_e(pages) or '확인 안 됨'}</p>
      <h2>발문</h2><div class="box">{_ml(it['stem'])}{st}</div>
      {'<h2>보기</h2><div class="box">' + ch + '</div>' if ch else ''}
      <h2>정답</h2><p class="ans">{_e(ans)}</p>
      <h2>해설</h2><p>{_e(it.get('explanation'))}</p>
      <h2>근거</h2><table><tr><th>라벨</th><th>근거 원문</th><th>위치</th></tr>{''.join(evs)}</table>
      <p class="muted">→ 다음 페이지부터 해당 원고 원본 페이지가 첨부된다.</p>""")


def _segments(it: dict, g: int) -> list[dict]:
    out = []
    for ev in it.get("evidence") or []:
        m = ev.get("match")
        if m and m["g"] == g:
            out.append({"bboxes": m["bboxes"], "correct": bool(ev.get("is_true")),
                        "label": f"{it['qlabel']}-{numbering.target_label(it, str(ev.get('target')))}"})
    return out


def _draw(page, segs: list[dict], used_y: list[float]):
    c = criteria()["colors"]
    for seg in segs:
        color = c["correct"] if seg["correct"] else c["wrong"]
        for bb in seg["bboxes"]:
            a = page.add_highlight_annot(pymupdf.Rect(bb))
            a.set_colors(stroke=color)
            a.set_opacity(c["opacity"])
            a.update()
    used: dict[float, list[float]] = {}  # 열(x) → 사용한 y
    H, W = page.rect.height, page.rect.width
    for seg in sorted(segs, key=lambda s: s["bboxes"][0][1]):
        tc = c["label_correct"] if seg["correct"] else c["label_wrong"]
        bg = c["bg_correct"] if seg["correct"] else c["bg_wrong"]
        b = seg["bboxes"][0]
        w = 8 + 4.6 * len(seg["label"])
        target = b[1] + (b[3] - b[1]) / 2
        x, y = _label_slot(used, target, b[0], w, W, H)
        r = pymupdf.Rect(x, y - 6, x + w, y + 6)
        if abs(y - target) > 6 or x > b[0]:  # 줄에서 떨어진 라벨은 연결선으로 위치를 이어 준다 (Codex C17)
            end = pymupdf.Point(b[0], target) if x < b[0] else pymupdf.Point(b[2], target)
            page.draw_line(pymupdf.Point(x + w if x < b[0] else x, y), end, color=tc, width=0.4)
        page.draw_rect(r, color=tc, fill=bg, width=0.6)
        page.insert_text(pymupdf.Point(x + 4, y + 2.4), seg["label"], fontname="helv", fontsize=6.8, color=tc)


def _label_slot(used: dict, target: float, line_x: float, w: float, W: float, H: float) -> tuple[float, float]:
    """라벨이 겹치지 않고 페이지 안에 들어가는 자리: 왼쪽 여백 열 → 그 왼쪽 열 → 오른쪽 여백 순."""
    cols, x = [], line_x - w - 6
    while x >= 2:
        cols.append(x)
        x -= w + 3
    cols.append(W - w - 4)
    ys = [target + k * 13 for k in range(0, 200) if target + k * 13 <= H - 8]
    ys += [target - k * 13 for k in range(1, 200) if target - k * 13 >= 8]
    for x in cols:
        taken = used.setdefault(round(x), [])
        for y in ys:
            if all(abs(y - u) >= 13 for u in taken):
                taken.append(y)
                return x, y
    return cols[-1], min(max(target, 8), H - 8)  # 페이지가 라벨로 가득 찬 극단적인 경우


def _page_header(page, text: str):
    page.insert_htmlbox(pymupdf.Rect(20, 4, page.rect.width - 20, 22),
                        f"<div style='color:#b05000;font-size:7.5pt'>{_e(text)}</div>",
                        css=_css(), archive=_archive())


_HEX = {True: "#f5c400", False: "#2f6fe0"}  # 참 진술 근거 / 함정 원문


def item_viewer_payload(project, item: dict) -> tuple[bytes, list[dict], list[str]]:
    """검수 화면 PDF 뷰어용: 근거 페이지만 잘라 만든 PDF, 근거 위치 상자(annotations), 페이지 라벨.
    좌표는 PDF 포인트 단위이며 page는 잘라 만든 PDF 기준 1부터."""
    gs = item.get("source_pages") or ([item["page"]] if item.get("page") is not None else [])
    src = pymupdf.open(project.manuscript_path)
    doc = pymupdf.open()
    for g in gs:
        doc.insert_pdf(src, from_page=g, to_page=g)
    pdf = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    src.close()
    anns = []
    for ev in item.get("evidence") or []:
        m = ev.get("match")
        if not m or m.get("g") not in gs:
            continue
        for bb in m.get("bboxes") or []:
            anns.append({"page": gs.index(m["g"]) + 1, "x": bb[0] - 2, "y": bb[1] - 1,
                         "width": bb[2] - bb[0] + 4, "height": bb[3] - bb[1] + 2,
                         "color": _HEX[bool(ev.get("is_true"))], "border": "solid"})
    return pdf, anns, [project.pages[g]["label"] for g in gs]


def render_item_page(project, item: dict, g: int, dpi: int = 110) -> bytes:
    """검수 화면용: 원고 페이지 g에 이 문항의 근거 하이라이트를 그린 PNG."""
    src = pymupdf.open(project.manuscript_path)
    doc = pymupdf.open()
    doc.insert_pdf(src, from_page=g, to_page=g)
    it = {**item, "qlabel": "Q"}
    _draw(doc[0], _segments(it, g), [])
    png = doc[0].get_pixmap(dpi=dpi, annots=True).tobytes("png")
    doc.close()
    src.close()
    return png


def build(project, items_all: list[dict], out_path: Path, *, mode: str = "extract", draft: bool = False,
          batch: int | None = None, chasi: int | None = None, title: str | None = None, scope: str | None = None) -> dict:
    opts = project.meta.get("options", {})
    items = numbering.number(numbering.select(items_all, draft=draft, batch=batch, scope=scope),
                             opts.get("numbering", "continuous"))
    if chasi is not None:
        items = [it for it in items if it.get("chasi") == chasi]
    src = pymupdf.open(project.manuscript_path)
    out = pymupdf.open()
    title = title or project.meta["name"]
    _cover(out, title, items, project, mode)
    _index(out, items, project)
    _guide(out)
    zero = []
    if mode == "extract":
        for it in items:
            _separator(out, it, project)
            for g in it.get("source_pages") or []:
                out.insert_pdf(src, from_page=g, to_page=g)
                pg = out[-1]
                segs = _segments(it, g)
                if not segs:
                    zero.append(it["qlabel"])
                _draw(pg, segs, [])
                _page_header(pg, f"{it['qlabel']} 근거 · 원고 p.{project.pages[g]['label']}")
    else:
        by_page: dict[int, list] = {}
        for it in items:
            for g in it.get("source_pages") or []:
                by_page.setdefault(g, []).extend(_segments(it, g))
        for p in project.pages:
            if chasi is not None and p["chasi"] != chasi:
                continue
            out.insert_pdf(src, from_page=p["g"], to_page=p["g"])
            pg = out[-1]
            segs = by_page.get(p["g"], [])
            _draw(pg, segs, [])
            n = len({s["label"].split("-")[0] for s in segs})
            note = "출제 제외 페이지" if p["excluded"] else f"이 페이지 근거 문항 {n}개"
            _page_header(pg, f"원고 p.{p['label']} · {p['chasi']}차시 · {note}")
    for it in items:
        if not any(ev.get("match") for ev in it.get("evidence") or []):
            zero.append(it["qlabel"])
    out.subset_fonts()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, garbage=4, deflate=True, clean=True)
    n_pages = len(out)
    out.close()
    src.close()
    export_log.record(project, mode, out_path, items, scope=scope or ("passed" if draft else "approved"),
                      batch=batch, chasi=chasi)
    return {"path": str(out_path), "items": len(items), "pages": n_pages, "no_highlight": sorted(set(zero))}
