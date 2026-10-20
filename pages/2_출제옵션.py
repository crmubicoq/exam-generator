"""2. 출제 옵션 — 난이도 %, 유형 비율, 페이지 할당(자동/강제), 최초·추가 출제."""
import math
from collections import Counter

import pandas as pd
import streamlit as st

from exam_gen import ui
from exam_gen.config import criteria
from exam_gen.generate.generator import start_batch
from exam_gen.llm import settings as llm_settings
from exam_gen.plan.coverage import allocate, largest_remainder
from exam_gen.plan.slots import build_slots, summarize

ui.setup("출제 옵션")
p = ui.require_project(ui.sidebar())
st.title("2. 출제 옵션")
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
    st.markdown("**난이도 비율 (%)** — 합계 100")
    cols = st.columns(4)
    dpct = {lv: cols[i].number_input(lv, 0, 100, int(opts["difficulty_pct"].get(lv, 0)), key=f"d_{lv}") for i, lv in enumerate(LV)}
    dsum = sum(dpct.values())
    (st.success if dsum == 100 else st.error)(f"난이도 합계 {dsum}%")
    with st.expander("난이도 기준(루브릭)"):
        for lv in LV:
            st.markdown(f"- **{lv}**: {C['difficulty_rubric'][lv]}")

    st.markdown("**유형 비율 (%)** — 합계 100")
    cols = st.columns(3)
    tpct = {t: cols[i].number_input(ui.TYPE_KO[t], 0, 100, int(opts["type_pct"].get(t, 0)), key=f"t_{t}")
            for i, t in enumerate(C["type_order"])}
    tsum = sum(tpct.values())
    (st.success if tsum == 100 else st.error)(f"유형 합계 {tsum}%")

    with st.expander("세부 옵션"):
        c1, c2, c3 = st.columns(3)
        pos = c1.number_input("선다형 '옳은 것 고르기' %", 0, 100, int(opts["mcq_positive_pct"]),
                              help="지침: 옳은 것 20% / 옳지 않은 것 80%")
        combo = c2.number_input("선다형 중 ㄱㄴㄷㄹ 조합형 %", 0, 100, int(opts["combo_pct"]))
        retries = c3.number_input("탈락 시 재출제 횟수", 0, 5, int(opts["max_retries"]))
        c1, c2, c3 = st.columns(3)
        score = c1.number_input("배점", 1, 100, int(opts["score"]))
        scoring = c2.text_input("채점기준", value=opts["scoring"])
        numbering = c3.selectbox("문항번호", ["continuous", "per_type"], index=["continuous", "per_type"].index(opts["numbering"]),
                                 format_func=lambda x: {"continuous": "전체 연번(양식)", "per_type": "유형별 1번부터(지침)"}[x])
        prefix = st.checkbox("문항 앞에 [과목/주제명] 붙이기 (유의사항 권고)", value=bool(opts["subject_prefix"]))

new_opts = {**opts, "difficulty_pct": dpct, "type_pct": tpct, "mcq_positive_pct": pos, "combo_pct": combo,
            "max_retries": retries, "score": score, "scoring": scoring, "numbering": numbering, "subject_prefix": prefix}

# ---------- 수량과 페이지 할당 ----------
st.subheader("수량과 페이지 할당")
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
    if mode != "추가 출제" or diff_counts is None:
        diff_counts = largest_remainder(n, dpct)
else:
    st.bar_chart(df.set_index("책 쪽수")[["기존 문항", "이번 출제"]], height=220, stack=True)

if n:
    preview = build_slots(pages, alloc, new_opts, difficulty_counts=diff_counts)
    sm = summarize(preview)
    c = st.columns(5)
    c[0].metric("이번 출제 슬롯", sm["total"])
    c[1].metric("출제 페이지", len(sm["pages"]))
    c[2].metric("페이지당", f"{min(sm['pages'].values())}~{max(sm['pages'].values())}")
    c[3].metric("난이도", " / ".join(f"{k}{sm['difficulty'].get(k, 0)}" for k in LV))
    c[4].metric("유형", " / ".join(f"{ui.TYPE_KO[k][:2]}{sm['type'].get(k, 0)}" for k in C["type_order"]))
    s = llm_settings.load()
    chunks = sum(math.ceil(v / max(s.chunk_size, 1)) for v in sm["pages"].values())
    est = int((chunks + 2 * len(sm["pages"])) * 1.4)
    st.caption(f"선다형 정답 위치 {dict(sorted((k, v) for k, v in sm['answer'].items() if k.isdigit()))} · "
               f"진위형 O/X {sm['answer'].get('O', 0)}/{sm['answer'].get('X', 0)} · 조합형 {sm['combo']} · "
               f"예상 LLM 호출 약 {est}회(재출제 포함 추정) · 공급자: {llm_settings.PROVIDERS[s.provider]}")

# ---------- 시작 ----------
problems = []
if dsum != 100:
    problems.append("난이도 합계가 100이 아니다.")
if tsum != 100:
    problems.append("유형 합계가 100이 아니다.")
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
    st.success("저장했다.")
if c2.button("출제 시작", type="primary", disabled=bool(problems)):
    p.update_meta(options=new_opts)
    start = sum(len(b["slots"]) for b in p.batches()) + 1
    slots = build_slots(pages, alloc, new_opts, difficulty_counts=diff_counts, start_index=start,
                        seed=start)
    kind = {"최초 출제": "initial", "추가 출제": "additional", "페이지 재출제(강제 할당)": "repair"}[mode]
    b = start_batch(p, llm_settings.load(), slots, new_opts, kind=kind)
    st.session_state["batch"] = b
    st.switch_page("pages/3_출제진행.py")
