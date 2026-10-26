"""5. 출력 — 양식 엑셀, 근거PDF(문항별 추출본·원고 전체 표시본)."""
from collections import Counter
from datetime import datetime

import streamlit as st

from exam_gen import ui
from exam_gen import select_final
from exam_gen.output import evidence_pdf, exam_paper, excel, export_log, numbering

p = ui.require_project(ui.current_project())
ui.header("출력", "download", "승인된 문항으로 양식 엑셀과 근거PDF를 만듭니다.")
items = p.items()
c = Counter(it["status"] for it in items)
# 결과 파일은 프로젝트별로 기억한다(다른 프로젝트로 바꿨을 때 이전 파일이 보이지 않게)
res = st.session_state.setdefault("export_results", {}).setdefault(str(p.dir), {})

# ---------- 최종 문항 자동 선정 ----------
with st.expander("최종 문항 자동 선정 — 여유분까지 출제한 문항에서 목표 수만큼 고르기", icon=":material/checklist:",
                 expanded=False):
    st.caption("목표 난이도·유형·차시 분포에 맞춰 고릅니다. 같은 원리 묶음에서는 1문항만, 정답 단서·이의 제기·AI 검증 생략 문항은 "
               "뒤로 미룹니다(수동확인·삭제는 제외). AI를 쓰지 않습니다. 고른 결과는 아래 출력 대상 **'최종 선정 문항만'**으로 내보냅니다.")
    _opts = p.meta.get("options", {})
    c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
    n_target = c1.number_input("목표 문항 수", 1, 5000, int(_opts.get("total", 300)))
    if c2.button("자동 선정", icon=":material/auto_awesome:"):
        st.session_state["final_select"] = select_final.select(p, int(n_target))
        st.rerun()
    rep = st.session_state.get("final_select")
    n_sel = sum(1 for it in items if it.get("selected") and it["status"] not in ("deleted",))
    if n_sel:
        st.markdown(f"현재 선정 **{n_sel}문항**")
    if rep:
        st.write(f"목표 {rep['target']} · 선정 {rep['selected']} (후보 {rep['candidates']}, 원리 반복으로 뒤로 미룬 문항 {rep['repeat_spare']})")
        st.caption("유형 " + " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in rep["type"].items())
                   + "  |  난이도 " + " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in rep["difficulty"].items())
                   + "  |  차시 " + " · ".join(f"{k}차시 {v[0]}/{v[1]}" for k, v in rep["chasi"].items()) + "  (선정/목표)")
        if rep["lacking"]:
            st.warning(f"후보가 모자라 {rep['lacking']}문항이 부족합니다. 추가 출제가 필요합니다.", icon=":material/warning:")
        if rep["short_difficulty"]:
            st.warning("목표보다 모자란 난이도: " + " · ".join(f"{k} {v}문항" for k, v in rep["short_difficulty"].items()),
                       icon=":material/trending_down:")

# ---------- 출력 설정 ----------
with st.container(border=True):
    m = st.columns(5)
    for col, (label, key) in zip(m, [("승인", "approved"), ("자동통과", "auto_pass"), ("경고", "warn"),
                                     ("수동확인", "manual"), ("삭제", "deleted")]):
        col.metric(label, c.get(key, 0))
    s1, s2 = st.columns([3, 2])
    # 출력 대상에 따라 범위별 문항 수가 달라지므로 먼저 그린다(화면 배치는 오른쪽)
    with s2:
        scope = st.radio("출력 대상", list(numbering.SCOPES), format_func=numbering.SCOPES.get, key="export_scope")
        st.caption("**전체(검수 없이)**: 삭제한 문항만 빼고 모두 출력합니다. 자동 검증에서 탈락한 문항은 엑셀에 **연한 빨강 행**으로, "
                   "근거PDF에는 '자동 검증 탈락'으로 표시됩니다.  \n"
                   "**자동 검증 통과**: 탈락 문항을 뺍니다(엑셀 첫 줄에 '초안' 표시).  \n"
                   "**승인한 문항만**: 검수 화면에서 사람이 승인한 문항만 — 최종 납품용입니다.")
    batches = p.batches()
    KIND = {"initial": "최초 출제", "additional": "추가 출제", "repair": "페이지 재출제"}

    def _scope_label(b):
        n = len(numbering.select(items, batch=b, scope=scope))
        if b is None:
            return f"전체 (모든 출제 회차) · {n}문항"
        info = batches[b - 1]
        day = (info.get("created") or "")[5:10].replace("-", "/")
        return f"배치 {b} · {KIND.get(info['kind'], info['kind'])} · {day} · {n}문항"

    with s1:
        batch = st.selectbox("출력 범위", [None] + [b["batch"] for b in batches], format_func=_scope_label)
        st.caption("**배치**는 출제를 한 번 실행한 회차입니다(처음 출제 = 배치 1, 이후 추가 출제마다 2, 3 …). "
                   "보통은 **전체**를 고르고, 추가 출제분만 따로 납품할 때 해당 배치를 고릅니다.")

sel = numbering.select(items, batch=batch, scope=scope)
if not sel:
    st.warning("출력할 문항이 없습니다. " + ("검수 화면에서 승인하거나 출력 대상을 바꾸세요." if scope == "approved" else ""))
    st.stop()
n_manual = sum(1 for it in sel if it.get("status") == "manual")
st.markdown(f"**출력 대상 {len(sel)}문항**" + (f" · 자동 검증 탈락 {n_manual}문항 포함" if n_manual else ""))
stamp = datetime.now().strftime("%Y%m%d_%H%M")
base = p.dir / "exports"
tag = {"all": "검수전_", "passed": "초안_", "approved": "", "selected": "최종선정_"}.get(scope, "") + (f"배치{batch}_" if batch else "")


def _name(path: str) -> str:
    return path.replace("\\", "/").split("/")[-1]


def _show_pdfs(key: str):
    for r in res.get(key, []):
        name = _name(r["path"])
        st.download_button(f"{name} ({r['items']}문항 · {r['pages']}쪽)", open(r["path"], "rb").read(),
                           file_name=name, icon=":material/download:", width="stretch", key=f"dl_{name}")
        if r["no_highlight"]:
            st.error(f"하이라이트가 없는 문항이 있습니다: {', '.join(r['no_highlight'])} — 근거를 확인해 주세요.")
        else:
            st.success("모든 문항의 근거 표시가 완료되었습니다.", icon=":material/check_circle:")


# ---------- 출력물 3종 ----------
# 카드 높이가 설명 길이에 따라 달라도 버튼 줄은 카드 아래쪽에 맞춘다
st.markdown("<style>[data-testid=stLayoutWrapper]:has(> [class*='st-key-export_act_']){margin-top:auto !important}</style>",
            unsafe_allow_html=True)
k1, k2, k3 = st.columns(3)
with k1.container(border=True, height="stretch"):
    st.markdown("##### :material/table_view: 평가문항 엑셀")
    st.caption("제출 양식 그대로 4행부터 채웁니다.\n\n"
               "- **최종 시트**: 승인 문항만, 번호 순서대로\n"
               "- **검수용(수정표시)** 숨김 시트: 삭제 = 회색 줄, 수정 = 노란 칸·빨간 글씨\n"
               "- **검증리포트** 시트: 난이도·유형·정답 위치 분포, 페이지 커버리지")
    act = st.container(key="export_act_xlsx")
    if act.button("엑셀 만들기", type="primary", icon=":material/play_arrow:", width="stretch"):
        with st.spinner("엑셀 작성 중…"):
            r = excel.write(p, items, base / f"{tag}평가문항_{stamp}.xlsx", batch=batch, scope=scope)
        res["xlsx"] = r
    if res.get("xlsx"):
        path = res["xlsx"]["path"]
        act.download_button(f"{_name(path)} ({res['xlsx']['count']}문항)", open(path, "rb").read(), file_name=_name(path),
                           icon=":material/download:", width="stretch")

with k2.container(border=True, height="stretch"):
    st.markdown("##### :material/description: 문항별 근거PDF")
    st.caption("**문항 하나씩 근거를 검토할 때** 씁니다.\n\n"
               "- 문항마다 안내 페이지(발문·보기·정답·해설) 뒤에 근거 원고 페이지를 붙입니다.\n"
               "- 같은 원고 페이지가 여러 문항에서 반복되므로 쪽수가 많습니다.\n"
               "- 기존 '근거PDF 추출본'과 같은 형식입니다.")
    act = st.container(key="export_act_extract")
    split = act.toggle("차시별 파일로 나누기", value=False, help="300문항 규모에서는 파일이 커지므로 차시별로 나눕니다.")
    if act.button("추출본 만들기", type="primary", icon=":material/play_arrow:", width="stretch"):
        outs = []
        with st.spinner("근거PDF 작성 중…"):
            for ch in (sorted({it.get("chasi") for it in sel}) if split else [None]):
                name = f"{tag}근거PDF_추출본{'_' + str(ch) + '차시' if ch else ''}_{stamp}.pdf"
                outs.append(evidence_pdf.build(p, items, base / name, mode="extract", batch=batch, chasi=ch, scope=scope))
        res["extract"] = outs
    with act:
        _show_pdfs("extract")

with k3.container(border=True, height="stretch"):
    st.markdown("##### :material/menu_book: 원고 전체 근거PDF")
    st.caption("**원고 전체에 고르게 출제됐는지 한눈에 볼 때** 씁니다.\n\n"
               "- 원고의 각 페이지를 한 번씩만 넣고, 그 페이지에서 나온 모든 문항의 라벨을 함께 표시합니다.\n"
               "- 라벨이 없는 페이지는 출제되지 않은 페이지입니다.\n"
               "- 문항 수와 상관없이 쪽수가 원고 분량 정도로 유지됩니다.")
    act = st.container(key="export_act_overview")
    if act.button("전체 표시본 만들기", type="primary", icon=":material/play_arrow:", width="stretch"):
        with st.spinner("근거PDF 작성 중…"):
            res["overview"] = [evidence_pdf.build(p, items, base / f"{tag}근거PDF_원고전체_{stamp}.pdf",
                                                  mode="overview", batch=batch, scope=scope)]
    with act:
        _show_pdfs("overview")

# ---------- 시험지 PDF ----------
with st.container(border=True):
    st.markdown("##### :material/quiz: 시험지 PDF (검토·회의용)")
    st.caption("문항번호·발문·보기를 시험지처럼 싣습니다. 위에서 고른 출력 대상·범위를 따릅니다.")
    c1, c2, c3 = st.columns([1, 1, 1.2], vertical_alignment="bottom")
    with_ans = c1.toggle("정답·해설 포함", value=True)
    by_ch = c2.toggle("차시별 구분", value=True)
    if c3.button("시험지 만들기", type="primary", icon=":material/play_arrow:", width="stretch"):
        with st.spinner("시험지 작성 중…"):
            res["paper"] = exam_paper.build(p, items, base / f"{tag}시험지{'_정답포함' if with_ans else ''}_{stamp}.pdf",
                                            scope=scope, batch=batch, with_answer=with_ans, by_chasi=by_ch)
    if res.get("paper"):
        r = res["paper"]
        st.download_button(f"{_name(r['path'])} ({r['items']}문항 · {r['pages']}쪽)", open(r["path"], "rb").read(),
                           file_name=_name(r["path"]), icon=":material/download:", key="dl_paper")

# ---------- 출력 이력 ----------
hist = export_log.history(p)
with st.expander(f"출력 이력 ({len(hist)}회) — 언제 어떤 파일을 몇 문항으로 만들었는지", icon=":material/history:"):
    st.caption("출력할 때마다 문항번호 ↔ 문항키 ↔ 그때의 문항 내용이 기록됩니다. 서비스에 올린 파일의 몇 번 문항에 "
               "이의신청이 오면 **이의신청 조회** 화면에서 이 기록으로 바로 찾습니다.")
    if not hist:
        st.caption("아직 출력 기록이 없습니다.")
    for e in hist[:30]:
        c1, c2 = st.columns([4, 1.2], vertical_alignment="center")
        c1.markdown(f"{e['at'][:16].replace('T', ' ')} · **{export_log.KIND_KO.get(e['kind'], e['kind'])}** · "
                    f"{numbering.SCOPES.get(e['scope'], e['scope'])} · {e['n']}문항 · `{e['file']}`")
        path = e.get("path")
        try:
            data = open(path, "rb").read() if path else None
        except OSError:
            data = None
        if data:
            c2.download_button("다시 받기", data, file_name=e["file"], key=f"hist_dl_{e['id']}",
                               icon=":material/download:", width="stretch")
        else:
            c2.caption("파일 없음")

st.caption(f"만든 파일은 프로젝트 폴더의 exports 에도 저장됩니다: {base}")
