"""5. 출력 — 양식 엑셀, 근거PDF(문항별 추출본·원고 전체 표시본)."""
from collections import Counter
from datetime import datetime

import streamlit as st

from exam_gen import ui
from exam_gen.output import evidence_pdf, excel, numbering

ui.setup("출력")
p = ui.require_project(ui.sidebar())
st.title("5. 출력")
items = p.items()
c = Counter(it["status"] for it in items)
st.write(f"승인 {c.get('approved', 0)} · 자동통과 {c.get('auto_pass', 0)} · 경고 {c.get('warn', 0)} · "
         f"수동확인 {c.get('manual', 0)} · 삭제 {c.get('deleted', 0)}")

c1, c2, c3 = st.columns(3)
draft = c1.toggle("초안 출력(승인 전 문항 포함)", value=False,
                  help="담당자 미리보기용. 최종 납품은 승인 문항만 출력한다. 수동확인·삭제 문항은 어느 경우에도 최종 시트에서 빠진다.")
batches = p.batches()
scope = c2.selectbox("범위", ["전체"] + [f"배치 {b['batch']}" for b in batches])
batch = None if scope == "전체" else int(scope.split()[1])
split = c3.toggle("근거PDF 차시별 분할", value=False, help="300문항 규모에서는 문항별 추출본이 커지므로 차시별로 나눈다.")

sel = numbering.select(items, draft=draft, batch=batch)
if not sel:
    st.warning("출력할 문항이 없다. " + ("검수 화면에서 승인하거나 '초안 출력'을 켜세요." if not draft else ""))
    st.stop()
st.caption(f"출력 대상 {len(sel)}문항")
stamp = datetime.now().strftime("%Y%m%d_%H%M")
base = p.dir / "exports"
tag = ("초안_" if draft else "") + (f"배치{batch}_" if batch else "")

if st.button("엑셀 만들기", type="primary"):
    with st.spinner("엑셀 작성 중…"):
        r = excel.write(p, items, base / f"{tag}평가문항_{stamp}.xlsx", draft=draft, batch=batch)
    st.session_state["xlsx"] = r["path"]
if st.session_state.get("xlsx"):
    path = st.session_state["xlsx"]
    st.download_button("엑셀 다운로드", open(path, "rb").read(), file_name=path.split("\\")[-1].split("/")[-1])

st.divider()
c1, c2 = st.columns(2)
if c1.button("근거PDF — 문항별 추출본"):
    outs = []
    with st.spinner("근거PDF 작성 중…"):
        chasis = sorted({it.get("chasi") for it in sel}) if split else [None]
        for ch in chasis:
            name = f"{tag}근거PDF_추출본{'_' + str(ch) + '차시' if ch else ''}_{stamp}.pdf"
            outs.append(evidence_pdf.build(p, items, base / name, mode="extract", draft=draft, batch=batch, chasi=ch))
    st.session_state["pdfs"] = outs
if c2.button("근거PDF — 원고 전체 표시본"):
    with st.spinner("근거PDF 작성 중…"):
        st.session_state["pdfs"] = [evidence_pdf.build(p, items, base / f"{tag}근거PDF_원고전체_{stamp}.pdf",
                                                       mode="overview", draft=draft, batch=batch)]
for r in st.session_state.get("pdfs", []):
    name = r["path"].replace("\\", "/").split("/")[-1]
    st.download_button(f"{name} ({r['items']}문항 · {r['pages']}쪽)", open(r["path"], "rb").read(), file_name=name)
    if r["no_highlight"]:
        st.error(f"하이라이트가 없는 문항: {', '.join(r['no_highlight'])} — 근거 확인 필요")
    else:
        st.success("모든 문항의 근거가 원고에 표시되었다.")
