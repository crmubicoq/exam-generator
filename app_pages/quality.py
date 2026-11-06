"""품질 리포트 — 출제 결과 자체 평가: 항목별 등급, 목표 대비 분포, 문제 문항 목록."""
import pandas as pd
import streamlit as st

from exam_gen import ui
from exam_gen.config import criteria
from exam_gen.quality_report import EXTRA_CATS, GRADE_RULES, LEVELS, actual_difficulty, analyze

GRADE_COLOR = {"A": "#1a7f37", "B": "#0969da", "C": "#bc4c00", "D": "#cf222e"}
GRADE_TEXT = {"A": "납품 수준", "B": "경미한 보완", "C": "보완 필요", "D": "재출제 권장"}
CAT_KO = {k: v[0] for k, v in GRADE_RULES.items()} | EXTRA_CATS
ACTION = {
    "accuracy": "검수 화면에서 해당 문항을 고치거나 삭제합니다.",
    "dispute": "검수 화면에서 '일반적으로', '가장 거리가 먼' 같은 단서 문구를 넣거나 문항을 삭제합니다.",
    "evidence": "근거가 부분 일치(L1)인 문항은 검수 화면 '원고 근거' 탭에서 하이라이트를 확인하고 승인합니다.",
    "coverage": "출제 옵션에서 '페이지 재출제'를 골라 문항이 없는 페이지만 다시 출제합니다.",
    "format": "검수 화면에서 문장을 고치고 'AI 재검증' 후 승인합니다.",
    "discrimination": "검수 화면에서 오답 보기를 원고의 다른 항목 내용으로 바꾸거나 문항을 삭제합니다.",
    "difficulty": "출제 옵션 → '추가 출제'에서 모자란 난이도(상·최상)만 수량을 지정해 보충합니다.",
}

p = ui.require_project(ui.current_project())
ui.header("품질 리포트", "analytics", "출제 결과를 지침·유의사항 기준으로 자동 점검한 결과입니다. 검수 전에 무엇을 보완할지 판단하는 데 씁니다. "
          "(같은 AI가 매긴 자체 평가이므로 사람 검수를 대신하지 않습니다.)")

batches = p.batches()
if not batches:
    st.info("아직 출제하지 않았습니다. 출제 옵션에서 출제를 시작하세요.")
    st.stop()
KIND = {"initial": "최초 출제", "additional": "추가 출제", "repair": "페이지 재출제", "revise": "원고 변경 처리"}
opts = [None] + [b["batch"] for b in batches]
sel = st.selectbox("범위", opts, format_func=lambda b: "전체 문항" if b is None else
                   f"{b}회차 · {KIND.get(batches[b - 1]['kind'] if b - 1 < len(batches) else '', '')}")
r = analyze(p, sel)
cnt = r["counts"]
if not cnt["accepted"]:
    st.info("자동 검증을 통과한 문항이 아직 없습니다.")
    st.stop()

# ---------- 종합 ----------
bad = [k for k, g in r["grades"].items() if g in ("C", "D")]
with st.container(border=True):
    st.markdown(f"#### :material/summarize: 종합 — 통과 {cnt['accepted']}문항 · 수동확인 {cnt['manual']} · 승인 {cnt['approved']}")
    if not bad:
        st.success("모든 항목이 B 이상입니다. 검수 화면에서 확인하고 승인하면 됩니다.", icon=":material/check_circle:")
    else:
        st.warning("보완이 필요한 항목: " + ", ".join(f"**{CAT_KO[k]}**({r['grades'][k]})" for k in bad), icon=":material/warning:")
        for k in bad:
            st.markdown(f"- **{CAT_KO[k]}** — {ACTION[k]}")

# ---------- 빠른 출제로 생략한 AI 검증 ----------
from exam_gen import review_ops as _ro  # noqa: E402
from exam_gen.llm import settings as _ls  # noqa: E402

_skipped = _ro.skipped_items(p)


@st.fragment(run_every=2)
def _verify_job_view():
    """생략한 AI 검증을 뒤에서 실행 — 진행률·중지(담당자 피드백 2026-11-06). 다른 화면으로 옮겨도 계속된다."""
    job = _ro.VERIFY_JOBS.get(p.slug)
    if not job:
        return
    if job["status"] == "running":
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.progress(job["done"] / (job["total"] or 1), text=f"AI 검증 중… {job['done']}/{job['total']}문항 "
                                                           "(다른 화면을 봐도 계속됩니다)")
        if c2.button("중지", icon=":material/stop_circle:", key="verify_stop", width="stretch",
                     help="지금 검증 중인 묶음까지 마치고 멈춥니다. 남은 문항은 다시 실행하면 이어서 검증합니다."):
            job["stop"] = True
        if job["stop"]:
            st.caption("중지 요청 — 지금 묶음을 마치면 멈춥니다.")
    elif job["status"] in ("done", "stopped"):
        (st.success if job["status"] == "done" else st.warning)(
            f"AI 검증 {'완료' if job['status'] == 'done' else '중지'}: 통과 {job['ok']} · 탈락(수동확인) {job['manual']}"
            + (" — 남은 문항은 다시 실행하면 이어서 검증합니다." if job["status"] == "stopped" else ""))
    else:
        st.error(f"AI 검증 중 오류: {job.get('error')}")


if _skipped or _ro.VERIFY_JOBS.get(p.slug):
    _s = _ls.load()
    with st.container(border=True):
        if _skipped:
            st.markdown(f"#### :orange[:material/bolt:] AI 검증을 생략한 문항 {len(_skipped)}개 (빠른 출제·수정 후 재검증 생략)")
            st.caption("정답 오류·이의 제기·함정 모순을 아직 확인하지 않은 문항입니다. 이 문항들은 정답·이의 제기 등급에 반영되지 않았습니다. "
                       "여러 문항을 묶어 검증하므로 문항마다 따로 하는 것보다 AI 호출이 적습니다. 실행 중에는 **중지**할 수 있습니다.")
        _job = _ro.VERIFY_JOBS.get(p.slug)
        if _skipped and not (_job and _job["status"] == "running"):
            if st.button(f"생략한 AI 검증 실행 — 예상 AI 호출 약 {_ro.estimate_verify(_s, len(_skipped))}회",
                         type="primary", icon=":material/verified:", key="verify_skipped"):
                _ro.start_verify_job(p, _s, [it["key"] for it in _skipped])
                st.rerun()
        _verify_job_view()

# ---------- 항목별 등급 ----------
_n = len(GRADE_RULES)
cols = st.columns(4) + st.columns(4)
for col, (k, (name, desc, cuts)) in zip(cols[:_n], GRADE_RULES.items()):
    g = r["grades"][k]
    with col.container(border=True, height="stretch"):
        st.markdown(f"<div style='text-align:center'><div style='font-size:0.85em;color:#555'>{name}</div>"
                    f"<div style='font-size:2.4em;font-weight:700;color:{GRADE_COLOR[g]};line-height:1.2'>{g}</div>"
                    f"<div style='font-size:0.75em;color:{GRADE_COLOR[g]}'>{GRADE_TEXT[g]}</div>"
                    f"<div style='font-size:0.8em;color:#666;margin-top:0.3rem'>문제 비율 {r['values'][k]:.0%}</div></div>",
                    unsafe_allow_html=True, help=f"{desc} · A ≤ {cuts[0]:.0%}, B ≤ {cuts[1]:.0%}, C ≤ {cuts[2]:.0%}, 그 이상 D")

# ---------- 분포 ----------
st.subheader(":material/bar_chart: 목표 대비 분포")
c1, c2 = st.columns([1, 1])
with c1:
    d = r["difficulty"]
    t_tot = sum(d["target"].values()) or 1
    f_tot = sum(d["final"].values()) or 1
    df = pd.DataFrame([{"난이도": lv, "목표(%)": round(d["target"][lv] * 100 / t_tot, 1),
                        "실제(%)": round(d["final"][lv] * 100 / f_tot, 1)} for lv in LEVELS])
    st.markdown("**난이도** — '실제'는 AI 검수가 '원고를 덮고 시험 보는 학습자' 기준으로 판정한 난이도입니다")
    ui.bar_chart(df, "난이도", ["목표(%)", "실제(%)"], stack=False, height=230)
    if d.get("shortfall"):
        st.warning("목표보다 모자란 난이도: " + " · ".join(f"**{lv} {n}문항**" for lv, n in d["shortfall"].items())
                   + " — 낮게 재분류된 문항은 목표를 채운 것이 아닙니다. 해당 난이도를 추가 출제하거나 담당자와 목표를 조정하세요.",
                   icon=":material/trending_down:")
with c2:
    n = cnt["accepted"]
    t = r["types"]
    ap = r["answer_pos"]
    pol = r["polarity"]
    rows = [
        ("유형", " · ".join(f"{ui.TYPE_KO[k]} {v}" for k, v in t.items())),
        ("선다형 정답 위치", " ".join(f"{'①②③④'[int(k) - 1]} {v}" for k, v in ap.items())),
        ("진위형 O / X", f"{r['ox']['O']} / {r['ox']['X']}"),
        ("선다형 옳지 않은 것 : 옳은 것", f"{pol['negative']} : {pol['positive']}  (목표 8 : 2)"),
        ("근거 일치", f"원문 그대로 {r['evidence_levels']['L2']} · 부분 일치 {r['evidence_levels']['L1']} · "
                     f"못 찾음 {r['evidence_levels']['미매칭']}"),
        ("문항 없는 페이지", ", ".join(r["coverage"]["zero"]) or "없음"),
        ("문항이 몰린 페이지", ", ".join(r["coverage"]["dense"]) or "없음"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["항목", "값"]), hide_index=True, width="stretch",
                 column_config={"항목": st.column_config.TextColumn(width="small"), "값": st.column_config.TextColumn(width="large")})

# ---------- 같은 원리 반복 (최종 풀 점검) ----------
from exam_gen.llm import settings as llm_settings  # noqa: E402
from exam_gen.validate import pool_review  # noqa: E402

pr = pool_review.last(p)
with st.container(border=True):
    st.markdown("#### :material/content_copy: 같은 원리 반복 점검")
    st.caption("문장이 달라도 같은 판단 원리를 묻는 문항 묶음입니다. 출제가 끝나면 자동으로 점검하며, 문항을 고치거나 지운 뒤에는 다시 점검할 수 있습니다. "
               "탈락시키지 않고 표시만 합니다 — 묶음마다 하나만 남길지, 묻는 조건을 달리할지 검수자가 정합니다.")
    c_a, c_b = st.columns([3, 1])
    if pr:
        c_a.markdown(f"마지막 점검 {pr['at'][:16].replace('T', ' ')} · {pr['items']}문항 중 **묶음 {len(pr['groups'])}개 · {pr['flagged']}문항**")
    else:
        c_a.markdown("아직 점검하지 않았습니다.")
    if c_b.button("다시 점검", icon=":material/refresh:", width="stretch", help="AI를 한 번 호출합니다(구독 사용량 소량)."):
        with st.spinner("문항 전체의 판단 원리를 비교하는 중…"):
            try:
                pr = pool_review.run(p, llm_settings.load())
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"점검에 실패했습니다: {e}")
    from exam_gen import review_ops  # noqa: E402
    for gi, g in enumerate((pr or {}).get("groups", [])):
        live = [k for k in g["keys"] if (p.item(k) or {}).get("status") not in (None, "deleted")]
        st.markdown(f"**{gi + 1}. {g['principle']}** — {', '.join(g['keys'])}  \n"
                    f"<span style='color:#666;font-size:0.9em'>{g['note']}</span>", unsafe_allow_html=True)
        if len(live) < 2:
            st.caption("정리됨: 남은 문항 " + (", ".join(live) or "없음"))
            continue
        # 같은 원리는 기본 1문항(Codex 권고). 묻는 조건이 달라 함께 써도 되면 그대로 둔다
        c1, c2 = st.columns([2, 2])
        keep = c1.selectbox("남길 문항", live, key=f"pool_keep_{gi}",
                            format_func=lambda k: f"{k} · {(p.item(k) or {}).get('stem', '')[:40]}")
        if c2.button(f"나머지 {len(live) - 1}문항 삭제", key=f"pool_del_{gi}", icon=":material/delete:", width="stretch",
                     help="삭제한 문항은 검수 화면 '삭제' 대기열에서 복원할 수 있습니다."):
            for k in live:
                if k != keep:
                    review_ops.set_status(p, k, "deleted", f"같은 원리 반복 — {keep}만 남김: {g['principle']}")
            st.rerun()

# ---------- 문제 문항 ----------
st.subheader(f":material/report: 문제 문항 {len(r['issues'])}개")
issue_rows = []
for key, cats in r["issues"].items():
    it = r["items"][key]
    for cat, msgs in cats.items():
        for m in msgs:
            issue_rows.append({"키": key, "항목": CAT_KO[cat], "유형": ui.TYPE_KO[it["type"]],
                               "난이도": actual_difficulty(it), "내용": m[:160], "_cat": cat})
if issue_rows:
    df_i = pd.DataFrame(issue_rows)
    pick = st.pills("항목", [CAT_KO[k] for k in CAT_KO if any(x["_cat"] == k for x in issue_rows)],
                    selection_mode="multi", key="q_cats")
    if pick:
        df_i = df_i[df_i["항목"].isin(pick)]
    sel_rows = st.dataframe(df_i.drop(columns=["_cat"]), hide_index=True, width="stretch", height=360,
                            on_select="rerun", selection_mode="single-row", key="q_issues")
    if sel_rows and sel_rows.selection.rows:
        key = df_i.iloc[sel_rows.selection.rows[0]]["키"]
        if st.button(f"{key} 검수 화면에서 열기", type="primary", icon=":material/open_in_new:"):
            st.session_state["review_key"] = key
            st.session_state["review_queue"] = "all"
            st.switch_page("app_pages/review.py")
else:
    st.success("자동 점검에서 지적된 문항이 없습니다.")

with st.expander("등급 기준과 계산 방법", icon=":material/info:"):
    st.markdown("\n".join(f"- **{name}**: {desc} — A ≤ {c[0]:.0%} · B ≤ {c[1]:.0%} · C ≤ {c[2]:.0%} · 그 이상 D"
                          for name, desc, c in GRADE_RULES.values()))
    st.caption("자동 검증을 통과한 문항만 집계합니다. 등급은 사람 검수를 대신하지 않으며, 무엇을 먼저 볼지 정하는 데 씁니다.")
    st.caption("'보기 다듬기(참고)'는 정답을 고르는 데 영향이 없는 검수 AI의 보기 수정 제안으로, 등급에 넣지 않습니다.")
    st.caption("'시의성 확인(참고)'은 기관명·법령·금액 기준·전화번호·날짜처럼 원고 발행 뒤 바뀔 수 있는 정보가 든 문항입니다. "
               "외부 확인은 하지 않으며, 정답이 그 정보에 달린 문항은 담당자·원고 집필자와 최신 여부를 확인합니다.")
