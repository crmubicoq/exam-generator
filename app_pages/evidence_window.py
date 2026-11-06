"""원고 근거 새 창 — 검수 화면에서 '원고 근거를 새 창으로 보기'를 켜고 연 창. 검수 화면에서 고른 문항을 자동으로 따라간다.

검수 화면이 지금 문항을 기억값(ui_state.json의 review_current)에 남기고, 이 창은 2초마다 확인해 바뀌면 다시 그린다.
듀얼 모니터에서 한쪽에 검수, 한쪽에 원고 근거를 크게 띄우는 용도(담당자 피드백 2026-11-05).
"""
import streamlit as st

from exam_gen.review_view import evidence_panel, question_html
from exam_gen.store import Project, last_project, list_projects

# 이 창에서는 사이드바 없이 원고를 넓게 쓴다
st.markdown("<style>section[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl']{display:none!important}"
            ".block-container{padding-top:1.2rem}</style>", unsafe_allow_html=True)


@st.fragment(run_every=2)
def follow():
    cur = last_project().get("review_current") or {}
    slug, key = cur.get("project"), cur.get("key")
    if not slug or slug not in list_projects() or not key:
        st.info("검수 화면에서 **'원고 근거를 새 창으로 보기'**를 켜고 문항을 고르면 여기에 원고 근거가 나옵니다.",
                icon=":material/open_in_new:")
        return
    p = Project(slug)
    item = p.item(key)
    if not item:
        st.warning(f"문항 {key}를 찾지 못했습니다.")
        return
    st.markdown(f"#### :material/menu_book: {key} 원고 근거 &nbsp;<span style='font-size:.6em;color:#888;font-weight:400'>"
                "검수 화면에서 문항을 바꾸면 1~2초 안에 따라 바뀝니다</span>", unsafe_allow_html=True)
    left, right = st.columns([3.2, 6.8], gap="large")
    with left:
        with st.container(border=True):
            st.markdown(question_html(item), unsafe_allow_html=True)
            if item.get("explanation"):  # 담당자 피드백 2026-11-06: 새 창에서도 해설을 함께 본다
                st.markdown("**해설**")
                st.markdown(item["explanation"])
    with right:
        # 키에 문항·수정 시각을 넣어 문항이 바뀔 때만 뷰어를 새로 그린다(2초마다 깜빡이지 않게)
        evidence_panel(p, item, key_prefix=f"win_{key}", viewer_height=900)


follow()
