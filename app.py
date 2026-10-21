"""평가문항 자동 출제 — 진입점.  실행: streamlit run app.py

화면 틀(메뉴 그룹·아이콘)은 st.navigation으로 구성하고, 프로젝트 선택·LLM 설정 사이드바는 여기서 한 번만 그린다.
"""
import streamlit as st

from exam_gen import ui

st.set_page_config(page_title="평가문항 자동 출제", page_icon=":material/quiz:", layout="wide")

PAGES = {
    "시작": [st.Page("app_pages/home.py", title="홈", icon=":material/home:", default=True)],
    "준비": [st.Page("app_pages/register.py", title="원고 등록", icon=":material/upload_file:"),
             st.Page("app_pages/options.py", title="출제 옵션", icon=":material/tune:")],
    "출제": [st.Page("app_pages/progress.py", title="출제 진행", icon=":material/autorenew:")],
    "검수·출력": [st.Page("app_pages/quality.py", title="품질 리포트", icon=":material/analytics:"),
                 st.Page("app_pages/review.py", title="검수", icon=":material/fact_check:"),
                 st.Page("app_pages/export.py", title="출력", icon=":material/download:")],
}

nav = st.navigation(PAGES)
st.markdown("<style>.block-container{padding-top:2.2rem;padding-bottom:1rem}</style>", unsafe_allow_html=True)
ui.render_sidebar()
nav.run()
