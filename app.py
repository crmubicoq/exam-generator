"""평가문항 자동 출제 — 대시보드 홈.  실행: streamlit run app.py"""
from collections import Counter

import streamlit as st

from exam_gen import ui

ui.setup("홈")
p = ui.sidebar()

st.title("📝 평가문항 자동 출제")
st.caption("원고 → 출제 옵션 → 출제(자동 검증 G1~G6) → 사람 검수(G7) → 엑셀·근거PDF")

st.markdown("""
| 단계 | 화면 | 하는 일 |
|---|---|---|
| 1 | **원고 등록** | 원고 업로드(PDF·PPTX·DOCX·HWP), 책 쪽수 매핑 확인, 차시 지정, 출제 제외 페이지 |
| 2 | **출제 옵션** | 문항 수, 난이도 % (최상·상·중·하), 유형 비율, 페이지별 할당(자동/강제), 추가 출제 |
| 3 | **출제 진행** | 진행률, 관문별 통과·탈락, 커버리지, 중지·재개 |
| 4 | **검수** | 문항별 원고 근거 하이라이트 확인, 수정·승인·삭제 |
| 5 | **출력** | 양식 엑셀, 근거PDF(문항별 추출본·원고 전체 표시본), 검증 리포트 |
""")

with st.expander("자동 검증 관문 (문항 하나가 통과해야 하는 단계)"):
    st.markdown("""
- **G1 구조**: 슬롯 명세(유형·정답 위치·정답 방향)와 문항 구조·정답 논리 일치
- **G2 근거**: 근거 인용문을 원고에서 찾아 줄 위치·책 쪽수를 코드가 확정 — 못 찾으면 탈락
- **G3 규칙**: 문어체, 단답 조사 힌트, 조합형 '모두', 진위형 O 해설, 표현 정확도(의무·금지·가능·권고) 등
- **G4 독립 풀이**: 정답을 숨기고 원고만으로 다시 풀어 출제 정답·보기별 참거짓과 대조
- **G5 LLM 검수**: 지침 검토 항목 + 출제 유의사항 체크리스트
- **G6 중복**: 기존 전체 문항과 유사도·근거 위치 비교
- **G7 사람 승인**: 승인한 문항만 최종 출력
""")

if p:
    m = p.meta
    items = p.items()
    st.subheader(f"현재 프로젝트: {m['name']}")
    c = Counter(it["status"] for it in items)
    cols = st.columns(6)
    cols[0].metric("원고 페이지", len(p.pages))
    cols[1].metric("전체 문항", len(items))
    cols[2].metric("승인", c.get("approved", 0))
    cols[3].metric("자동통과", c.get("auto_pass", 0))
    cols[4].metric("경고", c.get("warn", 0))
    cols[5].metric("수동확인", c.get("manual", 0))
    if m["sources"] and not p.page_map_ready():
        st.warning("원고 등록 화면에서 책 쪽수 매핑을 확인해야 출제를 시작할 수 있다.")
