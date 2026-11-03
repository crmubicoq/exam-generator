"""난이도 기준 — 담당자 지침(또는 담당자가 확인한 기준안)을 넣고 확정한다. 출제·AI 검수가 이 기준을 쓴다.
공통 기준(모든 프로젝트)과 프로젝트 전용 기준(과정마다 다를 때, 담당자 요청 2026-11-03)을 둔다."""
import pandas as pd
import streamlit as st

from exam_gen import difficulty_policy as DP
from exam_gen import ui
from exam_gen.store import last_project

ui.header("난이도 기준", "school", "하·중·상·최상을 무엇으로 나눌지 정합니다. 여기서 정한 기준으로 AI가 문항을 출제하고 난이도를 판정합니다.")

p = ui.current_project()
own = DP.has_project_policy(p) if p else False

# ---------- 어떤 기준을 고칠지 ----------
st.markdown("##### 기준 범위")
st.markdown("- **공통 기준**: 따로 정하지 않은 **모든 프로젝트**가 씁니다.\n"
            "- **프로젝트 전용 기준**: 과정마다 난이도 기준이 다를 때, **지금 선택한 프로젝트에만** 따로 정한 기준입니다. "
            "전용 기준이 있으면 그 프로젝트는 공통 기준 대신 이것을 씁니다.")
if p:
    name = p.meta.get("name", p.slug)
    st.info(f"지금 프로젝트 **{name}**은(는) " + ("**이 프로젝트 전용 기준**을 씁니다." if own else "**공통 기준**을 따릅니다."),
            icon=":material/folder_open:")
    target = st.radio("고칠 기준", ["project", "common"], horizontal=True, index=0 if own else 1,
                      format_func=lambda k: {"project": f"이 프로젝트 전용 기준 ({name})",
                                             "common": "공통 기준 (모든 프로젝트)"}[k])
else:
    st.caption("프로젝트를 선택하면 그 프로젝트에만 쓰는 전용 기준도 정할 수 있습니다.")
    target = "common"

proj = p if target == "project" else None
if target == "project" and not own:
    st.warning("이 프로젝트에는 아직 전용 기준이 없습니다. 아래에 **공통 기준을 복사해 두었으니** 이 과정에 맞게 고친 뒤 "
               "**기준 저장**을 누르면 이 프로젝트 전용 기준이 만들어집니다. 저장하기 전까지는 공통 기준을 씁니다.",
               icon=":material/content_copy:")
pol = DP.load(proj) if (target == "common" or own) else DP.load_common()

if pol["status"] == "확정":
    st.success(f"**담당자 확정 기준**입니다 — {pol.get('confirmed_by') or '확인자 미기록'} · "
               f"{(pol.get('confirmed_at') or '')[:16].replace('T', ' ')}", icon=":material/verified:")
else:
    st.warning("**임시 기준안**입니다(담당자 확인 전). 받은 기준 문서에는 난이도 비율만 있고 단계별 정의가 없어, "
               "교육 평가에서 널리 쓰는 **블룸(Bloom)의 인지 수준**(기억·이해·적용·분석/종합)에 맞춰 만든 안입니다. "
               "담당자 지침이 있으면 아래에 넣고, 없으면 이 안을 보고 고칠 곳만 고친 뒤 **확정**해 주세요.",
               icon=":material/pending:")

st.markdown("##### 이 기준이 쓰이는 곳")
st.markdown("- **출제**: AI가 슬롯 난이도에 맞는 문항을 만들 때 기준으로 씁니다.\n"
            "- **AI 검수**: 만든 문항의 실제 난이도를 판정할 때 기준으로 씁니다(판정은 '정답을 고르는 데 꼭 필요한 최소 사고 과정'으로 합니다).\n"
            "- 기준을 바꾸면 **이후 출제·AI 재검증부터** 적용됩니다. 이미 만든 문항의 난이도는 바뀌지 않습니다.\n"
            "- 문항마다 실제 난이도를 사람이 정하려면 검수 화면의 **담당자 판정 난이도**를 쓰세요. 담당자 판정은 AI 판정보다 우선하고, "
            "쌓일수록 '난이도 보정 출제'가 담당자 기준에 맞춰집니다.")

st.subheader(":material/edit_note: 단계별 기준 — " + ("이 프로젝트 전용" if proj else "공통"))
with st.form(f"policy_form_{target}"):
    levels = {}
    for lv in DP.LEVELS:
        levels[lv] = st.text_area(lv, pol["levels"][lv], height=90, key=f"pol_{target}_{lv}")
    c1, c2 = st.columns(2)
    source = c1.text_input("기준 출처", pol.get("source", ""),
                           help="예: '교육운영팀 평가 지침 2026', '담당자 메일 2026-11-02', '개발 기준안 확인'")
    note = c2.text_input("메모(바꾼 이유 등)", "")
    who = st.text_input("확인자 이름", last_project().get("reviewer", ""))
    confirm = st.checkbox("담당자가 확인한 기준입니다(확정)", value=pol["status"] == "확정",
                          help="체크하지 않고 저장하면 '임시 기준'으로 남습니다.")
    if st.form_submit_button("기준 저장" + (" (이 프로젝트 전용)" if proj else " (공통)"), type="primary", icon=":material/save:"):
        if any(not v.strip() for v in levels.values()):
            st.error("네 단계를 모두 채워 주세요.")
        elif confirm and not who.strip():
            st.error("확정하려면 확인자 이름을 넣어 주세요.")
        else:
            DP.save(levels, "확정" if confirm else "임시", who.strip(), source.strip(), note.strip(), project=proj)
            st.toast("난이도 기준을 저장했습니다.", icon=":material/check:")
            st.rerun()

if proj and own:
    with st.expander("전용 기준을 지우고 공통 기준 따르기", icon=":material/undo:"):
        st.caption("이 프로젝트 전용 기준을 지우고 다시 공통 기준을 씁니다. 지운 내용은 프로젝트 폴더에 백업 파일로 남습니다.")
        if st.checkbox("공통 기준을 따릅니다", key="pol_common_ok") and st.button("공통 기준 따르기", icon=":material/undo:"):
            DP.use_common(proj)
            st.rerun()
elif not proj:
    with st.expander("공통 기준을 개발 기준안(블룸 인지 수준)으로 되돌리기", icon=":material/restart_alt:"):
        st.caption("공통 기준을 처음 기준안으로 돌립니다. 상태는 '임시'가 됩니다. 바꾼 기록은 남습니다. "
                   "프로젝트 전용 기준은 바뀌지 않습니다.")
        if st.checkbox("되돌립니다", key="pol_reset_ok") and st.button("되돌리기", icon=":material/restart_alt:"):
            DP.reset(last_project().get("reviewer", ""))
            st.rerun()

hist = (pol.get("history") or []) if (target == "common" or own) else []
if hist:
    st.subheader(":material/history: 바꾼 기록")
    st.dataframe(pd.DataFrame([{"일시": h["at"][:16].replace("T", " "), "상태": h["status"], "확인자": h.get("by", ""),
                                "출처": h.get("source", ""), "메모": h.get("note", "")} for h in reversed(hist)]),
                 hide_index=True, width="stretch")
