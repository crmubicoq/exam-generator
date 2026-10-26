"""시험지 형태 PDF — 담당자 검토·회의용. 문항번호·발문·보기를 시험지처럼 싣고, 정답·해설은 선택해서 넣는다."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from ..config import type_name
from . import export_log, numbering
from .evidence_pdf import A4, _archive, _css, _e

_EXTRA_CSS = (".q{margin:0 0 12pt 0} .no{font-weight:bold} .ch{margin-left:14pt} .ans{color:#b00;margin-left:14pt}"
              " .exp{color:#444;margin-left:14pt;font-size:9pt} .hd{border-bottom:1px solid #999;margin-bottom:8pt}")


def _item_html(it: dict, with_answer: bool) -> str:
    out = [f"<div class='q'><div><span class='no'>{_e(it['no'])}.</span> {_e(it['stem'])}</div>"]
    for s in it.get("statements") or []:
        out.append(f"<div class='ch'>{_e(s['label'])}. {_e(s['text'])}</div>")
    if it["type"] == "mcq":
        out += [f"<div class='ch'>{'①②③④⑤'[i - 1]} {_e(c['text'])}</div>" for i, c in enumerate(it.get("choices") or [], 1)]
    elif it["type"] == "ox":
        out.append("<div class='ch'>( O / X )</div>")
    if with_answer:
        ans = it["answer"]
        if it["type"] == "mcq" and str(ans).isdigit():
            ans = "①②③④⑤"[int(ans) - 1]
        if it["type"] == "short" and it.get("short_variants"):
            ans = f"{ans} (유사답변: {', '.join(it['short_variants'])})"
        out.append(f"<div class='ans'>정답: {_e(ans)}</div>")
        if it.get("explanation"):
            out.append(f"<div class='exp'>해설: {_e(it['explanation'])}</div>")
    out.append("</div>")
    return "".join(out)


def build(project, items_all: list[dict], out_path: Path, *, scope: str = "all", batch: int | None = None,
          with_answer: bool = True, by_chasi: bool = True) -> dict:
    opts = project.meta.get("options", {})
    items = numbering.number(numbering.select(items_all, batch=batch, scope=scope), opts.get("numbering", "continuous"))
    doc = pymupdf.open()
    title = project.meta.get("name", "")
    blocks = [f"<h1>{_e(title)}</h1><p class='muted'>평가문항 {len(items)}문항 · "
              f"{'정답·해설 포함' if with_answer else '문제지(정답 없음)'}</p>"]
    chasi = None
    for it in items:
        if by_chasi and it.get("chasi") != chasi:
            chasi = it.get("chasi")
            blocks.append(f"<h2 class='hd'>{_e(chasi)}차시</h2>")
        blocks.append(_item_html(it, with_answer))
    css = _css() + _EXTRA_CSS
    story = pymupdf.Story(html="".join(blocks), user_css=css, archive=_archive())
    writer = pymupdf.DocumentWriter(str(out_path))
    rect = pymupdf.Rect(0, 0, A4[0], A4[1])
    where = rect + (45, 50, -45, -50)
    more = 1
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    with pymupdf.open(out_path) as d:
        n = d.page_count
    doc.close()
    export_log.record(project, "paper", out_path, items, scope=scope, batch=batch)
    return {"path": str(out_path), "items": len(items), "pages": n,
            "types": {type_name(t): sum(1 for it in items if it["type"] == t) for t in ("mcq", "ox", "short")}}
