"""홈 — 처음 쓰는 사람을 위한 안내: 지금 할 일, 단계별 진행 상태, 사용 안내·용어 풀이."""
from collections import Counter

import streamlit as st

from exam_gen import ui
from exam_gen.generate.generator import batch_state

ui.header("평가문항 자동 출제", "quiz",
          "원고를 올리면 원고 전체에 고르게 문항을 출제하고, 자동 검증과 사람 검수를 거쳐 양식 엑셀과 근거PDF로 내보낸다.")

p = ui.current_project()

# ---------- 프로젝트 상태 → 단계별 진행 상태와 '지금 할 일' ----------
DONE, DOING, TODO = "done", "doing", "todo"
state = {k: TODO for k in ("register", "options", "progress", "review", "export")}
nxt = ("왼쪽 사이드바의 **'새 프로젝트'**에서 과정명을 입력해 프로젝트를 만드세요.", None, "add")
c = Counter()
if p:
    m = p.meta
    items = p.items()
    c = Counter(it["status"] for it in items)
    batches = p.batches()
    states = [batch_state(p, b["batch"]) for b in batches]
    pending = c.get("manual", 0) + c.get("warn", 0) + c.get("auto_pass", 0)
    exports = list((p.dir / "exports").glob("*")) if (p.dir / "exports").exists() else []

    if m["sources"] and p.page_map_ready():
        state["register"] = DONE
    elif m["sources"]:
        state["register"] = DOING
    if batches:
        state["options"] = DONE
    if any(s in ("running_here", "running_elsewhere") for s in states):
        state["progress"] = DOING
    elif batches:
        state["progress"] = DONE
    if items and pending == 0 and c.get("approved"):
        state["review"] = DONE
    elif c.get("approved"):
        state["review"] = DOING
    if exports:
        state["export"] = DONE

    if not m["sources"]:
        nxt = ("**원고 등록**에서 원고 파일(PDF·PPTX·DOCX·HWP)을 올리세요.", "app_pages/register.py", "upload_file")
    elif not p.page_map_ready():
        nxt = ("**원고 등록**에서 책 쪽수 매핑을 확인하고 '이 매핑으로 확정'을 누르세요. 확정해야 출제할 수 있습니다.",
               "app_pages/register.py", "format_list_numbered")
    elif not batches:
        nxt = ("**출제 옵션**에서 문항 수와 난이도 비율을 정하고 '출제 시작'을 누르세요.", "app_pages/options.py", "tune")
    elif state["progress"] == DOING:
        nxt = ("출제가 진행 중입니다. **출제 진행**에서 진행률을 볼 수 있습니다(300문항 기준 20~40분).",
               "app_pages/progress.py", "autorenew")
    elif any(s in ("stopped", "partial", "error", "orphan") for s in states):
        nxt = ("중단되거나 일부만 끝난 출제가 있습니다. **출제 진행**에서 '이어서 출제'를 누르세요.",
               "app_pages/progress.py", "replay")
    elif pending:
        nxt = (f"검수할 문항이 **{pending}개** 남았습니다. **검수**에서 확인하고 승인하세요. "
               "승인한 문항만 최종 출력에 들어갑니다.", "app_pages/review.py", "fact_check")
    elif c.get("approved"):
        nxt = (f"승인된 문항 **{c['approved']}개**를 **출력**에서 엑셀과 근거PDF로 내려받으세요.",
               "app_pages/export.py", "download")

# ---------- 지금 할 일 ----------
with st.container(border=True):
    a, b = st.columns([5, 1.3], vertical_alignment="center")
    a.markdown(f"#### :material/flag: 지금 할 일\n{nxt[0]}")
    if nxt[1] and b.button("바로 가기", type="primary", icon=f":material/{nxt[2]}:", width="stretch"):
        st.switch_page(nxt[1])

# ---------- 현재 프로젝트 현황 ----------
if p:
    with st.container(border=True):
        st.markdown(f"**현재 프로젝트** · {p.meta['name']}")
        cols = st.columns(6)
        for col, (label, val, help_) in zip(cols, [
                ("원고 페이지", len(p.pages), "등록된 원고의 전체 페이지 수"),
                ("전체 문항", sum(c.values()), "출제된 모든 문항(삭제 포함)"),
                ("승인", c.get("approved", 0), "사람이 승인한 문항 — 이 문항만 최종 출력된다"),
                ("자동통과", c.get("auto_pass", 0), "자동 검증을 경고 없이 통과 — 훑어보고 일괄 승인 가능"),
                ("경고", c.get("warn", 0), "자동 검증은 통과했지만 확인할 점이 있음"),
                ("수동확인", c.get("manual", 0), "자동 검증을 끝내 통과하지 못함 — 수정하거나 삭제")]):
            col.metric(label, val, border=True, help=help_)

# ---------- 단계별 진행 ----------
BADGE = {DONE: ":green-badge[:material/check: 완료]", DOING: ":orange-badge[:material/pending: 진행 중]",
         TODO: ":gray-badge[대기]"}
STEPS = [
    ("register", "app_pages/register.py", "upload_file", "1. 원고 등록",
     "원고 파일을 올리고, 원고에 인쇄된 쪽수가 맞는지 확인한다. 표지·목차처럼 출제하지 않을 페이지를 뺀다.",
     "끝나면: 쪽수 매핑 '확인됨'"),
    ("options", "app_pages/options.py", "tune", "2. 출제 옵션",
     "몇 문항을 낼지, 난이도(최상·상·중·하)와 유형(선다·진위·단답) 비율을 정한다. 페이지별 문항 수는 자동으로 나눈다.",
     "끝나면: '출제 시작'"),
    ("progress", "app_pages/progress.py", "autorenew", "3. 출제 진행",
     "AI가 페이지마다 문항을 만들고 자동 검증한다. 진행률과 원고 전체에 고르게 나왔는지 확인한다.",
     "끝나면: 상태 '완료'"),
    ("review", "app_pages/review.py", "fact_check", "4. 검수",
     "문항과 원고 근거(하이라이트)를 나란히 보고 승인·수정·삭제한다. 승인해야 최종 출력에 들어간다.",
     "끝나면: 검수 대기 0"),
    ("export", "app_pages/export.py", "download", "5. 출력",
     "승인된 문항을 양식 엑셀과 근거PDF(원고에 출처 표시)로 내려받는다.",
     "끝나면: 파일 다운로드"),
]
cols = st.columns(5)
for col, (k, page, icon, title, desc, done_when) in zip(cols, STEPS):
    with col.container(border=True, height="stretch"):
        st.page_link(page, label=title, icon=f":material/{icon}:")
        st.markdown(BADGE[state[k]])
        st.caption(desc)
        st.caption(f":material/subdirectory_arrow_right: {done_when}")

# ---------- 처음 사용 안내 ----------
with st.expander("처음 사용하시나요? — 사용 안내", icon=":material/help:", expanded=not p):
    t1, t2, t3 = st.tabs(["사용 순서", "용어 풀이", "자주 묻는 질문"])
    with t1:
        st.markdown("""
1. **LLM 설정 확인** — 왼쪽 아래 **'LLM 설정'**을 펼쳐 사용할 AI(Claude 또는 Codex)를 고르고 **'연결·로그인 확인'**을 누른다.
   처음 한 번만 하면 된다.
2. **프로젝트 만들기** — 왼쪽 **'새 프로젝트'**에 과정명을 입력한다. 원고(교재) 하나당 프로젝트 하나.
3. **원고 등록** — 원고 파일을 올리고 **쪽수 매핑을 확정**한다. 엑셀의 'PPT' 열과 근거PDF에 이 쪽수가 쓰인다.
4. **출제 옵션** — 필요 문항 수(기본 300)와 난이도·유형 비율을 정하고 **'출제 시작'**. 원고 전체에 페이지별로 고르게 배분된다.
5. **출제 진행** — 끝날 때까지 기다린다. 중간에 멈춰도 **'이어서 출제'**로 남은 부분만 다시 출제한다.
6. **검수** — **'확인 필요'** 문항부터 하나씩 보고 승인한다. **'자동통과'** 문항은 훑어본 뒤 **'일괄 승인'**할 수 있다.
7. **출력** — 엑셀과 근거PDF를 내려받는다. 승인한 문항만 들어간다.

추가로 더 필요하면 **출제 옵션 → '추가 출제'**를 고른다. 기존 문항과 겹치지 않게 출제된다.
""")
    with t2:
        st.markdown("""
| 용어 | 뜻 |
|---|---|
| :blue-badge[:material/task_alt: 자동통과] | 자동 검증(G1~G6)을 경고 없이 모두 통과한 문항. 승인 전이다. |
| :orange-badge[:material/warning: 경고] | 자동 검증은 통과했지만 사람이 확인할 점이 있는 문항(예: 표시 난이도와 실제 난이도 차이). |
| :red-badge[:material/error: 수동확인] | 여러 번 다시 출제해도 자동 검증을 통과하지 못한 문항. 고치거나 삭제한다. |
| :green-badge[:material/check_circle: 승인] | 사람이 확인하고 승인한 문항. **이 문항만 엑셀·근거PDF에 들어간다.** |
| 근거 L2 | 문항의 근거 문장이 원고와 글자 그대로 일치한다. |
| 근거 L1 | 띄어쓰기·조사 정도만 다르다. 승인할 때 원고 하이라이트를 직접 확인해야 한다. |
| 근거PDF | 원고 위에 각 문항의 출처 문장을 노랑(정답 근거)·주황(함정이 바꾼 원문)으로 표시한 PDF. |

**자동 검증 관문**
- **G1 구조**: 정답 위치·옳은/옳지 않은 것 고르기 등 출제 지시대로 만들어졌는지
- **G2 근거**: 근거 문장을 원고에서 실제로 찾았는지(못 찾거나 숫자·의미가 바뀌면 탈락)
- **G3 규칙**: 문어체, 단답형 조사 힌트, 표현 정확도 등 출제 지침
- **G4 독립 풀이**: 정답을 모르는 AI가 원고만 보고 다시 풀어 같은 답이 나오는지
- **G5 검수**: 지침·유의사항 체크리스트와 실제 난이도 판정
- **G6 중복**: 이미 있는 문항과 겹치지 않는지
- **G7 사람 승인**: 검수 화면에서 사람이 승인
""")
    with t3:
        st.markdown("""
**Q. 300문항을 출제하는 데 얼마나 걸리나요?**
원고 분량과 AI에 따라 다르지만 대략 20~40분입니다. 출제하는 동안 다른 작업을 해도 됩니다.

**Q. 비용이 드나요?**
'Codex 구독 로그인'이나 'Claude 구독 로그인'은 각 구독 사용량만 씁니다. 'Anthropic API'는 300문항에 대략 $10~15입니다.

**Q. 출처(근거)는 어떻게 확인하나요?**
검수 화면의 **'원고 근거'** 탭에서 원고의 해당 위치로 바로 이동합니다. 출력의 근거PDF에도 같은 표시가 들어갑니다.

**Q. 특정 페이지에 문항이 몰리거나 빠졌어요.**
출제 진행 화면의 커버리지 그래프에서 확인하고, **출제 옵션 → '페이지 재출제(강제 할당)'**로 해당 페이지만 다시 출제합니다.

**Q. 문항을 고치고 싶어요.**
검수 화면에서 **'문항 수정'**을 펼쳐 고친 뒤 저장하고, **'LLM 재검증'**을 누른 다음 승인합니다.
""")
