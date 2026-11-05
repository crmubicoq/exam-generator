"""검수 화면 부품 — 보기마다 붙는 근거 인용문, 원고 근거 칸(검수 화면 오른쪽과 '새 창' 화면이 같이 쓴다).

담당자 피드백(2026-11-05): 문항과 원고 근거를 나란히 보고, 근거 인용문은 보기 바로 아래에 붙이며,
원고 근거는 스크롤해도 화면에 붙어 있게 하고, 원하면 새 창(듀얼 모니터)으로 띄운다.
"""
from __future__ import annotations

import html

import streamlit as st

from .ingest.page_map import format_pages
from .output.evidence_pdf import item_viewer_payload, render_item_page

try:  # PDF 뷰어(선택 패키지) — 없으면 페이지 이미지로 보여 준다
    from streamlit_pdf_viewer import pdf_viewer
except ImportError:
    pdf_viewer = None

CIRCLED = "①②③④"
TRUE_COLOR, TRAP_COLOR = "#f5c400", "#2f6fe0"   # 근거PDF와 같은 색(노랑=참 진술 근거, 파랑=함정이 바꾼 원문)


def chip(color: str, text: str) -> str:
    return (f"<span style='border:3px solid {color};border-radius:4px;padding:0 6px;"
            f"font-size:0.85em;white-space:nowrap'>{text}</span>")


def quote_box(e: dict) -> str:
    """근거 인용문 하나: 작은 글씨, 왼쪽 색 줄(노랑 참 진술 근거 / 파랑 함정 원문), 쪽·일치 수준, 함정이 바꾼 점."""
    m = e.get("match")
    where = f"p.{html.escape(str(m['label']))} · {m['level']}" if m else "<b style='color:#c62828'>원고에서 못 찾음</b>"
    true = bool(e.get("is_true"))
    color, kind = (TRUE_COLOR, "참 진술 근거") if true else (TRAP_COLOR, "함정 원문")
    note = (f"<br><span style='opacity:.75'>바꾼 점: {html.escape(e['trap_note'])}</span>"
            if not true and e.get("trap_note") else "")
    return (f"<div style='margin:2px 0 8px 22px;padding:3px 9px;border-left:4px solid {color};"
            f"background:rgba(127,127,127,.07);font-size:.86em;line-height:1.5'>"
            f"<b>{kind}</b> · {where}<br>“{html.escape(e['quote'])}”{note}</div>")


def quotes_for(item: dict, target) -> str:
    return "".join(quote_box(e) for e in item.get("evidence") or []
                   if e.get("quote") and str(e.get("target")) == str(target))


def quote_targets(item: dict) -> set[str]:
    """보기 아래에 붙는 근거의 대상 표기: 선다형 1~4, 조합형 진술 ㄱ~ㄹ, 진위형 OX, 단답형 A."""
    if item.get("statements"):
        return {str(s["label"]) for s in item["statements"]}
    if item["type"] == "mcq":
        return {str(i + 1) for i in range(len(item.get("choices") or []))}
    return {"OX"} if item["type"] == "ox" else {"A"}


def _row(text: str, bold: bool = False, badge: str = "") -> str:
    t = html.escape(text)
    return f"<div style='margin-top:6px;line-height:1.6'>{'<b>' + t + '</b>' if bold else t}{badge}</div>"


def question_html(item: dict) -> str:
    """시험지 형태의 문항 + 보기(진술·정답)마다 바로 아래 근거 인용문."""
    ok = (" <span style='background:#e3f4e8;color:#1a7f37;border-radius:6px;padding:0 6px;font-size:.85em;"
          "white-space:nowrap'>✓ 정답</span>")
    h = [f"<div style='font-size:1.1em;font-weight:600;line-height:1.6;white-space:pre-wrap;margin-bottom:6px'>"
         f"{html.escape(item.get('stem', ''))}</div>"]
    for s in item.get("statements") or []:  # 조합형: 진술마다 근거
        h.append(_row(f"{s['label']}. {s['text']}") + quotes_for(item, s["label"]))
    for i, c in enumerate(item.get("choices") or []):
        ans = str(i + 1) == str(item["answer"])
        h.append(_row(f"{CIRCLED[i]} {c['text']}", bold=ans, badge=ok if ans else "")
                 + ("" if item.get("statements") else quotes_for(item, i + 1)))
    if item["type"] == "ox":
        h.append(_row(f"정답: {item['answer']}", bold=True) + quotes_for(item, "OX"))
    elif item["type"] == "short":
        alt = f" (유사답변: {', '.join(item['short_variants'])})" if item.get("short_variants") else ""
        h.append(_row(f"정답: {item['answer']}{alt}", bold=True) + quotes_for(item, "A"))
    return "".join(h)


@st.cache_data(max_entries=40, show_spinner=False)
def _payload(pdir: str, key: str, updated: str):
    """PDF 뷰어용 원고 발췌·주석 — 같은 문항(같은 수정 시각)이면 다시 만들지 않는다(새 창은 2초마다 다시 그림)."""
    from .store import Project
    p = Project(__import__("pathlib").Path(pdir).name)
    return item_viewer_payload(p, p.item(key))


@st.cache_data(max_entries=80, show_spinner=False)
def _page_png(pdir: str, key: str, updated: str, g: int):
    from .store import Project
    p = Project(__import__("pathlib").Path(pdir).name)
    return render_item_page(p, p.item(key), g)


# 고정은 상자를 감싼 틀(stLayoutWrapper)에 건다 — 상자 자체에 걸면 틀이 상자와 같은 높이라 붙어 있을 공간이 없다
STICKY_CSS = ("<style>[data-testid='stLayoutWrapper']:has(> .st-key-{k}){{position:sticky;top:3.75rem;z-index:2;"
              "background:var(--background-color, white)}}"
              ".st-key-{k}_pages{{max-height:calc(100vh - 11rem);overflow-y:auto}}</style>")


def evidence_panel(project, item: dict, key_prefix: str = "ev", sticky: bool = False, viewer_height: int = 720):
    """원고 근거 칸: 범례, PDF 뷰어(근거로 자동 이동) 또는 페이지 이미지. sticky면 스크롤해도 화면에 붙어 있다."""
    pages = project.pages
    box = f"{key_prefix}_evidence"
    if sticky:
        st.markdown(STICKY_CSS.format(k=box), unsafe_allow_html=True)
    with st.container(key=box):
        gs = item.get("source_pages") or ([item["page"]] if item.get("page") is not None else [])
        labels = format_pages([pages[g]["label"] for g in gs]) if gs else ""
        st.markdown(f"**원고 근거** p.{labels} &nbsp; {chip(TRUE_COLOR, '참 진술 근거')} {chip(TRAP_COLOR, '함정이 바꾼 원문')}",
                    unsafe_allow_html=True)
        if not gs:
            st.warning("원고에서 확인된 근거 위치가 없습니다(근거 미매칭 또는 원고 삭제).", icon=":material/link_off:")
            return
        viewer = st.toggle("PDF 뷰어(근거로 자동 이동)", value=pdf_viewer is not None, disabled=pdf_viewer is None,
                           key=f"{key_prefix}_viewer", help="끄면 페이지 이미지로 봅니다. 뷰어 표시가 이상하면 끄세요.")
        if viewer:
            pdf, anns, _ = (_payload(str(project.dir), item["key"], item.get("updated", ""))
                            if item.get("key") else item_viewer_payload(project, item))
            pdf_viewer(pdf, annotations=anns, scroll_to_annotation=1 if anns else None, render_text=True,
                       annotation_outline_size=3, height=viewer_height,
                       key=f"{key_prefix}_pdf_{item['key']}_{item.get('updated', '')}")
        else:
            with st.container(key=f"{box}_pages"):
                for g in gs:
                    png = (_page_png(str(project.dir), item["key"], item.get("updated", ""), g)
                           if item.get("key") else render_item_page(project, item, g))
                    st.image(png, caption=f"원고 p.{pages[g]['label']}", width="stretch")
