"""홈 — 작업 흐름 안내와 현재 프로젝트 현황."""
from collections import Counter

import streamlit as st

from exam_gen import ui

ui.header("평가문항 자동 출제", "quiz", "원고 → 출제 옵션 → 출제(자동 검증 G1~G6) → 사람 검수(G7) → 엑셀·근거PDF")

p = ui.current_project()
if p:
    m = p.meta
    items = p.items()
    c = Counter(it["status"] for it in items)
    with st.container(border=True):
        st.markdown(f"**현재 프로젝트** · {m['name']}")
        cols = st.columns(6)
        for col, (label, val) in zip(cols, [("원고 페이지", len(p.pages)), ("전체 문항", len(items)),
                                             ("승인", c.get("approved", 0)), ("자동통과", c.get("auto_pass", 0)),
                                             ("경고", c.get("warn", 0)), ("수동확인", c.get("manual", 0))]):
            col.metric(label, val, border=True)
        if m["sources"] and not p.page_map_ready():
            st.warning("원고 등록 화면에서 책 쪽수 매핑을 확정해야 출제를 시작할 수 있다.", icon=":material/warning:")

STEPS = [
    ("app_pages/register.py", "upload_file", "1. 원고 등록",
     "PDF·PPTX·DOCX·HWP 업로드, 책 쪽수 매핑 확인, 차시 지정, 출제 제외 페이지"),
    ("app_pages/options.py", "tune", "2. 출제 옵션",
     "문항 수, 난이도 %(최상·상·중·하), 유형 비율, 페이지 할당(자동/강제), 추가 출제"),
    ("app_pages/progress.py", "autorenew", "3. 출제 진행",
     "진행률, 관문별 통과·탈락, 페이지 커버리지, 중지·재개"),
    ("app_pages/review.py", "fact_check", "4. 검수",
     "문항별 원고 근거 하이라이트 확인, 수정·승인·삭제"),
    ("app_pages/export.py", "download", "5. 출력",
     "양식 엑셀, 근거PDF(문항별 추출본·원고 전체 표시본), 검증 리포트"),
]
cols = st.columns(5)
for col, (page, icon, title, desc) in zip(cols, STEPS):
    with col.container(border=True, height="stretch"):
        st.page_link(page, label=title, icon=f":material/{icon}:")
        st.caption(desc)

with st.expander("자동 검증 관문 — 문항 하나가 통과해야 하는 단계", icon=":material/verified_user:"):
    st.markdown("""
- **G1 구조**: 슬롯 명세(유형·정답 위치·정답 방향)와 문항 구조·정답 논리 일치
- **G2 근거**: 근거 인용문을 원고에서 찾아 줄 위치·책 쪽수를 코드가 확정 — 못 찾거나 의미가 바뀌면 탈락
- **G3 규칙**: 문어체, 단답 조사 힌트, 조합형 '모두', 진위형 O 해설, 표현 정확도(의무·금지·가능·권고)
- **G4 독립 풀이**: 정답을 숨기고 원고만으로 다시 풀어 출제 정답·보기별 참거짓과 대조
- **G5 LLM 검수**: 지침 검토 항목 + 출제 유의사항 체크리스트 + 실제 난이도 판정
- **G6 중복**: 기존 채택 문항과 유사도·근거 위치 비교
- **G7 사람 승인**: 현재 내용으로 검증되고 승인된 문항만 최종 출력
""")
