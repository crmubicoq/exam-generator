"""1. 원고 등록 — ① 업로드 ② 쪽수 확정(Phase 0) ③ 차시·출제 제외 ④ 과정 정보, 나중에 원고 새 판 교체."""
import re
import tempfile
from pathlib import Path

import pandas as pd
import pymupdf
import streamlit as st

from exam_gen import manuscript_update as MU
from exam_gen import ui
from exam_gen.ingest.convert import SUPPORTED, ConvertError
from exam_gen.llm import settings as llm_settings

p = ui.require_project(ui.current_project())
ui.header("원고 등록", "upload_file", "아래 ①~④를 순서대로 하면 됩니다. ② 쪽수를 확정해야 출제를 시작할 수 있습니다.")
ss = st.session_state


def step(n: int, title: str, done: bool | None, icon: str):
    """단계 제목 + 완료 표시. done=None이면 '선택'."""
    badge = (":green-badge[:material/check: 완료]" if done else
             ":gray-badge[선택]" if done is None else ":orange-badge[:material/pending: 할 일]")
    st.subheader(f":material/{icon}: {n}. {title}  {badge}")


# ---------- ① 업로드 ----------
meta = p.meta
step(1, "원고 파일 올리기", bool(meta["sources"]), "upload_file")
with st.container(border=True):
    st.caption("PDF·PPTX·DOCX·HWP를 올립니다. 모두 PDF로 바꿔 근거 표시에 씁니다. 원고가 여러 파일이면 차시 순서대로 "
               "올리세요(파일 순서가 차시 순서가 됩니다).")
    for kind, msg in ss.pop("reg_msgs", []):
        (st.success if kind == "ok" else st.error)(msg)
    # 등록이 끝나면 업로드 칸을 비운다(같은 파일을 실수로 두 번 등록하지 않게)
    ups = st.file_uploader("원고 파일", type=[e.lstrip(".") for e in SUPPORTED], accept_multiple_files=True,
                           label_visibility="collapsed", key=f"src_up_{ss.get('src_up_n', 0)}")
    have = {x["filename"] for x in meta["sources"]}
    dup = [u.name for u in ups or [] if u.name in have]
    if dup:
        st.warning(f"이미 등록된 파일입니다: {', '.join(dup)} — 다시 등록하면 같은 원고가 두 번 들어갑니다. "
                   "수정본이면 맨 아래 '나중에 원고 수정본이 오면'에서 새 판으로 교체하세요.", icon=":material/content_copy:")
    new = [u for u in ups or [] if u.name not in have]
    if new and st.button(f"{len(new)}개 파일 등록", type="primary", icon=":material/upload:"):
        msgs = []
        for up in new:
            with st.spinner(f"{up.name} 변환·분석 중…"):
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / up.name
                    tmp.write_bytes(up.getvalue())
                    try:
                        s = p.add_source(tmp, up.name)
                        msgs.append(("ok", f"{up.name}: {s['pages']}페이지를 등록했습니다. 아래 ②에서 쪽수를 확인하세요."))
                    except ConvertError as e:
                        msgs.append(("err", f"{up.name}: {e}"))
        ss["reg_msgs"] = msgs
        ss["src_up_n"] = ss.get("src_up_n", 0) + 1
        st.rerun()
    if meta["sources"]:
        st.markdown("등록된 원고: " + " · ".join(f"**{x['filename']}**({x['pages']}쪽)" for x in meta["sources"]))

if not meta["sources"]:
    st.stop()

# ---------- ② 쪽수 확정 ----------
step(2, "쪽수 확정", p.page_map_ready(), "format_list_numbered")
st.caption("엑셀의 쪽수 칸과 근거PDF에는 **원고에 인쇄된 쪽수**를 씁니다. 프로그램이 찾은 쪽수가 오른쪽 그림(원고의 머리말·꼬리말)과 "
           "맞는지 보고 **이 쪽수로 확정**을 누르세요. 확정해야 출제할 수 있습니다.")
for s in meta["sources"]:
    pm = s["page_map"]
    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        with c1:
            ok = (":green-badge[:material/check: 확정됨]" if s.get("page_map_confirmed")
                  else ":orange-badge[:material/warning: 확정 필요]")
            st.markdown(f"**{s['filename']}** · {s['pages']}페이지 · {s['kind'].upper()} · {ok}")
            mode_desc = {"printed": "원고에 인쇄된 쪽수", "pdf": "파일 순서대로(인쇄된 쪽수를 못 찾음)", "slide": "슬라이드 번호"}
            st.write(f"찾은 결과: **파일 첫 쪽 = 원고 {pm['offset']}쪽** ({mode_desc.get(pm['mode'], pm['mode'])}, "
                     f"{pm['total']}쪽 중 {pm['matched']}쪽에서 확인)")
            if s.get("ocr"):
                st.warning("스캔한 원고(이미지)로 보입니다. 근거 문장을 원고에서 찾지 못하는 문항이 많으면 출제가 자동으로 멈춥니다.")
            first = st.number_input("파일 첫 쪽의 원고 쪽수", value=int(pm["offset"]), step=1, key=f"off_{s['id']}",
                                    help="원고 쪽수가 다르게 찍혀 있으면 직접 고칩니다. 예: 파일 첫 쪽이 원고 17쪽이면 17")
            mode = st.radio("쪽수 기준", ["printed", "pdf", "slide"], index=["printed", "pdf", "slide"].index(pm["mode"]),
                            horizontal=True, key=f"mode_{s['id']}", format_func=lambda m: mode_desc[m])
            if st.button("이 쪽수로 확정", key=f"ok_{s['id']}", type="primary", icon=":material/check:"):
                p.set_page_map(s["id"], first, mode, True)
                st.rerun()
            with st.expander("이 원고 삭제", icon=":material/delete:"):
                used = p.items_using_source(s["id"])
                st.caption("잘못 올린 원고를 지웁니다." +
                           (f" 이 원고로 만든 문항 {used}개는 근거가 사라져 '수동확인'으로 바뀝니다." if used else ""))
                sure = st.checkbox("이 원고를 삭제합니다", key=f"rmok_{s['id']}")
                if st.button("원고 삭제", key=f"rm_{s['id']}", disabled=not sure):
                    lost = p.remove_source(s["id"])
                    if lost:
                        st.warning(f"문항 {lost}개를 '수동확인'으로 바꿨습니다(근거 원고 삭제).")
                    st.rerun()
        with c2:
            doc = pymupdf.open(p.dir / s["pdf"])
            i = min(1, len(doc) - 1)
            pg = doc[i]
            for name, clip in (("머리말", pymupdf.Rect(0, 0, pg.rect.width, pg.rect.height * 0.09)),
                               ("꼬리말", pymupdf.Rect(0, pg.rect.height * 0.88, pg.rect.width, pg.rect.height))):
                st.image(pg.get_pixmap(dpi=130, clip=clip).tobytes("png"),
                         caption=f"파일 {i + 1}번째 쪽의 {name} → 원고 {i + int(first)}쪽으로 기록됩니다")
            doc.close()

# ---------- ③ 차시·출제 제외 ----------
pages = p.pages
_chasi = {x["chasi"] for x in pages if not x["excluded"]}
step(3, "차시 나누기·출제 제외 페이지", len(_chasi) > 1 or len(pages) <= 15, "view_list")
st.caption("**차시마다 문항 수를 분량에 맞춰 먼저 나누므로, 차시를 바르게 지정해야 원고 전체에 고르게 출제됩니다.** "
           "'차시 일괄 지정'으로 쪽 범위마다 차시를 정하고, 표지·목차·학습정리처럼 출제하지 않을 페이지는 '제외'에 체크한 뒤 "
           "**페이지 설정 저장**을 누르세요. 글자가 적은 페이지는 자동으로 제외됩니다.")
if len(_chasi) <= 1 and len(pages) > 15:
    st.warning(f"지금은 {len(pages)}쪽 전체가 **1차시**입니다. 원고가 여러 차시라면 나눠 주세요.", icon=":material/warning:")
srcname = {s["id"]: s["filename"] for s in meta["sources"]}
df = pd.DataFrame([{"g": x["g"], "순번": x["g"] + 1, "파일": srcname.get(x["src"], x["src"]), "원고 쪽수": x["label"],
                    "차시": x["chasi"], "제외": x["excluded"], "글자 수": x["chars"],
                    "미리보기": p.page_text(x["g"])[:60].replace("\n", " ")} for x in pages])
with st.expander("차시 일괄 지정", icon=":material/format_list_numbered_rtl:"):
    st.caption("표의 **순번**으로 범위를 정합니다(여러 파일이면 이어서 센 번호).")
    c1, c2, c3, c4 = st.columns(4, vertical_alignment="bottom")
    a = c1.number_input("시작 순번", 1, len(pages), 1)
    b = c2.number_input("끝 순번", 1, len(pages), len(pages))
    ch = c3.number_input("차시", 1, 99, 1)
    if c4.button("적용", width="stretch"):
        for x in pages:
            if a - 1 <= x["g"] <= b - 1:
                x["chasi"] = int(ch)
        p.save_pages(pages)
        st.rerun()
edited = st.data_editor(df, hide_index=True, width="stretch", height=420,
                        disabled=["g", "순번", "파일", "원고 쪽수", "글자 수", "미리보기"],
                        column_config={"g": None, "차시": st.column_config.NumberColumn(min_value=1, max_value=99, step=1)})
if st.button("페이지 설정 저장", type="primary", icon=":material/save:"):
    for x, (_, row) in zip(pages, edited.iterrows()):
        x["chasi"], x["excluded"] = int(row["차시"]), bool(row["제외"])
    p.save_pages(pages)
    st.toast("저장했습니다", icon=":material/check:")
elig = [x for x in pages if not x["excluded"]]
st.caption(f"출제 대상 {len(elig)}페이지 / 전체 {len(pages)}페이지 · 차시 "
           f"{', '.join(map(str, sorted({x['chasi'] for x in elig})))}")

# ---------- ④ 과정 정보 ----------
step(4, "과정 정보 (권장)", bool(meta.get("objectives")) or None, "school")
st.caption("학습목표를 넣으면 AI가 '중요한 내용'을 고르는 기준으로 씁니다. 원고에 학습목표가 있으면 아래에 후보가 보입니다.")
c1, c2 = st.columns([1, 2])
subject = c1.text_input("과목/주제명 (문항 앞에 [주제명]으로 붙음, 비워도 됨)", value=meta.get("subject_tag", ""))
objectives = c2.text_area("학습목표", value=meta.get("objectives", ""), height=110)
if not meta.get("objectives"):
    found = []
    for pg in p.pages[:6]:
        txt = p.page_text(pg["g"])
        m = re.search(r"학습\s*목표([\s\S]{0,400})", txt)
        if m:
            found.append(m.group(1).strip())
    if found:
        st.caption("원고에서 찾은 학습목표 후보: " + found[0][:200])
if st.button("과정 정보 저장", icon=":material/save:"):
    p.update_meta(subject_tag=subject, objectives=objectives)
    st.toast("저장했습니다", icon=":material/check:")

# ---------- 다음 단계 ----------
st.divider()
if p.page_map_ready():
    n1, n2, _ = st.columns([1.4, 1.4, 2])
    if n1.button("기출문제 등록(선택)", icon=":material/history_edu:", width="stretch"):
        st.switch_page("app_pages/prior.py")
    if n2.button("다음: 출제 옵션", type="primary", icon=":material/arrow_forward:", width="stretch"):
        st.switch_page("app_pages/options.py")
else:
    st.info("② 쪽수를 확정하면 다음 단계(출제 옵션)로 넘어갈 수 있습니다.", icon=":material/info:")

# ---------- 원고 새 판으로 교체 ----------
st.subheader(":material/published_with_changes: 나중에 원고 수정본이 오면")
with st.expander("원고 수정본이 왔을 때 — 새 판으로 교체", icon=":material/published_with_changes:",
                 expanded=bool(MU.flagged_items(p))):
    st.caption("수정된 원고를 올리면 **쪽수와 근거 위치를 자동으로 다시 맞추고**, 근거 문장이 바뀌거나 사라진 문항만 "
               "'원고 변경'으로 표시합니다. 이 단계는 **AI를 쓰지 않습니다**. 원고가 더 바뀌지 않을 때 아래 '변경 문항 일괄 처리'를 "
               "누르면 그때 AI로 고칩니다(바로 전 판과만 비교하므로 이미 고친 문항은 다시 표시되지 않습니다).")
    for s in meta["sources"]:
        c1, c2, c3 = st.columns([3, 3, 1.4], vertical_alignment="bottom")
        c1.markdown(f"**{s['filename']}** · 현재 **{s.get('version', 1)}판** · {s['pages']}쪽")
        newf = c2.file_uploader("새 판 파일", type=["pdf", "pptx", "docx", "hwp", "hwpx", "ppt", "doc"],
                                key=f"newver_{s['id']}", label_visibility="collapsed")
        if c3.button("새 판으로 교체", key=f"replace_{s['id']}", disabled=newf is None, type="primary", width="stretch"):
            with tempfile.TemporaryDirectory() as td, st.spinner("새 판을 비교하는 중… (AI 사용 없음)"):
                fp = Path(td) / newf.name
                fp.write_bytes(newf.getvalue())
                try:
                    st.session_state["ms_report"] = MU.replace_source(p, s["id"], fp, newf.name)
                except Exception as e:  # noqa: BLE001
                    st.error(f"교체하지 못했습니다: {e}")
            st.rerun()
    rep = MU.last_report(p)
    if rep:
        st.markdown(f"**최근 교체: {rep['filename']} → {rep['version']}판** ({rep['at'][:16].replace('T', ' ')})")
        st.write(f"쪽 {rep['pages_old']} → {rep['pages_new']} · 내용이 바뀐 쪽 {len(rep['changed_pages'])} · "
                 f"사라진 쪽 {len(rep['deleted_pages'])} · 새로 생긴 쪽 {len(rep['added_pages'])} · "
                 f"원고 변경 문항: 내용 변경 {rep['items_changed']} · 내용 삭제 {rep['items_removed']}")
        if rep.get("page_map_reconfirm"):
            st.warning("새 판의 쪽수가 달라졌습니다. 위 ② 쪽수 확정에서 다시 확정하세요.", icon=":material/warning:")
        if rep["added_pages"]:
            st.caption(f"새로 생긴 쪽 {', '.join(rep['added_pages'][:15])} — 필요하면 '출제 옵션 → 페이지 재출제'로 추가 출제하세요.")
    flagged = MU.flagged_items(p)
    if flagged:
        est = MU.estimate(flagged)
        st.markdown(f"#### 변경 문항 {len(flagged)}개 일괄 처리")
        st.info(f"AI 수정 {est['revise']}개(바뀐 부분만) + 재출제 {est['regenerate']}개(내용이 사라진 문항) · "
                f"예상 AI 호출 약 {est['calls']}회 · 약 {est['minutes']}분. 문항 번호는 그대로 유지되고, 고친 칸은 엑셀 "
                "검수용 시트에 노란색으로 표시됩니다. 문항별로 처리하려면 검수 화면의 '원고 변경' 대기열을 쓰세요.",
                icon=":material/auto_fix_high:")
        if st.checkbox("원고가 확정되었습니다 — 변경 문항을 지금 AI로 처리합니다", key="ms_confirm"):
            if st.button("변경 문항 일괄 처리 시작", type="primary", icon=":material/play_arrow:"):
                MU.start_revise(p, llm_settings.load(), [it["key"] for it in flagged])
                st.switch_page("app_pages/progress.py")
