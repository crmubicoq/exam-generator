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
ui.header("출제 진행", "autorenew", "배치별 진행률, 관문별 탈락, 원고 전체 커버리지를 확인한다.")
batches = p.batches()
if not batches:
    st.info("출제 옵션 화면에서 출제를 시작하세요.")
    st.stop()
KIND = {"initial": "최초", "additional": "추가", "repair": "재출제"}
ids = [b["batch"] for b in batches]
cur = st.session_state.get("batch", ids[-1])
sel = st.selectbox("배치", ids, index=ids.index(cur) if cur in ids else len(ids) - 1,
                   format_func=lambda b: f"배치 {b} · {KIND.get(batches[ids.index(b)]['kind'], '')} · "
                                         f"{len(batches[ids.index(b)]['slots'])}슬롯 · {batches[ids.index(b)]['status']}")
st.session_state["batch"] = sel
batch = batches[ids.index(sel)]


@st.fragment(run_every=2)
def live():
    pr = p.read_progress(sel)
    state = batch_state(p, sel)
    if not pr:
        st.info("아직 진행 정보가 없다.")
        if state in RESUMABLE and st.button("출제 시작(이 배치)", type="primary"):
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
        eta = f" · 예상 남은 시간 약 {max(1, round(el / got * (total - got) / 60))}분"
    st.progress(min(got / total, 1.0),
                text=f"{got}/{total} 슬롯 처리 · 페이지 {pr.get('pages_done', 0)}/{pr.get('pages_total', 0)}{eta}")
    if status in ("running", "running_here"):
        st.caption("끝나거나 멈추면 윈도우 알림이 뜹니다. 이 화면을 닫아도 출제는 계속됩니다(대시보드 실행 창은 닫지 마세요).")
    c = st.columns(6)
    c[0].metric("상태", {"running": "진행 중", "running_here": "진행 중", "done": "완료", "stopped": "중지됨", "error": "오류",
                        "partial": "부분 완료", "orphan": "중단됨"}.get(status, status), border=True)
    c[1].metric("통과", pr.get("accepted", 0), border=True)
    c[2].metric("경고 포함", pr.get("warn", 0), border=True)
    c[3].metric("수동 확인", pr.get("manual", 0), border=True)
    c[4].metric("재출제 슬롯", pr.get("retries", 0), border=True)
    ft = pr.get("first_try", {})
    rate = ft.get("g2_pass", 0) / ft["items"] if ft.get("items") else 0
    c[5].metric("첫 시도 근거 매칭률", f"{rate:.0%}", border=True)
    if state == "orphan":
        st.error("실행 주체가 없는 배치다(서버 재시작 등으로 중단됨). '이어서 출제'로 남은 슬롯을 재개할 수 있다.")
    elif state == "running_elsewhere":
        st.info("다른 프로세스(다른 서버 창)에서 실행 중이다. 이 화면에서는 중지·재개할 수 없다.")
    elif pr.get("message"):
        (st.error if status in ("stopped", "error", "partial") else st.info)(pr["message"])
    gf = pr.get("gate_fail", {})
    st.caption("관문별 탈락(시도 기준): " + " · ".join(f"{k} {v}" for k, v in gf.items()) +
               f" · LLM 호출 {pr.get('usage', {}).get('calls', 0)}회")
    if status in ("done", "partial") and st.button("품질 리포트 보기", icon=":material/analytics:", key="to_quality"):
        st.switch_page("app_pages/quality.py")
    if state == "running_here":
        if st.button("중지", type="secondary"):
            stop_batch(p, sel)
    elif state in RESUMABLE:
        if st.button("이어서 출제(남은 슬롯)", type="primary"):
            try:
                resume_batch(p, llm_settings.load(), sel)
            except RuntimeError as e:
                st.error(str(e))
            st.rerun()


live()

# ---------- 분포·커버리지 ----------
st.subheader(":material/bar_chart: 분포와 커버리지")
items = [it for it in p.items() if it["status"] != "deleted"]
bi = [it for it in items if it["batch"] == sel]
if bi:
    c1, c2, c3 = st.columns(3)
    c1.markdown("**상태**")
    c1.dataframe(pd.Series(Counter(ui.STATUS_KO[it["status"]] for it in bi), name="문항"), width="stretch")
    c2.markdown("**난이도 (목표 슬롯 / 통과)**")
    want = Counter(s["difficulty"] for s in batch["slots"])
    okd = Counter(it["difficulty"] for it in bi if it["status"] != "manual")
    c2.dataframe(pd.DataFrame({"목표": want, "통과": okd}).reindex(criteria()["difficulty_levels"]).fillna(0).astype(int),
                 width="stretch")
    c3.markdown("**유형 (목표 / 통과)**")
    wt = Counter(ui.TYPE_KO[s["type"]] for s in batch["slots"])
    okt = Counter(ui.TYPE_KO[it["type"]] for it in bi if it["status"] != "manual")
    c3.dataframe(pd.DataFrame({"목표": wt, "통과": okt}).fillna(0).astype(int), width="stretch")

st.markdown("**페이지 커버리지 (전체 프로젝트, 수동 확인 제외)** — 원고 전체에 고르게 출제되었는지 확인")
good = [it for it in items if it["status"] != "manual"]
counts = Counter(it["page"] for it in good)
cov = coverage_stats(p.pages, counts)
df = pd.DataFrame([{"책 쪽수": x["label"], "문항 수": counts.get(x["g"], 0),
                    "기대값": round(cov["expected"].get(x["g"], 0), 1)} for x in p.pages if not x["excluded"]])
ui.bar_chart(df, "책 쪽수", ["문항 수", "기대값"], stack=False, height=280)
if cov["zero"]:
    st.error("문항이 없는 페이지: " + ", ".join(p.pages[g]["label"] for g in cov["zero"]) +
             " → 출제 옵션 > '페이지 재출제(강제 할당)'로 보완하세요.")
if cov["dense"]:
    st.warning("기대보다 문항이 많이 몰린 페이지: " + ", ".join(p.pages[g]["label"] for g in cov["dense"]))
imp = [it for it in good if any("지엽" in w for w in it["validation"].get("warnings", []))]
if imp:
    st.warning(f"지엽성 경고 문항 {len(imp)}개 — 검수 화면에서 '경고' 필터로 확인하세요.")

with st.expander("탈락 기록 (최근 200건)", icon=":material/history:"):
    ev = [e for e in p.events(batch=sel, limit=200) if e["kind"] == "gate_fail"]
    if ev:
        st.dataframe(pd.DataFrame([{"시각": e["ts"], "슬롯": e["detail"].get("sid"), "시도": e["detail"].get("attempt"),
                                    "사유": " / ".join(e["detail"].get("fails", [])), "발문": e["detail"].get("stem")}
                                   for e in ev]), width="stretch", hide_index=True)
    else:
        st.caption("탈락 기록 없음")
