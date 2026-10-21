"""1. 원고 등록 — 업로드, 책 쪽수 매핑 확인(Phase 0), 차시 지정, 출제 제외 페이지, 학습목표."""
import re
import tempfile
from pathlib import Path

import pandas as pd
import pymupdf
import streamlit as st

from exam_gen import ui
from exam_gen.ingest.convert import SUPPORTED, ConvertError

p = ui.require_project(ui.current_project())
ui.header("원고 등록", "upload_file", "원고를 올리고 책 쪽수 매핑을 확정한다. 확정 전에는 출제를 시작할 수 없다.")

# ---------- 업로드 ----------
with st.container(border=True):
    st.markdown(":material/upload_file: **원고 업로드** — PDF·PPTX·DOCX·HWP (모두 PDF로 변환해 근거 표시에 사용)")
    ups = st.file_uploader("원고 파일", type=[e.lstrip(".") for e in SUPPORTED], accept_multiple_files=True,
                           label_visibility="collapsed")
    if ups and st.button(f"{len(ups)}개 파일 등록", type="primary"):
        for up in ups:
            with st.spinner(f"{up.name} 변환·분석 중…"):
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / up.name
                    tmp.write_bytes(up.getvalue())
                    try:
                        s = p.add_source(tmp, up.name)
                        st.success(f"{up.name}: {s['pages']}페이지 등록")
                    except ConvertError as e:
                        st.error(f"{up.name}: {e}")
        st.rerun()

meta = p.meta
if not meta["sources"]:
    st.info("원고를 업로드하세요.")
    st.stop()

# ---------- 쪽수 매핑 ----------
st.subheader(":material/format_list_numbered: 책 쪽수 매핑 확인")
st.caption("엑셀 'PPT' 열과 근거PDF에는 원고에 인쇄된 쪽수를 쓴다. 자동 감지 결과를 원본 이미지와 비교해 확인하세요. "
           "확인 전에는 출제를 시작할 수 없다(자동 중단 조건 1).")
for s in meta["sources"]:
    pm = s["page_map"]
    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        with c1:
            ok = ":green-badge[:material/check: 확인됨]" if s.get("page_map_confirmed") else ":orange-badge[:material/warning: 확인 필요]"
            st.markdown(f"**{s['filename']}** · {s['pages']}페이지 · {s['kind'].upper()} · {ok}")
            mode_desc = {"printed": "인쇄 쪽수 감지", "pdf": "쪽수 감지 실패 → PDF 순번 사용", "slide": "슬라이드 번호"}
            st.write(f"감지: {mode_desc.get(pm['mode'], pm['mode'])} · PDF 1쪽 = 책 {pm['offset']}쪽 "
                     f"(일치 {pm['matched']}/{pm['total']}, 신뢰도 {pm['confidence']:.0%})")
            if s.get("ocr"):
                st.warning("스캔(OCR) 원고로 보인다. 근거 매칭률이 낮으면 자동 중단된다.")
            first = st.number_input("PDF 1쪽의 책 쪽수", value=int(pm["offset"]), step=1, key=f"off_{s['id']}")
            mode = st.radio("쪽수 기준", ["printed", "pdf", "slide"], index=["printed", "pdf", "slide"].index(pm["mode"]),
                            horizontal=True, key=f"mode_{s['id']}", format_func=lambda m: mode_desc[m])
            b1, b2 = st.columns(2)
            if b1.button("이 매핑으로 확정", key=f"ok_{s['id']}", type="primary"):
                p.set_page_map(s["id"], first, mode, True)
                st.rerun()
            used = p.items_using_source(s["id"])
            sure = True
            if used:
                sure = b2.checkbox(f"문항 {used}개의 근거·승인이 무효화됨을 확인", key=f"rmok_{s['id']}")
            if b2.button("원고 삭제", key=f"rm_{s['id']}", disabled=not sure):
                lost = p.remove_source(s["id"])
                if lost:
                    st.warning(f"문항 {lost}개를 '수동 확인'으로 돌렸다(근거 원고 삭제).")
                st.rerun()
        with c2:
            doc = pymupdf.open(p.dir / s["pdf"])
            i = min(1, len(doc) - 1)
            pg = doc[i]
            for name, clip in (("머리말", pymupdf.Rect(0, 0, pg.rect.width, pg.rect.height * 0.09)),
                               ("꼬리말", pymupdf.Rect(0, pg.rect.height * 0.88, pg.rect.width, pg.rect.height))):
                st.image(pg.get_pixmap(dpi=90, clip=clip).tobytes("png"),
                         caption=f"PDF {i + 1}쪽 {name} → 책 {i + int(first)}쪽으로 기록됨")
            doc.close()

# ---------- 과정 정보 ----------
st.subheader(":material/school: 과정 정보")
c1, c2 = st.columns([1, 2])
subject = c1.text_input("과목/주제명 (문항 앞 [주제명] 접두에 사용)", value=meta.get("subject_tag", ""))
objectives = c2.text_area("학습목표 (출제·검수 시 '중요 내용' 판단 기준)", value=meta.get("objectives", ""), height=110)
if not meta.get("objectives"):
    found = []
    for pg in p.pages[:6]:
        txt = p.page_text(pg["g"])
        m = re.search(r"학습\s*목표([\s\S]{0,400})", txt)
        if m:
            found.append(m.group(1).strip())
    if found:
        st.caption("원고에서 찾은 학습목표 후보: " + found[0][:200])
if st.button("과정 정보 저장"):
    p.update_meta(subject_tag=subject, objectives=objectives)
    st.toast("저장했다", icon=":material/check:")

# ---------- 페이지 표 ----------
st.subheader(":material/view_list: 페이지별 차시·출제 제외")
st.caption("차시를 고치거나 표지·목차·학습정리처럼 출제하지 않을 페이지를 '제외'로 표시하세요. 글자 수가 적은 페이지는 자동 제외된다.")
pages = p.pages
srcname = {s["id"]: s["filename"] for s in meta["sources"]}
df = pd.DataFrame([{"g": x["g"], "파일": srcname.get(x["src"], x["src"]), "PDF쪽": x["idx"] + 1, "책 쪽수": x["label"],
                    "차시": x["chasi"], "제외": x["excluded"], "글자 수": x["chars"],
                    "미리보기": p.page_text(x["g"])[:60].replace("\n", " ")} for x in pages])
with st.expander("차시 일괄 지정"):
    c1, c2, c3, c4 = st.columns(4)
    a = c1.number_input("PDF 시작(전체 순번)", 1, len(pages), 1)
    b = c2.number_input("PDF 끝", 1, len(pages), len(pages))
    ch = c3.number_input("차시", 1, 99, 1)
    if c4.button("적용"):
        for x in pages:
            if a - 1 <= x["g"] <= b - 1:
                x["chasi"] = int(ch)
        p.save_pages(pages)
        st.rerun()
edited = st.data_editor(df, hide_index=True, width="stretch", height=420,
                        disabled=["g", "파일", "PDF쪽", "책 쪽수", "글자 수", "미리보기"],
                        column_config={"g": None, "차시": st.column_config.NumberColumn(min_value=1, max_value=99, step=1)})
if st.button("페이지 설정 저장", type="primary"):
    for x, (_, row) in zip(pages, edited.iterrows()):
        x["chasi"], x["excluded"] = int(row["차시"]), bool(row["제외"])
    p.save_pages(pages)
    st.toast("저장했다", icon=":material/check:")
elig = [x for x in pages if not x["excluded"]]
st.caption(f"출제 대상 {len(elig)}페이지 / 전체 {len(pages)}페이지 · 차시 {sorted({x['chasi'] for x in elig})}")
