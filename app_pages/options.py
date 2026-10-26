"""2. 출제 옵션 — 난이도 %, 유형 비율, 페이지 할당(자동/강제), 최초·추가 출제."""
import math
from collections import Counter

import pandas as pd
import streamlit as st

from exam_gen import ui
from exam_gen.config import criteria
from exam_gen.cost import estimate
from exam_gen.generate.generator import start_batch
from exam_gen.llm import settings as llm_settings
from exam_gen.plan.coverage import allocate, largest_remainder
from exam_gen.plan.slots import build_slots, check_options, summarize

p = ui.require_project(ui.current_project())
ui.header("출제 옵션", "tune", "난이도·유형 비율과 페이지 할당을 정하고 출제를 시작한다.")
C = criteria()
LV = C["difficulty_levels"]
meta = p.meta
opts = {**C["defaults"], **meta.get("options", {})}
pages = p.pages
elig = [x for x in pages if not x["excluded"]]
if not elig:
    st.warning("출제 대상 페이지가 없다. 원고 등록 화면을 확인하세요.")
    st.stop()

items = [it for it in p.items() if it["status"] != "deleted"]
existing_pages = Counter(it["page"] for it in items)
mode = st.radio("출제 종류", ["최초 출제", "추가 출제", "페이지 재출제(강제 할당)"], horizontal=True,
                index=1 if items else 0,
                help="추가 출제는 기존 문항과 중복되지 않게 출제하며, 기존 문항이 적은 페이지에 더 배분한다.")

# ---------- 공통 비율 ----------
with st.container(border=True):
    st.markdown(":material/signal_cellular_alt: **난이도 비율 (%)** — 합계 100")
    cols = st.columns(4)
    dpct = {lv: cols[i].number_input(lv, 0, 100, int(opts["difficulty_pct"].get(lv, 0)), key=f"d_{lv}") for i, lv in enumerate(LV)}
    dsum = sum(dpct.values())
    (st.success if dsum == 100 else st.error)(f"난이도 합계 {dsum}%")
    with st.expander("난이도 기준(루브릭)", icon=":material/menu_book:"):
        for lv in LV:
            st.markdown(f"- **{lv}**: {C['difficulty_rubric'][lv]}")

    st.markdown(":material/category: **유형 비율 (%)** — 합계 100")
    cols = st.columns(3)
    tpct = {t: cols[i].number_input(ui.TYPE_KO[t], 0, 100, int(opts["type_pct"].get(t, 0)), key=f"t_{t}")
            for i, t in enumerate(C["type_order"])}
    tsum = sum(tpct.values())
    (st.success if tsum == 100 else st.error)(f"유형 합계 {tsum}%")

    with st.expander("세부 옵션", icon=":material/settings:"):
        c1, c2, c3 = st.columns(3)
        pos = c1.number_input("선다형 '옳은 것 고르기' %", 0, 100, int(opts["mcq_positive_pct"]),
                              help="지침·담당자 협의: 옳지 않은 것 80% / 옳은 것 20%")
        combo = c2.number_input("선다형 중 ㄱㄴㄷㄹ 조합형 %", 0, 100, int(opts["combo_pct"]))
        retries = c3.number_input("탈락 시 재출제 횟수", 0, 5, int(opts["max_retries"]))
        c1, c2, c3 = st.columns(3)
        score = c1.number_input("배점", 1, 100, int(opts["score"]))
        scoring = c2.text_input("채점기준", value=opts["scoring"])
        numbering = c3.selectbox("문항번호", ["continuous", "per_type"], index=["continuous", "per_type"].index(opts["numbering"]),
                                 format_func=lambda x: {"continuous": "전체 연번(양식)", "per_type": "유형별 1번부터(지침)"}[x])
        prefix = st.checkbox("문항 앞에 [과목/주제명] 붙이기 (유의사항 권고)", value=bool(opts["subject_prefix"]))
        fast = st.checkbox("빠른 출제 — AI 독립 풀이·검수 생략", value=bool(opts.get("fast_mode", False)))
        st.caption("급할 때만 사용합니다. 형식·원고 근거 대조·규칙·중복 검사는 그대로 하지만, **정답 오류·이의 제기·함정 모순은 확인하지 "
                   "않습니다.** 시간·AI 사용량이 약 1/3로 줄고, 문항은 'AI 검증 생략'으로 표시됩니다. 나중에 품질 리포트·검수 화면에서 "
                   "'생략한 AI 검증'을 실행하면 일반 문항과 같아집니다.")
        topup = st.checkbox("난이도 보충 출제 — 실제 난이도(검수 판정)가 목표보다 모자라면 배치 끝에 한 번 더 출제",
                            value=bool(opts.get("difficulty_topup", False)),
                            help="실측(30문항): 보충 14문항 중 8문항이 다시 '하'로 판정되어 효과는 낮고 비용은 약 47% 늘었다. 기본은 끔.")

new_opts = {**opts, "difficulty_pct": dpct, "type_pct": tpct, "mcq_positive_pct": pos, "combo_pct": combo,
            "max_retries": retries, "score": score, "scoring": scoring, "numbering": numbering, "subject_prefix": prefix, "difficulty_topup": topup,
            "fast_mode": fast}

# ---------- 수량과 페이지 할당 ----------
st.subheader(":material/grid_view: 수량과 페이지 할당")
diff_counts = None
if mode == "최초 출제":
    c1, c2 = st.columns(2)
    total = c1.number_input("필요 문항 수", 1, 3000, int(opts["total"]))
    extra = c2.number_input("여유 Pool (%)", 0, 50, int(opts["pool_extra_pct"]),
                            help="유의사항: 심사·운영 중 삭제에 대비해 조금 여유 있게 출제")
    n = math.ceil(total * (1 + extra / 100))
    new_opts.update(total=total, pool_extra_pct=extra)
    alloc = allocate(pages, n)
elif mode == "추가 출제":
    how = st.radio("수량 입력 방식", ["난이도별 수량", "총 수량 + 난이도 %"], horizontal=True)
    if how == "난이도별 수량":
        cols = st.columns(4)
        diff_counts = {lv: cols[i].number_input(f"{lv} 문항 수", 0, 1000, 0, key=f"dc_{lv}") for i, lv in enumerate(LV)}
        n = sum(diff_counts.values())
    else:
        n = st.number_input("추가 문항 수", 0, 3000, 50)
        diff_counts = largest_remainder(n, dpct)
    st.caption(f"난이도 구성: {diff_counts} · 기존 {len(items)}문항과 중복되지 않게 출제(회피 목록 + G6)")
    alloc = allocate(pages, n, existing=dict(existing_pages)) if n else {}
else:
    st.caption("편중 리포트에서 0문항·과밀 페이지가 보이면, 아래 표에 페이지별 수를 직접 입력해 해당 페이지만 다시 출제한다.")
    alloc = {}
    n = 0

forced = st.toggle("페이지별 수 직접 지정(강제 할당)", value=mode == "페이지 재출제(강제 할당)",
                   disabled=mode == "페이지 재출제(강제 할당)")
df = pd.DataFrame([{"g": x["g"], "책 쪽수": x["label"], "차시": x["chasi"], "글자 수": x["chars"],
                    "기존 문항": existing_pages.get(x["g"], 0), "이번 출제": alloc.get(x["g"], 0)} for x in elig])
if forced:
    df = st.data_editor(df, hide_index=True, width="stretch", height=360,
                        disabled=["g", "책 쪽수", "차시", "글자 수", "기존 문항"], column_config={"g": None})
    alloc = {int(r["g"]): int(r["이번 출제"]) for _, r in df.iterrows() if int(r["이번 출제"]) > 0}
    n = sum(alloc.values())
    if mode != "추가 출제":
        diff_counts = largest_remainder(n, dpct)
    elif diff_counts is not None and sum(diff_counts.values()) != n:
        st.error(f"페이지 할당 합계({n})와 난이도별 수량 합계({sum(diff_counts.values())})가 다르다 — 둘 중 하나를 맞추세요.")
else:
    ui.bar_chart(df, "책 쪽수", ["기존 문항", "이번 출제"], stack=True, height=260)

# ---------- 설정 검사 (Codex C12·C13) ----------
problems = check_options(new_opts, n if diff_counts is not None else None, diff_counts)

if n and not problems:
    preview = build_slots(pages, alloc, new_opts, difficulty_counts=diff_counts)
    sm = summarize(preview)
    def _card(col, title, cells):
        """구성 카드: 칸마다 이름·값·보조값을 보여 준다(긴 내용이 잘리지 않게). cells = [(이름, 값, 보조값)]"""
        with col.container(border=True, height="stretch"):
            st.caption(title)
            for sc, (name, val, sub) in zip(st.columns(len(cells)), cells):
                sc.markdown(f"<div style='text-align:center;line-height:1.3'><span style='font-size:0.8em;color:#666'>{name}</span>"
                            f"<br><span style='font-size:1.5em;font-weight:600'>{val}</span>"
                            f"<br><span style='font-size:0.75em;color:#888'>{sub}</span></div>",
                            unsafe_allow_html=True)

    def _mix(col, title, labels, counts):
        tot = sum(counts.values()) or 1
        _card(col, title, [(name, counts.get(k, 0), f"{counts.get(k, 0) / tot:.0%}") for k, name in labels])

    c = st.columns(3)
    pc = sm["pages"].values()
    _card(c[0], "출제 규모", [("출제 슬롯", sm["total"], f"여유 {new_opts.get('pool_extra_pct', 0)}% 포함"
                                                       if mode == "최초 출제" else "이번 배치"),
                            ("출제 페이지", len(sm["pages"]), f"전체 {len(elig)}쪽"),
                            ("페이지당", f"{min(pc)}~{max(pc)}", "문항")])
    _mix(c[1], "난이도 구성", [(k, k) for k in LV], sm["difficulty"])
    _mix(c[2], "유형 구성", [(k, ui.TYPE_KO[k]) for k in C["type_order"]], sm["type"])
    s = llm_settings.load()
    pos_txt = " ".join(f"{'①②③④'[int(k) - 1]} {v}" for k, v in sorted(sm["answer"].items()) if k.isdigit())
    pol = sm.get("polarity", {})
    st.caption(f"**선다형 옳지 않은 것 : 옳은 것 = {pol.get('negative', 0)} : {pol.get('positive', 0)} "
               f"(목표 8 : 2, 조합형은 옳은 것에 포함)** · "
               f"선다형 정답 위치 {pos_txt} · 진위형 O/X {sm['answer'].get('O', 0)}/{sm['answer'].get('X', 0)} · "
               f"조합형 {sm['combo']} · 유형별 난이도: 단답형은 하·중, 진위형은 상까지(상은 정답 X)")
    est = estimate(s, sm["total"])
    if new_opts.get("fast_mode"):  # AI 검증(G4·G5) 생략 — 실측 호출의 약 1/3(추정)
        est = {**est, "usd": round(est["usd"] / 3, 1), "minutes": max(1, round(est["minutes"] / 3)),
               "basis": est["basis"] + " · 빠른 출제(AI 검증 생략) 추정 1/3"}
    cost_txt = (f"구독 사용량에서 차감 (API로 환산하면 약 ${est['usd']})" if est["subscription"]
                else f"약 ${est['usd']}")
    st.info(f"예상 소요 약 {est['minutes']}분 · 비용 {cost_txt} · 공급자 {' / '.join(sorted(set(s.provider_for(r) for r in ('generate', 'solve', 'review'))))} "
            f"— 근거: {est['basis']}. 재출제가 많으면 늘어난다.", icon=":material/payments:")

# ---------- 시작 ----------
if not p.page_map_ready():
    problems.append("원고 등록 화면에서 책 쪽수 매핑을 확정해야 한다(자동 중단 조건 1).")
if not n:
    problems.append("출제할 문항 수가 0이다.")
for x in problems:
    st.error(x)
if llm_settings.load().provider == "mock":
    st.warning("현재 LLM 공급자가 '데모(모의)'다. 화면 흐름 확인용 문항이 만들어진다.")
c1, c2 = st.columns([1, 3])
if c1.button("옵션 저장"):
    p.update_meta(options=new_opts)
    st.toast("저장했다", icon=":material/check:")
if c2.button("출제 시작", type="primary", disabled=bool(problems)):
    p.update_meta(options=new_opts)
    start = sum(len(b["slots"]) for b in p.batches()) + 1
    slots = build_slots(pages, alloc, new_opts, difficulty_counts=diff_counts, start_index=start,
                        seed=start)
    kind = {"최초 출제": "initial", "추가 출제": "additional", "페이지 재출제(강제 할당)": "repair"}[mode]
    b = start_batch(p, llm_settings.load(), slots, new_opts, kind=kind)
    st.session_state["batch"] = b
    st.switch_page("app_pages/progress.py")
