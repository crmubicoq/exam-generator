"""3. 출제 진행 — 진행률, 관문별 통과·탈락, 커버리지(편중) 리포트, 중지·재개."""
from collections import Counter

import pandas as pd
import streamlit as st

from exam_gen import ui
from exam_gen.config import criteria
from exam_gen.generate.generator import RESUMABLE, batch_state, resume_batch, stop_batch
from exam_gen.llm import settings as llm_settings
from exam_gen.plan.coverage import coverage_stats

p = ui.require_project(ui.current_project())
ui.header("출제 진행", "autorenew", "출제가 얼마나 진행됐는지, 원고 전체에 고르게 나왔는지 확인합니다. 이 화면을 닫아도 출제는 계속됩니다.")
batches = p.batches()
if not batches:
    st.info("출제 옵션 화면에서 출제를 시작하세요.")
    st.stop()
KIND = {"initial": "최초 출제", "additional": "추가 출제", "repair": "페이지 재출제", "revise": "원고 변경 처리"}
STATUS = {"running": "진행 중", "done": "완료", "stopped": "중지됨", "error": "오류", "partial": "부분 완료", "pending": "대기"}
ids = [b["batch"] for b in batches]
cur = st.session_state.get("batch", ids[-1])
sel = st.selectbox("출제 회차", ids, index=ids.index(cur) if cur in ids else len(ids) - 1,
                   help="출제 옵션에서 '출제 시작'을 누를 때마다 회차가 하나씩 늘어납니다.",
                   format_func=lambda b: f"{b}회차 · {KIND.get(batches[ids.index(b)]['kind'], '')} · "
                                         f"{len(batches[ids.index(b)]['slots'])}문항 · "
                                         f"{STATUS.get(batches[ids.index(b)]['status'], batches[ids.index(b)]['status'])}")
st.session_state["batch"] = sel
batch = batches[ids.index(sel)]


@st.fragment(run_every=2)
def live():
    pr = p.read_progress(sel)
    state = batch_state(p, sel)
    if not pr:
        st.info("아직 진행 정보가 없습니다.")
        if state in RESUMABLE and st.button("출제 시작(이 회차)", type="primary"):
            resume_batch(p, llm_settings.load(), sel)
            st.rerun()
        return
    status = {"orphan": "orphan", "running_elsewhere": "running"}.get(state, pr.get("status"))
    total = pr.get("slots_total", 0) or 1
    got = pr.get("accepted", 0) + pr.get("manual", 0)
    eta = ""
    if status in ("running", "running_here") and got and pr.get("started"):
        from datetime import datetime
        el = (datetime.now() - datetime.fromisoformat(pr["started"])).total_seconds()
        left = max(1, round(el / got * (total - got) / 60))
        eta = f" · 예상 남은 시간 약 {left // 60}시간 {left % 60}분" if left >= 60 else f" · 예상 남은 시간 약 {left}분"
    st.progress(min(got / total, 1.0),
                text=f"{got}/{total}문항 처리 · 페이지 {pr.get('pages_done', 0)}/{pr.get('pages_total', 0)}{eta}")
    if status in ("running", "running_here"):
        st.caption("끝나거나 멈추면 윈도우 알림이 뜹니다. 이 화면을 닫아도 출제는 계속됩니다(대시보드 실행 창은 닫지 마세요).")
    c = st.columns(4)
    c[0].metric("상태", {"running": "진행 중", "running_here": "진행 중", "done": "완료", "stopped": "중지됨", "error": "오류",
                        "partial": "부분 완료", "orphan": "중단됨"}.get(status, status), border=True)
    c[1].metric("자동 검증 통과", pr.get("accepted", 0), border=True,
                help=f"이 중 확인할 점이 있는 '경고' 문항 {pr.get('warn', 0)}개 포함")
    c[2].metric("수동확인", pr.get("manual", 0), border=True,
                help="여러 번 다시 출제해도 자동 검증을 통과하지 못한 문항 — 검수 화면에서 고치거나 삭제합니다")
    c[3].metric("다시 출제한 횟수", pr.get("retries", 0), border=True,
                help="자동 검증에서 탈락해 같은 조건으로 다시 만든 횟수")
    if state == "orphan":
        st.error("출제가 중간에 끊겼습니다(프로그램을 다시 켜는 등). '이어서 출제'를 누르면 남은 문항만 출제합니다.")
    elif state == "running_elsewhere":
        st.info("다른 대시보드 창에서 출제 중입니다. 이 화면에서는 중지·재개할 수 없습니다.")
    elif pr.get("message"):
        (st.error if status in ("stopped", "error", "partial") else st.info)(pr["message"])
    gf = pr.get("gate_fail", {})
    GATE = {"G1": "형식", "G2": "근거", "G3": "규칙", "G4": "독립 풀이", "G5": "AI 검수", "G6": "중복", "누락": "응답 누락"}
    st.caption("자동 검증 탈락 횟수: " + " · ".join(f"{GATE.get(k, k)} {v}" for k, v in gf.items() if v) +
               f" · AI 호출 {pr.get('usage', {}).get('calls', 0)}회"
               + (f" · 토큰 입력 {pr['usage']['input_tokens'] / 1e4:,.0f}만·출력 {pr['usage'].get('output_tokens', 0) / 1e4:,.0f}만"
                  if (pr.get("usage") or {}).get("input_tokens") else ""))
    if status in ("done", "partial"):
        manual = pr.get("manual", 0)
        st.success("출제가 끝났습니다. 다음 순서: **품질 리포트**에서 보완할 점 확인 → **검수**에서 문항 확인·승인 → **출력**."
                   + (f" 수동확인 {manual}문항은 검수 화면의 '확인 필요'에서 고치거나 삭제하세요." if manual else ""),
                   icon=":material/task_alt:")
        n1, n2, _ = st.columns([1.2, 1.2, 2])
        if n1.button("다음: 품질 리포트", type="primary", icon=":material/analytics:", key="to_quality", width="stretch"):
            st.switch_page("app_pages/quality.py")
        if n2.button("검수로 바로 가기", icon=":material/fact_check:", key="to_review", width="stretch"):
            st.switch_page("app_pages/review.py")
    if state == "running_here":
        if st.button("중지", type="secondary"):
            stop_batch(p, sel)
    elif state in RESUMABLE:
        if st.button("이어서 출제(남은 문항)", type="primary"):
            try:
                resume_batch(p, llm_settings.load(), sel)
            except RuntimeError as e:
                st.error(str(e))
            st.rerun()


live()

# ---------- 분포·커버리지 ----------
st.subheader(":material/bar_chart: 출제 결과 분포")
items = [it for it in p.items() if it["status"] != "deleted"]
bi = [it for it in items if it["batch"] == sel]
if bi:
    c1, c2, c3 = st.columns(3)
    c1.markdown("**상태**")
    c1.dataframe(pd.Series(Counter(ui.STATUS_KO[it["status"]] for it in bi), name="문항"), width="stretch")
    c2.markdown("**난이도 (목표 / 통과)**")
    want = Counter(s["difficulty"] for s in batch["slots"])
    okd = Counter(it["difficulty"] for it in bi if it["status"] != "manual")
    c2.dataframe(pd.DataFrame({"목표": want, "통과": okd}).reindex(criteria()["difficulty_levels"]).fillna(0).astype(int),
                 width="stretch")
    c3.markdown("**유형 (목표 / 통과)**")
    wt = Counter(ui.TYPE_KO[s["type"]] for s in batch["slots"])
    okt = Counter(ui.TYPE_KO[it["type"]] for it in bi if it["status"] != "manual")
    c3.dataframe(pd.DataFrame({"목표": wt, "통과": okt}).fillna(0).astype(int), width="stretch")

st.markdown("**페이지별 문항 수** — 원고 전체에 고르게 출제되었는지 봅니다(이 프로젝트 전체, 수동확인 제외). "
            "'기대값'은 분량에 맞춘 목표입니다.")
good = [it for it in items if it["status"] != "manual"]
counts = Counter(it["page"] for it in good)
cov = coverage_stats(p.pages, counts)
df = pd.DataFrame([{"책 쪽수": x["label"], "문항 수": counts.get(x["g"], 0),
                    "기대값": round(cov["expected"].get(x["g"], 0), 1)} for x in p.pages if not x["excluded"]])
ui.bar_chart(df, "책 쪽수", ["문항 수", "기대값"], stack=False, height=280)
if cov["zero"]:
    st.error("문항이 없는 페이지: " + ", ".join(p.pages[g]["label"] for g in cov["zero"]) +
             " → 출제 옵션에서 '페이지 재출제'를 골라 이 페이지들만 다시 출제할 수 있습니다.")
if cov["dense"]:
    st.warning("기대보다 문항이 많이 몰린 페이지: " + ", ".join(p.pages[g]["label"] for g in cov["dense"]))
imp = [it for it in good if any("지엽" in w for w in it["validation"].get("warnings", []))]
if imp:
    st.warning(f"지엽성 경고 문항 {len(imp)}개 — 검수 화면에서 '경고' 필터로 확인하세요.")

with st.expander("자동 검증 탈락 기록 (최근 200건) — 왜 다시 출제했는지", icon=":material/history:"):
    ev = [e for e in p.events(batch=sel, limit=200) if e["kind"] == "gate_fail"]
    if ev:
        st.dataframe(pd.DataFrame([{"시각": e["ts"], "출제 칸": e["detail"].get("sid"), "시도": e["detail"].get("attempt"),
                                    "사유": " / ".join(e["detail"].get("fails", [])), "발문": e["detail"].get("stem")}
                                   for e in ev]), width="stretch", hide_index=True)
    else:
        st.caption("탈락 기록이 없습니다.")
