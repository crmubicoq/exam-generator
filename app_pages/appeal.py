"""이의신청 조회 — 서비스된 문항을 모든 프로젝트에서 찾아 근거·검증 기록·출력 이력을 한 화면에서 보고 처리 결과를 남긴다."""
from datetime import datetime

import pandas as pd
import streamlit as st

from exam_gen import appeal, ui
from exam_gen.integrity import content_hash
from exam_gen.output import export_log
from exam_gen.output.evidence_pdf import render_item_page
from exam_gen.store import Project, list_projects, set_last_project

ui.header("이의신청 조회", "gavel", "수험생 이의신청이 들어온 문항을 찾아 원고 근거와 자동 검증 기록을 확인하고 처리 결과를 남깁니다. "
                              "AI를 쓰지 않습니다.")

slugs = list_projects()
if not slugs:
    st.info("프로젝트가 없습니다.")
    st.stop()
names = {s: Project(s).meta.get("name", s) for s in slugs}

# ---------- 찾기 ----------
with st.container(border=True):
    st.markdown("##### :material/search: 문항 찾기")
    t1, t2 = st.tabs(["문항 내용·문항키로 찾기", "출력물의 문항번호로 찾기"])
    with t1:
        st.caption("이의신청서에 적힌 **문제 문장 일부**를 붙여넣거나 **문항키**(예: K00012, 엑셀 숨김 R열)를 입력합니다. "
                   "띄어쓰기·문장부호는 무시하고, 출력한 뒤 고친 문항은 출력 당시 내용으로도 찾습니다.")
        c1, c2 = st.columns([3, 2])
        query = c1.text_area("찾을 내용", key="appeal_q", height=90,
                             placeholder="예: 날짜와 담당 업무가 빠진 매끄러운 문장보다")
        scope = c2.multiselect("찾을 프로젝트(비우면 전체)", slugs, format_func=names.get, key="appeal_scope")
        results = appeal.search(query, scope or None) if query.strip() else []
        if query.strip() and not results:
            st.warning("비슷한 문항을 찾지 못했습니다. 더 긴 문장을 붙여넣거나 다른 부분으로 찾아 보세요.")
        if results:
            st.caption(f"{len(results)}건 — 일치도가 높은 순서입니다. 행을 고르면 아래에 자세히 보입니다.")
            df = pd.DataFrame([{"일치도": f"{r['score']:.0%}", "프로젝트": r["project"], "문항키": r["key"],
                                "상태": {**ui.STATUS_KO, "prior": "기출"}.get(r["status"], r["status"]),
                                "출력 횟수": r["exports"], "찾은 곳": r["where"], "발문": r["stem"][:80]} for r in results])
            sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                               key="appeal_table")
            if sel and sel.selection.rows:
                r = results[sel.selection.rows[0]]
                st.session_state["appeal_pick"] = (r["slug"], r["key"])
    with t2:
        st.caption("서비스에 올린 **출력 파일**과 그 파일의 **문항번호**를 알 때 씁니다.")
        c1, c2, c3 = st.columns([2, 3, 1], vertical_alignment="bottom")
        slug_n = c1.selectbox("프로젝트", slugs, format_func=names.get, key="appeal_n_proj")
        hist = export_log.history(Project(slug_n))
        if not hist:
            st.caption("이 프로젝트에는 출력 기록이 없습니다(출력 이력은 이 기능을 넣은 뒤 출력한 파일부터 남습니다).")
        else:
            eid = c2.selectbox("출력 파일", [e["id"] for e in hist], key="appeal_n_exp",
                               format_func=lambda i: next(f"{e['at'][:16].replace('T', ' ')} · {e['file']} ({e['n']}문항)"
                                                          for e in hist if e["id"] == i))
            no = c3.text_input("문항번호", key="appeal_n_no")
            if no.strip():
                k = appeal.by_number(Project(slug_n), eid, no.strip())
                if k:
                    st.session_state["appeal_pick"] = (slug_n, k)
                    st.success(f"{no}번 = 문항키 {k}")
                else:
                    st.warning(f"그 출력 파일에 {no}번 문항이 없습니다.")

pick = st.session_state.get("appeal_pick")
if not pick or pick[0] not in slugs:
    st.stop()
slug, key = pick
p = Project(slug)
it = p.item(key)
if it is None:
    st.warning("문항을 찾을 수 없습니다.")
    st.stop()
h = content_hash(it)
exps = export_log.item_exports(p, key, h)

# ---------- 한 화면 확인 ----------
st.markdown(f"### :material/description: {names[slug]} · {key} &nbsp;{ui.status_badge(it['status'])}")
left, right = st.columns([1, 1])
with left:
    with st.container(border=True):
        st.markdown(f"**[{ui.TYPE_KO.get(it['type'], it['type'])} · {it.get('difficulty')}]** {(it.get('stem') or '').replace(chr(10), '  ' + chr(10))}")
        for s in it.get("statements") or []:
            st.markdown(f"{s['label']}. {s['text']}")
        for i, c in enumerate(it.get("choices") or [], 1):
            st.markdown(f"{'①②③④⑤'[i - 1]} {c['text']}")
        ans = it.get("answer")
        if it["type"] == "mcq" and str(ans).isdigit():
            ans = "①②③④⑤"[int(ans) - 1]
        st.markdown(f":red[**정답: {ans}**]" + (f" (유사답변: {', '.join(it['short_variants'])})"
                                              if it.get("short_variants") else ""))
        if it.get("explanation"):
            st.caption(f"해설: {it['explanation']}")
    diff = [e for e in exps if not e["same"]]
    if diff:
        st.warning(f"출력한 뒤 문항 내용이 바뀌었습니다. 아래는 {diff[0]['at'][:10]} '{diff[0]['file']}'에 "
                   f"{diff[0]['no']}번으로 나간 당시 내용입니다.", icon=":material/history:")
        snap = diff[0].get("snap") or {}
        with st.container(border=True):
            st.markdown(snap.get("stem", "").replace(chr(10), '  ' + chr(10)))
            for s in snap.get("statements") or []:
                st.markdown(f"{s['label']}. {s['text']}")
            for i, c in enumerate(snap.get("choices") or [], 1):
                st.markdown(f"{'①②③④⑤'[i - 1]} {c['text']}")
            st.markdown(f"정답: {snap.get('answer')}")

    _g = it.get("gen") or {}
    st.caption(f"출제 정보: {_g.get('provider', '-')} · {_g.get('model') or '계정 기본 모델'} · 프롬프트 {_g.get('prompt_version', '-')}"
               + f" · 프로그램 {('v' + _g['app_version']) if _g.get('app_version') else 'v1.0.0 이전'}"
               + (f" · 난이도 기준 {_g['difficulty_policy']}" if _g.get('difficulty_policy') else ""))
    st.markdown("##### :material/verified: 자동 검증 기록")
    v = it.get("validation") or {}
    g4 = v.get("G4") or {}
    if g4:
        st.markdown(f"**AI 독립 풀이(G4)** — 정답을 모르는 AI가 고른 답: **{g4.get('answer')}** "
                    + ("(정답과 같음)" if str(g4.get("answer")) == str(it.get("answer")) else ":red[(정답과 다름)]"))
        for j in g4.get("judgments") or []:
            st.caption(f"{j.get('target')}: {'옳음' if j.get('is_true') else '틀림'} — {j.get('reason')}")
    for t in (v.get("G5") or {}).get("traps") or []:
        st.markdown(f"**AI 검수의 함정 판정(G5) · 보기 {t.get('target')}** — {appeal.VERDICT_KO.get(t.get('verdict'), t.get('verdict'))}")
        st.caption(t.get("conflict") or t.get("reason") or "")
    for w in v.get("warnings") or []:
        st.caption(f":orange[경고] {w}")
    for w in v.get("fails") or []:
        st.caption(f":red[탈락 사유] {w}")
    if not (g4 or v.get("G5")):
        st.caption("AI 검증 기록이 없습니다(빠른 출제 또는 기출 문항).")

with right:
    gs = it.get("source_pages") or ([it["page"]] if it.get("page") is not None else [])
    st.markdown("##### :material/menu_book: 원고 근거 — 노랑: 정답 근거 · 파랑: 함정이 바꾼 원문")
    if not gs:
        st.caption("근거 쪽이 없습니다.")
    for g in gs:
        st.caption(f"원고 p.{p.pages[g]['label']}")
        st.image(render_item_page(p, it, g), width="stretch")

# ---------- 이력 ----------
st.markdown("##### :material/schedule: 이력")
ap = it.get("approved") or {}
rel = appeal.related(p, key)
st.markdown(f"출제 {(it.get('created') or '')[:10]} · 원고 {it.get('manuscript_version', 1)}판 · "
            f"승인 {ap.get('by') or '-'} {(ap.get('at') or '')[:10]}"
            + (f" · 같은 원리로 묶인 문항 **{', '.join(rel)}** — 함께 점검이 필요할 수 있습니다." if rel else ""))
if exps:
    st.dataframe(pd.DataFrame([{"출력 일시": e["at"][:16].replace("T", " "),
                                "출력물": export_log.KIND_KO.get(e["kind"], e["kind"]), "파일": e["file"],
                                "문항번호": e["no"], "현재 내용과 같음": "예" if e["same"] else "아니요"} for e in exps]),
                 hide_index=True, width="stretch")
else:
    st.caption("출력 기록이 없습니다(출력 이력은 이 기능을 넣은 뒤 출력한 파일부터 남습니다).")

# ---------- 처리 기록 ----------
with st.container(border=True):
    st.markdown("##### :material/edit_note: 처리 결과 기록")
    c1, c2 = st.columns([2, 3])
    outcome = c1.radio("처리 결과", list(appeal.OUTCOMES), format_func=appeal.OUTCOMES.get, key=f"ap_out_{key}")
    memo = c2.text_area("메모(신청 내용·판단 근거)", key=f"ap_memo_{key}", height=110)
    handler = c2.text_input("처리자", value=st.session_state.get("who", ""), key=f"ap_who_{key}")
    ref = c2.text_input("관련 출력·회차(선택)", key=f"ap_ref_{key}", placeholder="예: 2026 상반기 1회차 7번")
    st.caption("**문항 폐기**를 기록하면 문항이 삭제 상태가 되어 다음 출력부터 빠집니다. "
               "**문항 수정**은 기록 뒤 아래 '검수 화면에서 고치기'로 고치면 AI 재검증을 거칩니다. "
               "**복수 정답 인정**은 서비스 시스템의 정답도 함께 바꿔야 합니다.")
    b1, b2, b3 = st.columns(3)
    if b1.button("기록", type="primary", icon=":material/save:", width="stretch", disabled=not handler.strip()):
        appeal.record(p, key, outcome, memo, handler, ref)
        st.session_state["who"] = handler
        st.toast("처리 결과를 기록했습니다.", icon=":material/check:")
        st.rerun()
    if b2.button("검수 화면에서 고치기", icon=":material/edit:", width="stretch"):
        st.session_state["project"] = slug
        set_last_project(slug, by="page")
        st.session_state["review_queue"] = "all"
        st.session_state["review_key"] = key
        st.switch_page("app_pages/review.py")
    if b3.button("검토서 PDF 만들기", icon=":material/picture_as_pdf:", width="stretch"):
        out = p.dir / "exports" / f"이의신청검토서_{key}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        st.session_state["appeal_pdf"] = appeal.review_pdf(p, key, out)
    r = st.session_state.get("appeal_pdf")
    if r and key in r["path"]:
        name = r["path"].replace("\\", "/").split("/")[-1]
        st.download_button(f"{name} ({r['pages']}쪽)", open(r["path"], "rb").read(), file_name=name,
                           icon=":material/download:")
    recs = appeal.records(p, key)
    if recs:
        st.dataframe(pd.DataFrame([{"일시": x["at"][:16].replace("T", " "), "결과": appeal.OUTCOME_SHORT[x["outcome"]],
                                    "처리자": x["handler"], "관련": x["export_ref"], "메모": x["memo"]} for x in recs]),
                     hide_index=True, width="stretch")
