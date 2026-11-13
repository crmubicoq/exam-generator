"""2. 출제 옵션 — 난이도 %, 유형 비율, 페이지 할당(자동/강제), 최초·추가 출제."""
import math
from collections import Counter

import pandas as pd
import streamlit as st

from exam_gen import difficulty_policy, ui
from exam_gen.config import criteria
from exam_gen.cost import estimate, hard_extra_minutes
from exam_gen.generate.generator import start_batch
from exam_gen.llm import settings as llm_settings
from exam_gen.plan import calibrate
from exam_gen.plan.coverage import allocate, largest_remainder, repair_alloc
from exam_gen.plan.slots import build_slots, check_options, summarize

p = ui.require_project(ui.current_project())
ui.header("출제 옵션", "tune", "몇 문항을 어떤 난이도·유형으로 낼지 정하고 출제를 시작합니다. 기본값은 담당자 협의 기준입니다.")
C = criteria()
LV = C["difficulty_levels"]
meta = p.meta
opts = {**C["defaults"], **meta.get("options", {})}
pages = p.pages
elig = [x for x in pages if not x["excluded"]]
if not elig:
    st.warning("출제할 페이지가 없습니다. 원고 등록 화면에서 원고를 올리고 쪽수를 확정하세요.")
    st.stop()

items = [it for it in p.items() if it["status"] != "deleted"]
existing_pages = Counter(it["page"] for it in items)
MODES = ["최초 출제", "추가 출제", "페이지 재출제"]
mode = st.radio("출제 종류", MODES, horizontal=True, index=1 if items else 0,
                captions=["처음 출제할 때", "이미 있는 문항에 더 낼 때(겹치지 않게)", "문항이 없거나 적은 페이지만 다시 낼 때"])

# ---------- 문항 수 (담당자 피드백 2026-11-05: 수량을 먼저 정하고 비율을 정한다) ----------
st.subheader(":material/pin: 문항 수")
diff_counts = None
how = None
_qty = {}
if mode == "최초 출제":
    c1, c2 = st.columns(2)
    total = c1.number_input("필요 문항 수", 1, 3000, int(opts["total"]))
    extra = c2.number_input("여유분 (%)", 0, 50, int(opts["pool_extra_pct"]),
                            help="검수에서 지울 문항에 대비해 조금 더 출제합니다. 출력할 때 '최종 문항 자동 선정'으로 필요한 수만 고릅니다.")
    n = math.ceil(total * (1 + extra / 100))
    _qty = dict(total=total, pool_extra_pct=extra)
    st.caption(f"실제 출제 **{n}문항** = 필요 {total}문항 + 여유분 {extra}% (검수에서 지울 문항 대비)")
    alloc = allocate(pages, n)
elif mode == "추가 출제":
    # 입력 방식을 '총 수량 + 난이도 %' 하나로 통일(담당부서 피드백 2026-11-13 #11 — 쓰지 않는 칸이 보여 헷갈렸다)
    n = st.number_input("추가 문항 수", 0, 3000, 50)
    st.caption("난이도 구성은 아래 **난이도 비율(%)**로 나눕니다. 비율 옆에 난이도별 문항 수가 표시됩니다.")
    alloc = allocate(pages, n, existing=dict(existing_pages)) if n else {}
else:
    st.caption("문항이 없거나 적은 페이지만 다시 출제합니다. 아래 **'페이지별 문항 수 직접 지정'**을 끄면 문항 수만 넣어 "
               "자동으로 배정하고, 켜면 '페이지 배분' 표의 '이번 출제' 칸에 페이지마다 직접 넣습니다.")
    c1, c2 = st.columns(2)
    n = c1.number_input("다시 출제할 문항 수", 0, 1000, 10, key="repair_n")
    max_ex = c2.number_input("대상 페이지: 기존 문항이 몇 개 이하인 페이지", 0, 20, 0, key="repair_max",
                             help="0이면 문항이 하나도 없는 페이지에만 배정합니다.")
    _targets = [x for x in elig if existing_pages.get(x["g"], 0) <= max_ex]
    alloc = repair_alloc(pages, n, dict(existing_pages), int(max_ex))
    st.caption(f"대상 페이지 **{len(_targets)}쪽** (기존 문항 {max_ex}개 이하) — 문항이 적은 페이지부터, 같으면 글자 수가 많은 "
               "페이지부터 한 문항씩 돌아가며 배정합니다.")
    if not _targets:
        st.warning("조건에 맞는 페이지가 없습니다. '기존 문항이 몇 개 이하'를 늘리세요.", icon=":material/warning:")


# ---------- 공통 비율 ----------
st.subheader(":material/percent: 난이도·유형 비율")
with st.container(border=True):
    st.markdown(":material/signal_cellular_alt: **난이도 비율 (%)** — 합계 100")
    cols = st.columns(4)
    dpct = {lv: cols[i].number_input(lv, 0, 100, int(opts["difficulty_pct"].get(lv, 0)), key=f"d_{lv}") for i, lv in enumerate(LV)}
    d_cnt = {lv: cols[i].empty() for i, lv in enumerate(LV)}  # 문항 수는 아래에서 구성이 정해진 뒤 채운다
    dsum = sum(dpct.values())
    (st.success if dsum == 100 else st.error)(f"난이도 합계 {dsum}%")
    with st.expander(f"난이도 기준 — {difficulty_policy.label(p)}", icon=":material/menu_book:"):
        _rub = difficulty_policy.rubric(p)
        for lv in LV:
            st.markdown(f"- **{lv}**: {_rub[lv]}")
        st.caption("기준은 관리 → 난이도 기준에서 이 프로젝트 전용으로 따로 정하거나 공통 기준을 따르게 할 수 있습니다.")

    st.markdown(":material/category: **유형 비율 (%)** — 합계 100")
    cols = st.columns(3)
    tpct = {t: cols[i].number_input(ui.TYPE_KO[t], 0, 100, int(opts["type_pct"].get(t, 0)), key=f"t_{t}")
            for i, t in enumerate(C["type_order"])}
    t_cnt = {t: cols[i].empty() for i, t in enumerate(C["type_order"])}
    tsum = sum(tpct.values())
    (st.success if tsum == 100 else st.error)(f"유형 합계 {tsum}%")

    with st.expander("세부 옵션", icon=":material/settings:"):
        c1, c2, c3 = st.columns(3)
        pos = c1.number_input("선다형 '옳은 것 고르기' %", 0, 100, int(opts["mcq_positive_pct"]),
                              help="담당자 협의: 옳지 않은 것 80% / 옳은 것 20%")
        pos_cnt = c1.empty()
        combo = c2.number_input("선다형 중 ㄱㄴㄷㄹ 조합형 %", 0, 100, int(opts["combo_pct"]))
        combo_cnt = c2.empty()
        retries = c3.number_input("탈락 시 재출제 횟수", 0, 5, int(opts["max_retries"]))
        c3.caption("검증에서 탈락하면 같은 조건으로 다시 만드는 최대 횟수입니다.")
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
        calib = st.checkbox("난이도 보정 출제 (권장)", value=bool(opts.get("difficulty_calibrate", True)))
        st.caption("AI 검수는 출제 의도보다 난이도를 낮게 판정하는 경향이 있습니다(실측: '상'으로 낸 문항의 약 70%가 '중', "
                   "'중'으로 낸 문항의 약 75%가 '하'). 켜 두면 어려운 문항을 더 배정해, **검수 판정 기준으로** 위 난이도 비율에 "
                   "가깝게 나오도록 합니다. 다만 보정은 한 단계 정도만 합니다(최상은 목표 수만큼, 상·최상 합은 목표의 2배까지). "
                   "출력할 때 '최종 문항 자동 선정'이 판정 난이도로 비율을 맞춥니다. 끄면 위 비율 그대로 출제 지시합니다.")
        topup = st.checkbox("난이도 보충 출제 — 실제 난이도(검수 판정)가 목표보다 모자라면 배치 끝에 한 번 더 출제",
                            value=bool(opts.get("difficulty_topup", False)),
                            help="실측(30문항): 보충 14문항 중 8문항이 다시 '하'로 판정되어 효과는 낮고 사용량은 약 47% 늘었습니다. 기본은 끕니다.")

new_opts = {**opts, "difficulty_pct": dpct, "type_pct": tpct, "mcq_positive_pct": pos, "combo_pct": combo,
            "max_retries": retries, "score": score, "scoring": scoring, "numbering": numbering, "subject_prefix": prefix, "difficulty_topup": topup, "difficulty_calibrate": calib,
            "fast_mode": fast, **_qty}
if mode == "추가 출제":
    diff_counts = largest_remainder(n, dpct)
    st.caption(f"추가 출제 난이도 구성: {' · '.join(f'{k} {v}' for k, v in diff_counts.items())} · "
               f"기존 {len(items)}문항과 겹치지 않게 출제합니다.")

# ---------- 페이지 배분 ----------
st.subheader(":material/grid_view: 페이지 배분")
st.caption("위 문항 수를 원고 분량(글자 수)에 맞춰 페이지마다 나눈 결과입니다. 차시가 나뉘어 있으면 차시별로 먼저 나눕니다.")

forced = st.toggle("페이지별 문항 수 직접 지정", value=False, key=f"forced_{mode}",
                   help="끄면 자동으로 나눕니다(최초·추가 출제는 분량(글자 수)에 맞춰, 페이지 재출제는 문항이 적은 페이지부터). "
                        "보통은 끈 채로 둡니다.")
df = pd.DataFrame([{"g": x["g"], "책 쪽수": x["label"], "차시": x["chasi"], "글자 수": x["chars"],
                    "기존 문항": existing_pages.get(x["g"], 0), "이번 출제": alloc.get(x["g"], 0)} for x in elig])
if forced:
    df = st.data_editor(df, hide_index=True, width="stretch", height=360,
                        disabled=["g", "책 쪽수", "차시", "글자 수", "기존 문항"], column_config={"g": None})
    alloc = {int(r["g"]): int(r["이번 출제"]) for _, r in df.iterrows() if int(r["이번 출제"]) > 0}
    n = sum(alloc.values())
    diff_counts = largest_remainder(n, dpct)
else:
    ui.bar_chart(df, "책 쪽수", ["기존 문항", "이번 출제"], stack=True, height=260)

# ---------- 난이도 보정: 목표(판정 기준) → 출제 지시 난이도 ----------
@st.cache_data(ttl=300, show_spinner=False)
def _calib_table():
    return calibrate.table()  # 모든 프로젝트 문항을 읽으므로 5분 동안 재사용



calib_info = None
if calib and n:
    target_pct = dpct
    tab, n_hist = _calib_table()
    diff_counts = calibrate.plan(n, target_pct, largest_remainder(n, tpct), tab)
    calib_info = {"goal": {lv: n * target_pct.get(lv, 0) / 100 for lv in LV},
                  "expected": calibrate.expected(diff_counts, tab), "history": sum(n_hist.values())}

# ---------- 설정 검사 (Codex C12·C13) ----------
problems = check_options(new_opts, n if diff_counts is not None else None, diff_counts)
est, sm = {}, {}

if n and not problems:
    preview = build_slots(pages, alloc, new_opts, difficulty_counts=diff_counts)
    sm = summarize(preview)
    def _card(col, title, cells):
        """구성 카드: 칸마다 이름·값·보조값을 보여 준다(긴 내용이 잘리지 않게). cells = [(이름, 값, 보조값)]"""
        with col.container(border=True, height="stretch"):
            st.caption(title)
            for sc, (name, val, sub) in zip(st.columns(len(cells)), cells):
                sc.markdown(f"<div style='text-align:center;line-height:1.3'><span style='font-size:0.8em;color:#666'>{name}</span>"
                            f"<br><span style='font-size:1.5em;font-weight:600;white-space:nowrap'>{val}</span>"
                            f"<br><span style='font-size:0.75em;color:#888'>{sub}</span></div>",
                            unsafe_allow_html=True)

    def _mix(col, title, labels, counts):
        tot = sum(counts.values()) or 1
        _card(col, title, [(name, counts.get(k, 0), f"{counts.get(k, 0) / tot:.0%}") for k, name in labels])

    c = st.columns(3)
    pc = sm["pages"].values()
    _card(c[0], "출제 규모", [("출제 문항", sm["total"], f"여유 {new_opts.get('pool_extra_pct', 0)}% 포함"
                                                       if mode == "최초 출제" else "이번 배치"),
                            ("페이지", len(sm["pages"]), f"전체 {len(elig)}쪽"),
                            ("페이지당", f"{min(pc)}~{max(pc)}", "문항")])
    _mix(c[1], "출제 지시 난이도" if calib_info else "난이도 구성", [(k, k) for k in LV], sm["difficulty"])
    _mix(c[2], "유형 구성", [(k, ui.TYPE_KO[k]) for k in C["type_order"]], sm["type"])
    pos_txt = " ".join(f"{'①②③④'[int(k) - 1]} {v}" for k, v in sorted(sm["answer"].items()) if k.isdigit())
    pol = sm.get("polarity", {})
    st.caption(f"**선다형 옳지 않은 것 : 옳은 것 = {pol.get('negative', 0)} : {pol.get('positive', 0)} "
               f"(목표 8 : 2, 조합형은 옳은 것에 포함)** · "
               f"선다형 정답 위치 {pos_txt} · 진위형 O/X {sm['answer'].get('O', 0)}/{sm['answer'].get('X', 0)} · "
               f"조합형 {sm['combo']} · 유형별 난이도: 단답형은 하·중, 진위형은 상까지(상은 정답 X)")
    if calib_info:
        # 목표·출제 지시·예상 판정을 한 표에 나란히(담당부서 피드백 2026-11-13 #2 — 두 숫자가 달라 불일치처럼 보였다)
        g, e = calib_info["goal"], calib_info["expected"]
        with st.container(border=True):
            st.markdown(":material/tune: **난이도 보정 — 목표와 출제 지시가 다른 이유**")
            st.caption("AI 검수는 출제 의도보다 난이도를 한 단계 낮게 판정하는 경향이 있습니다. 그래서 **최종 판정이 목표에 가깝게 "
                       "나오도록 AI에게는 조금 더 어렵게 지시**합니다. 출력·자동 선정·품질 리포트는 '최종 판정' 기준입니다. "
                       "목표대로 그대로 지시하려면 '세부 옵션'에서 **난이도 보정 출제**를 끄세요.")
            st.dataframe(pd.DataFrame([{"난이도": lv, "① 목표(최종 판정 기준)": round(g[lv]),
                                        "② 출제 지시(AI에게 요청)": sm["difficulty"].get(lv, 0),
                                        "③ 예상 판정": round(e[lv])} for lv in LV]),
                         hide_index=True, width="stretch")
            st.caption("①은 위 난이도 비율로 계산한 수, ②는 실제로 출제를 지시하는 수(위 '출제 지시 난이도' 카드), ③은 ②로 출제했을 때 "
                       + ("개발 시험 실측" if calib_info["history"] < 20 else
                          f"이 PC 출제 기록 {calib_info['history']}문항과 개발 시험 실측")
                       + "으로 예상한 판정 결과입니다.")
            if g["최상"] >= 1 and e["최상"] < g["최상"] * 0.8:
                st.caption(f":orange[최상은 예상 {e['최상']:.0f}문항으로 목표에 못 미칩니다] — 최상 기준은 담당부서와 협의 중입니다.")
            if g["중"] >= 1 and e["중"] < g["중"] * 0.8:
                st.caption(f":orange[중은 예상 {e['중']:.0f}문항으로 목표보다 적고 하가 많습니다] — 검수 화면 '담당자 판정 난이도'로 "
                           "바로잡을 수 있고, 판정 기준은 담당부서가 정합니다.")
    # ---- 비율 옆에 문항 수 표시(담당자 요청 2026-11-03) ----
    goal_d = {lv: round(calib_info["goal"][lv]) for lv in LV} if calib_info else sm["difficulty"]
    for lv in LV:
        d_cnt[lv].caption(f"→ 목표 {goal_d.get(lv, 0)}문항" + (f" · 출제 지시 {sm['difficulty'].get(lv, 0)}" if calib_info else ""))
    for t in C["type_order"]:
        t_cnt[t].caption(f"→ {sm['type'].get(t, 0)}문항")
    _mcq = sm["type"].get("mcq", 0)
    pos_cnt.caption(f"→ 선다형 {_mcq}문항 중 옳은 것 {pol.get('positive', 0)} · 옳지 않은 것 {pol.get('negative', 0)}"
                    + " (조합형 포함)")
    combo_cnt.caption(f"→ 선다형 {_mcq}문항 중 조합형 {sm['combo']}문항")
    est = estimate(llm_settings.load(), sm["total"])
    if calib_info:  # 보정으로 늘어난 상·최상 슬롯은 더 오래 걸린다
        base = largest_remainder(n, dpct)
        more = (sm["difficulty"].get("최상", 0) + sm["difficulty"].get("상", 0)) - (base.get("최상", 0) + base.get("상", 0))
        est = {**est, "minutes": est["minutes"] + hard_extra_minutes(more, llm_settings.load())}
    if new_opts.get("fast_mode"):  # AI 검증(G4·G5) 생략 — 실측 호출의 약 1/3(추정)
        est = {**est, "usd": round(est["usd"] / 3, 1), "minutes": max(1, round(est["minutes"] / 3)),
               "basis": est["basis"] + " · 빠른 출제(AI 검증 생략) 추정 1/3"}


# ---------- 출제 전 점검 ----------
if not p.page_map_ready():
    problems.append("원고 등록 화면에서 쪽수를 확정해야 출제할 수 있습니다.")
if not n:
    problems.append("출제할 문항 수가 0입니다.")
s = llm_settings.load()
mock = s.provider == "mock"
st.subheader(":material/checklist: 출제 전 점검")
with st.container(border=True):
    chasi_n = len({x["chasi"] for x in elig})
    rows = [(p.page_map_ready(), "원고 쪽수 확정", "확정됨" if p.page_map_ready() else "원고 등록 화면에서 확정하세요"),
            (chasi_n > 1 or len(elig) <= 15 or None, "차시(단원) 나누기",
             f"{chasi_n}개 차시" + ("" if chasi_n > 1 or len(elig) <= 15 else
                                  " — 원고가 여러 단원이면 원고 등록 ③에서 단원별로 나누면 단원마다 고르게 출제됩니다(한 단원이면 그대로 두어도 됩니다)")),
            (bool(meta.get("objectives")) or None, "학습목표",
             "입력됨" if meta.get("objectives") else "없음(권장) — 원고 등록 ④에서 넣으면 중요한 내용 위주로 출제합니다")]
    _pol = difficulty_policy.load(p)
    rows.append((_pol["status"] == "확정" or None, "난이도 기준",
                 difficulty_policy.label(p) + (f"({_pol.get('confirmed_by')})" if _pol["status"] == "확정" and _pol.get("confirmed_by") else "")
                 + ("" if _pol["status"] == "확정" else " — 관리 → 난이도 기준에서 확인·확정할 수 있습니다")))
    P = llm_settings.PROVIDERS

    def _ai(role):
        prov = s.provider_for(role)
        m = s.model_for(role) or "계정 기본 모델"
        return f"{P.get(prov, prov).split(' (')[0]} · {m}"
    ai_txt = _ai("generate") if len({_ai(r) for r in ("generate", "solve", "review")}) == 1 else \
        f"생성 {_ai('generate')} / 독립 풀이 {_ai('solve')} / 검수 {_ai('review')}"
    rows.append((not mock or None, "사용할 AI", ai_txt + (" — 데모(모의): 화면 흐름 확인용 문항만 만들어집니다" if mock else "")))
    if n and not problems:
        mins = est["minutes"]
        t = f"약 {mins // 60}시간 {mins % 60}분" if mins >= 60 else f"약 {mins}분"
        use = "구독 사용량에서 차감(별도 비용 없음)" if est["subscription"] else f"약 ${est['usd']}"
        rows.append((True, "예상 시간·사용량", f"{sm['total']}문항 · {t} · {use} — "
                                            + ("이 PC에서 지금까지 출제한 실측 기준" if "실측" in est["basis"]
                                               else "개발 시험 출제 실측 기준(Opus 5.5, 32문항 약 12분)")
                                            + (", 난이도 보정으로 늘어난 상·최상 문항은 문항당 약 1분" if calib_info else "")
                                            + "이며, 다시 출제하는 문항이 많으면 늘어납니다. 끝나면 윈도우 알림이 뜹니다."))
    for ok, name, detail in rows:
        mark = (":green[:material/check_circle:]" if ok else ":orange[:material/error:]" if ok is None
                else ":red[:material/cancel:]")
        st.markdown(f"{mark} **{name}** — {detail}")
for x in problems:
    st.error(x)
confirm = True
if not mock and n and not problems:
    st.caption("출제하는 동안 프로그램이 윈도우 자동 절전을 막습니다. 다른 화면·프로그램을 써도 됩니다. "
               "다만 **검은 창을 닫거나, 노트북 덮개를 닫거나, 직접 절전·종료하면 출제가 멈춥니다**(멈추면 '출제 진행'에서 '이어서 출제').")
    confirm = st.checkbox("위 예상 시간·사용량을 확인했습니다. 출제가 끝날 때까지 검은 창과 노트북 덮개를 닫지 않습니다.")
c1, c2 = st.columns([1, 3])
if c1.button("옵션만 저장", icon=":material/save:"):
    p.update_meta(options=new_opts)
    st.toast("저장했습니다", icon=":material/check:")
if c2.button("출제 시작", type="primary", disabled=bool(problems) or not confirm, icon=":material/play_arrow:"):
    p.update_meta(options=new_opts)
    start = sum(len(b["slots"]) for b in p.batches()) + 1
    slots = build_slots(pages, alloc, new_opts, difficulty_counts=diff_counts, start_index=start,
                        seed=start)
    kind = {"최초 출제": "initial", "추가 출제": "additional", "페이지 재출제": "repair"}[mode]
    b = start_batch(p, llm_settings.load(), slots, new_opts, kind=kind)
    st.session_state["batch"] = b
    st.switch_page("app_pages/progress.py")
