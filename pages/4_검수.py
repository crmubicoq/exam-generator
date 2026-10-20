"""4. 검수(G7) — 문항과 원고 근거 하이라이트를 나란히 보고 수정·승인·삭제한다."""
import copy

import pandas as pd
import streamlit as st

from exam_gen import review_ops, ui
from exam_gen.generate import choice_builder
from exam_gen.llm import settings as llm_settings
from exam_gen.output.evidence_pdf import render_item_page
from exam_gen.ingest.page_map import format_pages

ui.setup("검수")


def _s(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip()


p = ui.require_project(ui.sidebar())
st.title("4. 검수")
st.caption("자동 검증(G1~G6)을 통과한 문항도 사람이 승인해야 최종 엑셀·근거PDF에 들어간다. "
           "근거를 원고에서 찾지 못한 문항은 승인할 수 없다.")

items = p.items()
if not items:
    st.info("출제된 문항이 없다.")
    st.stop()
pages = p.pages

# ---------- 필터 ----------
c = st.columns(6)
f_status = c[0].multiselect("상태", list(ui.STATUS_KO), default=["manual", "warn", "auto_pass"],
                            format_func=lambda s: ui.STATUS_KO[s])
f_type = c[1].multiselect("유형", list(ui.TYPE_KO), format_func=lambda t: ui.TYPE_KO[t])
f_diff = c[2].multiselect("난이도", ["최상", "상", "중", "하"])
f_chasi = c[3].multiselect("차시", sorted({it.get("chasi") for it in items}))
f_page = c[4].multiselect("책 쪽수", [x["label"] for x in pages])
f_text = c[5].text_input("검색(발문·키)")
view = [it for it in items
        if (not f_status or it["status"] in f_status) and (not f_type or it["type"] in f_type)
        and (not f_diff or it["difficulty"] in f_diff) and (not f_chasi or it.get("chasi") in f_chasi)
        and (not f_page or pages[it["page"]]["label"] in f_page)
        and (not f_text or f_text in it.get("stem", "") or f_text == it["key"])]
st.caption(f"{len(view)}문항 표시 / 전체 {len(items)}")

rows = pd.DataFrame([{"키": it["key"], "상태": ui.status_badge(it["status"]), "차시": it.get("chasi"),
                      "유형": ui.TYPE_KO[it["type"]], "난이도": it["difficulty"],
                      "쪽": format_pages([pages[g]["label"] for g in it.get("source_pages") or []]) or pages[it["page"]]["label"],
                      "발문": it.get("stem", "")[:70],
                      "경고": len(it["validation"].get("warnings", [])), "탈락사유": len(it["validation"].get("fails", []))}
                     for it in view])
left, right = st.columns([5, 7])
with left:
    sel = st.dataframe(rows, hide_index=True, width="stretch", height=560, on_select="rerun",
                       selection_mode="single-row", key="grid")
    with st.expander("일괄 승인"):
        cand = [it["key"] for it in view if it["status"] == "auto_pass"]
        st.write(f"현재 목록의 '자동통과'(경고 없음) 문항 {len(cand)}개를 승인한다. 승인 시 코드 관문을 다시 검사한다.")
        who = st.text_input("검수자", key="who_bulk")
        sure = st.checkbox("내용을 확인했고 일괄 승인한다")
        if st.button("일괄 승인", disabled=not (cand and sure and who)):
            r = review_ops.bulk_approve(p, cand, who)
            st.success(f"승인 {len(r['approved'])} · 차단 {len(r['blocked'])}")
            st.rerun()

if not view:
    st.stop()
idx = sel.selection.rows[0] if sel and sel.selection.rows else 0
it = copy.deepcopy(view[min(idx, len(view) - 1)])

with right:
    st.markdown(f"### {it['key']} · {ui.status_badge(it['status'])}")
    st.caption(f"{it.get('chasi')}차시 · {ui.TYPE_KO[it['type']]}"
               + (" 조합형" if it.get("statements") else "")
               + (f" · {'옳은 것' if it.get('polarity') == 'positive' else '옳지 않은 것'} 고르기" if it["type"] == "mcq" and not it.get("statements") else "")
               + f" · 난이도 {it['difficulty']} · 배치 {it['batch']} · 시도 {it['validation'].get('attempts')} · "
               f"근거 {it.get('evidence_level') or '없음'} · 모델 {it['gen'].get('model')}")
    for f in it["validation"].get("fails", []):
        st.error(f)
    for w in it["validation"].get("warnings", []):
        st.warning(w)

    tab_edit, tab_src, tab_check, tab_hist = st.tabs(["문항", "원고 근거", "검증 상세", "이력"])
    with tab_edit:
        it["stem"] = st.text_area("발문", it.get("stem", ""), height=90)
        if it.get("statements"):
            sdf = st.data_editor(pd.DataFrame(it["statements"]), hide_index=True, width="stretch",
                                 disabled=["label"], key=f"st_{it['key']}")
            it["statements"] = sdf.to_dict("records")
            st.caption("보기: " + "  ".join(f"{'①②③④'[i]} {c['text']}" for i, c in enumerate(it["choices"]))
                       + " (진술의 참/거짓을 바꾸면 보기가 다시 만들어진다)")
        elif it["type"] == "mcq":
            cdf = st.data_editor(pd.DataFrame([{"번호": i + 1, "보기": c["text"], "참": c["is_true"]}
                                               for i, c in enumerate(it["choices"])]),
                                 hide_index=True, width="stretch", disabled=["번호"], key=f"ch_{it['key']}")
            it["choices"] = [{"text": r["보기"], "is_true": bool(r["참"])} for _, r in cdf.iterrows()]
        c1, c2 = st.columns(2)
        if it["type"] == "ox":
            it["answer"] = c1.radio("정답", ["O", "X"], index=0 if it["answer"] == "O" else 1, horizontal=True)
        elif it["type"] == "mcq":
            it["answer"] = c1.selectbox("정답", ["1", "2", "3", "4"], index=["1", "2", "3", "4"].index(str(it["answer"])))
        else:
            it["answer"] = c1.text_input("정답", it["answer"])
            v = c2.text_input("허용 정답({^} 구분)", "{^}".join(it.get("short_variants") or []))
            it["short_variants"] = [x.strip() for x in v.split("{^}") if x.strip()]
        it["explanation"] = st.text_area("해설", it.get("explanation", ""), height=90)
        st.markdown("**근거 인용문** (원고 문장을 그대로 — 저장 시 원고에서 다시 찾는다)")
        edf = st.data_editor(pd.DataFrame([{"대상": e.get("target"), "인용문": e.get("quote", ""), "쪽": e.get("page_label", ""),
                                            "참 진술": bool(e.get("is_true")), "함정 메모": e.get("trap_note", ""),
                                            "찾음": (f"p.{e['match']['label']} {e['match']['level']}" if e.get("match") else "✗")}
                                           for e in it.get("evidence") or []]),
                             hide_index=True, width="stretch", num_rows="dynamic", disabled=["찾음"],
                             key=f"ev_{it['key']}")
        it["evidence"] = [{"target": _s(r["대상"]), "quote": _s(r["인용문"]), "page_label": _s(r["쪽"]),
                           "is_true": bool(r["참 진술"]) if not pd.isna(r["참 진술"]) else False,
                           "trap_note": _s(r["함정 메모"])}
                          for _, r in edf.iterrows() if _s(r["대상"])]
        note = st.text_input("수정 메모(선택)")
        who = st.text_input("검수자", key="who_one", value=st.session_state.get("who", ""))
        st.session_state["who"] = who
        b = st.columns(5)
        if b[0].button("수정 저장", width="stretch"):
            if it.get("statements"):
                correct = [s["label"] for s in it["statements"] if s.get("is_true")]
                sets = choice_builder.build(correct, int(it["answer"]))
                it["choices"] = [{"text": choice_builder.fmt(x), "labels": x,
                                  "is_true": x == sorted(correct, key="ㄱㄴㄷㄹ".index)} for x in sets]
                it["slot"]["combo_true"] = len(correct)
            it["slot"]["answer"] = str(it["answer"])
            r = review_ops.save_edit(p, it, note)
            st.toast("저장했다. " + ("코드 관문 탈락: " + "; ".join(r["validation"]["fails"]) if r["validation"]["fails"] else "코드 관문 통과"))
            st.rerun()
        if b[1].button("승인", type="primary", width="stretch", disabled=not who or it["status"] == "deleted"):
            ok, fails = review_ops.approve(p, it["key"], who)
            (st.toast("승인했다.") if ok else st.error("승인 불가: " + "; ".join(fails)))
            st.rerun()
        if b[2].button("LLM 재검증", width="stretch", help="G4 독립 풀이 + G5 검수를 다시 실행"):
            with st.spinner("재검증 중…"):
                review_ops.rerun_llm_gates(p, llm_settings.load(), it["key"])
            st.rerun()
        if it["status"] != "deleted":
            if b[3].button("삭제", width="stretch"):
                review_ops.set_status(p, it["key"], "deleted", note)
                st.rerun()
        elif b[3].button("복원", width="stretch"):
            review_ops.set_status(p, it["key"], "restore", note)
            st.rerun()
        if not who:
            b[4].caption("승인하려면 검수자 입력")

    with tab_src:
        gs = it.get("source_pages") or [it["page"]]
        for g in gs:
            st.image(render_item_page(p, it, g), caption=f"원고 p.{pages[g]['label']} — 노랑: 참 진술 근거 / 주황: 함정이 바꾼 원문",
                     width="stretch")

    with tab_check:
        g4 = it["validation"].get("G4")
        if g4:
            st.markdown(f"**G4 독립 풀이**: 답 `{g4.get('answer')}` (출제 정답 `{it['answer']}`) · "
                        f"정답 유일 {g4.get('unique')} · 원고로 풀이 가능 {g4.get('solvable')}")
            if g4.get("judgments"):
                st.dataframe(pd.DataFrame(g4["judgments"]), hide_index=True, width="stretch")
            if g4.get("note"):
                st.caption(g4["note"])
        g5 = it["validation"].get("G5")
        if g5:
            st.markdown("**G5 LLM 검수**")
            st.dataframe(pd.DataFrame(g5.get("checks", [])), hide_index=True, width="stretch")
            if g5.get("suggestion"):
                st.info("수정안: " + g5["suggestion"])
        g6 = it["validation"].get("G6")
        if g6:
            st.markdown(f"**G6 중복**: 가장 비슷한 문항 {g6.get('key')} (유사도 {g6.get('score')}) {g6.get('stem', '')}")
        st.markdown(f"**난이도 근거**: {it.get('difficulty_basis', '')}  \n**판단 포인트**: {it.get('key_point', '')}")

    with tab_hist:
        ev = p.events(key=it["key"], limit=50)
        st.dataframe(pd.DataFrame([{"시각": e["ts"], "작업": e["kind"], "내용": str(e["detail"])[:200]} for e in ev]),
                     hide_index=True, width="stretch")
