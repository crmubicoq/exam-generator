"""4. 검수(G7) — 왼쪽 문항 카드 목록, 오른쪽 읽기 화면(문항·원고 근거). 수정은 펼쳐서 한다."""
import copy
import html
import re
from collections import Counter

import pandas as pd
import streamlit as st

from exam_gen import integrity, review_ops, ui
from exam_gen.generate import choice_builder
from exam_gen.ingest.page_map import format_pages
from exam_gen.llm import settings as llm_settings
from exam_gen.output.evidence_pdf import item_viewer_payload, render_item_page

try:  # 검수 화면 PDF 뷰어(선택 패키지) — 없으면 페이지 이미지로 보여 준다
    from streamlit_pdf_viewer import pdf_viewer
except ImportError:
    pdf_viewer = None

PAGE_SIZE = 50          # 목록 한 쪽에 담는 문항 수
LIST_H = 660            # 왼쪽 목록 상자 높이(px) — 안에서만 스크롤
BODY_H = 480            # 오른쪽 문항 본문 상자 높이(px)
CIRCLED = "①②③④"
QUEUES = {"todo": ("확인 필요", ("manual", "warn")), "ms": ("원고 변경", ()), "skip": ("AI 검증 생략", ()),
          "auto": ("자동통과", ("auto_pass",)),
          "done": ("승인", ("approved",)), "del": ("삭제", ("deleted",)),
          "all": ("전체", ("manual", "warn", "auto_pass", "approved", "deleted"))}
_ICON = {"approved": ":green[:material/check_circle:]", "auto_pass": ":blue[:material/task_alt:]",
         "warn": ":orange[:material/warning:]", "manual": ":red[:material/error:]", "deleted": ":gray[:material/delete:]"}
_LIST_CSS = """<style>
.st-key-review_list, .st-key-review_list [data-testid="stVerticalBlock"] {gap:0.1rem !important;}
.st-key-review_list button {justify-content:flex-start; text-align:left; min-height:2rem; padding:0.1rem 0.6rem;}
.st-key-review_list button p {white-space:normal; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2;
  -webkit-box-orient:vertical; font-size:0.85em; line-height:1.35;}
</style>"""


def _label(it) -> str:
    return pages[it["page"]]["label"] if it.get("page") is not None else "-"


def _pages_of(it) -> str:
    return format_pages([pages[g]["label"] for g in it.get("source_pages") or []]) or _label(it)


def _plain(text: str) -> str:
    """카드 미리보기용: '다음 괄호 안에 … 작성하시오.' 같은 지시문을 빼고 본문만."""
    return re.sub(r"^다음[^.\[]*?(작성하시오|고르시오)\.?\s*", "", text or "").strip("[] ")


def _chip(color: str, text: str) -> str:
    """근거PDF와 같은 색(노랑·주황)의 범례 견본."""
    return (f"<span style='border:3px solid {color};border-radius:4px;padding:0 6px;"
            f"font-size:0.85em'>{text}</span>")


def _s(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip()


p = ui.require_project(ui.current_project())
from exam_gen.store import last_project, set_pref  # noqa: E402

h1, h2 = st.columns([3, 1.3], vertical_alignment="bottom")
h1.markdown("### :material/fact_check: 검수 &nbsp;<span style='font-size:0.55em;color:#888;font-weight:400'>"
            "문항과 원고 근거를 보고 승인·수정·삭제합니다. 납품용 출력에는 승인한 문항만 들어갑니다</span>",
            unsafe_allow_html=True)
if "who" not in st.session_state:
    st.session_state["who"] = last_project().get("reviewer", "")
who = h2.text_input("검수자 이름", key="who", placeholder="이름 입력",
                    help="승인 기록에 남습니다. 한 번 넣으면 다음에도 기억합니다.")
if who != last_project().get("reviewer", ""):
    set_pref(reviewer=who)

items = p.items()
if not items:
    st.info("출제된 문항이 없습니다. 출제 옵션에서 출제를 시작하세요.")
    st.stop()
pages = p.pages

if st.session_state.get("review_flash"):  # 직전 승인·삭제 결과 알림(다시 그린 뒤에도 보이게)
    st.toast(st.session_state.pop("review_flash"), icon=":material/check_circle:")

# ---------- 검수 진행률 · 대기열 · 상세 필터 ----------
cnt = Counter(it["status"] for it in items)
active = len(items) - cnt.get("deleted", 0)
st.progress(cnt.get("approved", 0) / active if active else 0.0,
            text=f"검수 진행 — 승인 {cnt.get('approved', 0)} / {active}문항 (삭제 제외)")
q1, q2 = st.columns([4, 1], vertical_alignment="center")
def _in_queue(it, q):
    if q == "ms":  # 원고를 새 판으로 바꾼 뒤 근거 문장이 바뀌거나 사라진 문항
        return bool((it.get("validation") or {}).get("ms_change")) and it["status"] != "deleted"
    if q == "skip":  # 빠른 출제로 AI 검증(G4·G5)을 생략한 문항 — 품질 리포트에서 한꺼번에 검증
        return bool((it.get("validation") or {}).get("llm_skipped")) and it["status"] != "deleted"
    return it["status"] in QUEUES[q][1]


queue = q1.segmented_control(
    "검수 대기열", list(QUEUES), default="todo", key="review_queue", label_visibility="collapsed",
    format_func=lambda k: f"{QUEUES[k][0]} {sum(1 for it in items if _in_queue(it, k))}") or "todo"
with q2.popover("상세 필터", icon=":material/filter_list:", width="stretch"):
    f_type = st.multiselect("유형", list(ui.TYPE_KO), format_func=lambda t: ui.TYPE_KO[t])
    f_diff = st.multiselect("난이도", ["최상", "상", "중", "하"])
    f_chasi = st.multiselect("차시", sorted({it.get("chasi") for it in items}))
    f_page = st.multiselect("책 쪽수", [x["label"] for x in pages])
    f_text = st.text_input("검색(발문·키)")
view = [it for it in items
        if _in_queue(it, queue) and (not f_type or it["type"] in f_type)
        and (not f_diff or it["difficulty"] in f_diff) and (not f_chasi or it.get("chasi") in f_chasi)
        and (not f_page or _label(it) in f_page)
        and (not f_text or f_text in it.get("stem", "") or f_text == it["key"])]
if not view:
    st.info(f"'{QUEUES[queue][0]}' 대기열에 문항이 없다.", icon=":material/inbox:")
    st.stop()

# ---------- 현재 문항: 문항 키로 기억 (필터가 바뀌거나 승인한 문항이 목록에서 빠져도 위치 유지) ----------
keys = [x["key"] for x in view]
if st.session_state.get("review_key") not in keys:
    st.session_state["review_key"] = keys[0]
pos = keys.index(st.session_state["review_key"])


def _go(to: int):
    st.session_state["review_key"] = keys[max(0, min(to, len(keys) - 1))]


def _pick(key: str):
    st.session_state["review_key"] = key


def _leave_to_next():
    """승인·삭제로 현재 문항이 목록에서 빠질 수 있으므로 다음(없으면 이전) 문항으로 옮겨 둔다."""
    nxt = keys[pos + 1] if pos + 1 < len(keys) else (keys[pos - 1] if pos > 0 else None)
    if nxt:
        st.session_state["review_key"] = nxt


saved = view[pos]                                 # 저장된 상태(미리보기·승인 판단용)
it = copy.deepcopy(saved)                         # 편집용 사본

left, right = st.columns([3.6, 6.4], gap="large")

# ================= 왼쪽: 문항 카드 목록 =================
with left:
    n_pages = (len(view) - 1) // PAGE_SIZE + 1
    lp = pos // PAGE_SIZE
    st.markdown(_LIST_CSS, unsafe_allow_html=True)
    st.caption(f"{QUEUES[queue][0]} {len(view)}문항" + (f" · 목록 {lp + 1}/{n_pages}" if n_pages > 1 else ""))
    # 고정 높이 상자 안에서만 스크롤 — 문항이 300개여도 페이지가 길어지지 않는다
    with st.container(height=LIST_H, key="review_list"):
        for x in view[lp * PAGE_SIZE:(lp + 1) * PAGE_SIZE]:
            cur = x["key"] == saved["key"]
            label = (f"{_ICON.get(x['status'], '')} **{x['key']}** · {ui.TYPE_KO[x['type']][:2]}·{x['difficulty']}"
                     f" · p.{_pages_of(x)} · {_plain(x.get('stem', ''))[:40]}")
            st.button(label, key=f"pick_{x['key']}", type="primary" if cur else "tertiary", width="stretch",
                      on_click=_pick, args=(x["key"],), help=x.get("stem", "")[:200])
    if n_pages > 1:
        l1, l2, l3 = st.columns([1, 2, 1], vertical_alignment="center")
        l1.button("", icon=":material/keyboard_double_arrow_left:", key="list_prev", disabled=lp == 0,
                  on_click=_go, args=((lp - 1) * PAGE_SIZE,), help="목록 앞쪽")
        l2.markdown(f"<div style='text-align:center'>목록 {lp + 1} / {n_pages}</div>", unsafe_allow_html=True)
        l3.button("", icon=":material/keyboard_double_arrow_right:", key="list_next", disabled=lp >= n_pages - 1,
                  on_click=_go, args=((lp + 1) * PAGE_SIZE,), help="목록 뒤쪽")
    with st.expander("일괄 승인", icon=":material/done_all:"):
        cand = [x["key"] for x in view if x["status"] == "auto_pass"]
        st.write(f"현재 목록의 '자동통과'(경고 없음) 문항 {len(cand)}개를 한꺼번에 승인합니다. 승인할 때 자동 검사를 다시 합니다.")
        if not st.session_state.get("who"):
            st.caption(":material/info: 위쪽 **검수자 이름**을 먼저 넣으세요.")
        sure = st.checkbox("내용을 확인했고 일괄 승인합니다")
        if st.button("일괄 승인", disabled=not (cand and sure and st.session_state.get("who"))):
            r = review_ops.bulk_approve(p, cand, st.session_state["who"])
            st.session_state["review_flash"] = f"승인 {len(r['approved'])} · 차단 {len(r['blocked'])}"
            st.rerun()

# ================= 오른쪽: 읽기 화면 =================
with right:
    # --- 이동 ---
    n1, n2, n3 = st.columns([1, 2, 1], vertical_alignment="center")
    n1.button("이전", icon=":material/chevron_left:", width="stretch", disabled=pos == 0,
              on_click=_go, args=(pos - 1,), key="nav_prev")
    n2.markdown(f"<div style='text-align:center'>{pos + 1} / {len(keys)}</div>", unsafe_allow_html=True)
    n3.button("다음", icon=":material/chevron_right:", width="stretch", disabled=pos == len(keys) - 1,
              on_click=_go, args=(pos + 1,), key="nav_next")

    # --- 머리말 ---
    st.markdown(f"### {saved['key']} {ui.status_badge(saved['status'])}")
    kind = ui.TYPE_KO[saved["type"]] + (" 조합형" if saved.get("statements") else "")
    if saved["type"] == "mcq" and not saved.get("statements"):
        kind += f" · {'옳은 것' if saved.get('polarity') == 'positive' else '옳지 않은 것'} 고르기"
    tgt = saved.get("difficulty_target")
    diff_txt = saved["difficulty"] + (f"(목표 {tgt})" if tgt and tgt != saved["difficulty"] else "")
    st.caption(f"{saved.get('chasi')}차시 · {kind} · 난이도 {diff_txt} · 원고 p.{_pages_of(saved)} · "
               f"근거 {saved.get('evidence_level') or '없음'} · 시도 {saved['validation'].get('attempts')} · "
               f"생성 {saved['gen'].get('provider')}")

    # --- 최종 선정(출력 화면의 자동 선정 결과) — 사람이 바꿀 수 있다 ---
    if any(it.get("selected") for it in items):
        _sel = st.toggle("최종 선정에 포함", value=bool(saved.get("selected")), key=f"sel_{saved['key']}",
                         help="출력 화면 '최종 문항 자동 선정' 결과입니다. 바꾸면 '최종 선정 문항만' 출력에 반영됩니다.")
        if _sel != bool(saved.get("selected")):
            _it = p.item(saved["key"])
            _it["selected"] = _sel
            p.save_item(_it, "final_select_manual", {"selected": _sel})
            st.rerun()

    # --- 원고 변경 표시 ---
    ms = saved["validation"].get("ms_change")
    if ms:
        from exam_gen import manuscript_update as MU
        with st.container(border=True):
            st.markdown(f"**:orange[:material/edit_document:] 원고 {ms['version']}판에서 근거가 {MU.FLAG_KO[ms['kind']]}되었습니다**")
            for d in ms["details"]:
                st.markdown(f"- 이전: “{d['old']}”  \n  → 새 원고: " + (f"“{d['new']}”" if d["new"] else ":red[삭제됨]"))
            if ms.get("ai_failed"):
                st.warning("AI 수정이 검증을 통과하지 못했습니다. 직접 고치거나 삭제하세요.", icon=":material/warning:")
            mode = "AI 수정(바뀐 부분만)" if ms["kind"] == "changed" else "재출제(같은 조건)"
            if st.button(f"이 문항만 {mode} — 예상 AI 호출 약 {MU.CALLS[ms['kind']]}회", key=f"ms_fix_{saved['key']}",
                         icon=":material/auto_fix_high:", help="문항 번호는 그대로 두고, 고친 칸은 엑셀 검수용 시트에 노란색으로 표시됩니다."):
                MU.start_revise(p, llm_settings.load(), [saved["key"]])
                st.switch_page("app_pages/progress.py")

    # --- 승인·삭제 (항상 보이게) ---
    with st.container(border=True):
        reasons = integrity.block_reasons(saved)
        need_conf = integrity.needs_evidence_confirm(saved)
        a2, a3, a4 = st.columns([1.2, 1.4, 1.1], vertical_alignment="bottom")
        if not who:
            st.caption(":material/info: 승인하려면 맨 위 **검수자 이름**을 넣으세요.")
        confirm = False
        if need_conf:
            confirm = st.checkbox("근거가 원고와 부분 일치(L1)합니다 — '원고 근거' 탭에서 하이라이트를 직접 확인했습니다",
                                  key=f"conf_{saved['key']}")
        if a2.button("승인", type="primary", icon=":material/check:", width="stretch",
                     disabled=not who or saved["status"] in ("deleted", "approved")):
            ok, fails = review_ops.approve(p, saved["key"], who, evidence_confirmed=confirm)
            if ok:  # 승인하면 다음 문항으로 넘어간다
                st.session_state["review_flash"] = f"{saved['key']} 승인했습니다"
                _leave_to_next()
                st.rerun()
            else:  # 막힌 이유가 사라지지 않도록 다시 그리지 않는다
                st.error("승인할 수 없습니다: " + " / ".join(fails), icon=":material/block:")
        if a3.button("AI 재검증", icon=":material/refresh:", width="stretch",
                     help="문항을 고친 뒤 AI 독립 풀이와 AI 검수를 다시 실행합니다(AI 호출 약 2회)"):
            with st.spinner("AI 재검증 중…"):
                review_ops.rerun_llm_gates(p, llm_settings.load(), saved["key"])
            st.rerun()
        if saved["status"] != "deleted":
            if a4.button("삭제", icon=":material/delete:", width="stretch"):
                review_ops.set_status(p, saved["key"], "deleted", "")
                st.session_state["review_flash"] = f"{saved['key']} 삭제했습니다"
                _leave_to_next()
                st.rerun()
        elif a4.button("복원", icon=":material/restore:", width="stretch"):
            review_ops.set_status(p, saved["key"], "restore", "")
            st.rerun()
        if reasons and saved["status"] != "deleted":
            st.caption(":material/info: 승인 전 해결할 것 — " + " / ".join(reasons))

    for f in saved["validation"].get("fails", []):
        st.error(f, icon=":material/block:")
    warns = saved["validation"].get("warnings", [])
    if warns:
        with st.expander(f"경고 {len(warns)}건", icon=":material/warning:", expanded=False):
            for w in warns:
                st.markdown(f"- {w}")

    tab_q, tab_src, tab_check, tab_hist = st.tabs([":material/article: 문항", ":material/menu_book: 원고 근거",
                                                   ":material/rule: 검증 상세", ":material/history: 이력"])

    # --- 문항 읽기 (시험지 형태) ---
    with tab_q, st.container(height=BODY_H, border=False):  # 탭 안에서만 스크롤
        with st.container(border=True):
            st.markdown(f"<div style='font-size:1.15em;font-weight:600;line-height:1.6'>"
                        f"{html.escape(saved.get('stem', ''))}</div>", unsafe_allow_html=True)
            lines = [f"**{s['label']}.** {s['text']}" for s in saved.get("statements") or []]
            if saved["type"] == "mcq":
                for i, ch in enumerate(saved.get("choices") or []):
                    is_ans = str(i + 1) == str(saved["answer"])
                    mark = " :green-badge[:material/check: 정답]" if is_ans else ""
                    lines.append(f"{CIRCLED[i]} {'**' + ch['text'] + '**' if is_ans else ch['text']}{mark}")
            if lines:  # 한 블록으로 묶어 줄 간격을 좁힌다
                st.markdown("  \n".join(lines))
            elif saved["type"] == "ox":
                st.markdown(f"정답 :green-badge[{saved['answer']}]")
            else:
                alt = f" · 유사답변: {', '.join(saved['short_variants'])}" if saved.get("short_variants") else ""
                st.markdown(f"정답 :green-badge[{saved['answer']}]{alt}")
        st.markdown("**해설**")
        st.markdown(saved.get("explanation", ""))
        with st.expander("근거 인용문", icon=":material/format_quote:"):
            for e in saved.get("evidence") or []:
                if not e.get("quote"):
                    continue
                m = e.get("match")
                where = f"p.{m['label']} · {m['level']}" if m else ":red[원고에서 못 찾음]"
                kind_e = ":orange[함정 원문]" if not e.get("is_true") else "참 진술 근거"
                st.markdown(f"- **[{e.get('target')}]** {kind_e} · {where}  \n  “{e['quote']}”"
                            + (f"  \n  :gray[{e['trap_note']}]" if e.get("trap_note") and not e.get("is_true") else ""))

        # --- 수정 (펼쳐서) ---
        with st.expander("문항 수정", icon=":material/edit:"):
            it["stem"] = st.text_area("발문", it.get("stem", ""), height=90)
            if it.get("statements"):
                sdf = st.data_editor(pd.DataFrame(it["statements"]), hide_index=True, width="stretch",
                                     disabled=["label"], key=f"st_{it['key']}")
                it["statements"] = sdf.to_dict("records")
                st.caption("진술의 참/거짓을 바꾸면 보기 조합이 다시 만들어진다.")
            elif it["type"] == "mcq":
                cdf = st.data_editor(pd.DataFrame([{"번호": i + 1, "보기": ch["text"], "참": ch["is_true"]}
                                                   for i, ch in enumerate(it["choices"])]),
                                     hide_index=True, width="stretch", disabled=["번호"], key=f"ch_{it['key']}")
                it["choices"] = [{"text": r["보기"], "is_true": bool(r["참"])} for _, r in cdf.iterrows()]
            c1, c2 = st.columns(2)
            if it["type"] == "ox":
                it["answer"] = c1.radio("정답", ["O", "X"], index=0 if it["answer"] == "O" else 1, horizontal=True)
            elif it["type"] == "mcq":
                it["answer"] = c1.selectbox("정답", ["1", "2", "3", "4"], index=["1", "2", "3", "4"].index(str(it["answer"])))
            else:
                it["answer"] = c1.text_input("정답", it["answer"])
                v = c2.text_input("유사답변({^} 구분)", "{^}".join(it.get("short_variants") or []))
                it["short_variants"] = [x.strip() for x in v.split("{^}") if x.strip()]
            it["explanation"] = st.text_area("해설", it.get("explanation", ""), height=90)
            st.markdown("**근거 인용문** (원고 문장을 그대로 — 저장 시 원고에서 다시 찾는다)")
            edf = st.data_editor(pd.DataFrame([{"대상": e.get("target"), "인용문": e.get("quote", ""), "쪽": e.get("page_label", ""),
                                                "참 진술": bool(e.get("is_true")), "함정 메모": e.get("trap_note", ""),
                                                "찾음": (f"p.{e['match']['label']} {e['match']['level']}" if e.get("match") else "✗")}
                                               for e in it.get("evidence") or []]),
                                 hide_index=True, width="stretch", num_rows="dynamic", disabled=["찾음"],
                                 key=f"ev_{it['key']}")
            it["evidence"] = [{"target": _s(r["대상"]), "quote": _s(r["인용문"]), "page_label": _s(r["쪽"]),
                               "is_true": bool(r["참 진술"]) if not pd.isna(r["참 진술"]) else False,
                               "trap_note": _s(r["함정 메모"])}
                              for _, r in edf.iterrows() if _s(r["대상"])]
            note = st.text_input("수정 메모(선택)")
            if st.button("수정 저장", icon=":material/save:"):
                if it.get("statements"):
                    correct = [s["label"] for s in it["statements"] if s.get("is_true")]
                    sets = choice_builder.build(correct, int(it["answer"]))
                    it["choices"] = [{"text": choice_builder.fmt(x), "labels": x,
                                      "is_true": x == sorted(correct, key="ㄱㄴㄷㄹ".index)} for x in sets]
                    it["slot"]["combo_true"] = len(correct)
                it["slot"]["answer"] = str(it["answer"])
                r = review_ops.save_edit(p, it, note)
                st.session_state["review_flash"] = ("저장했습니다 · " + ("자동 검사 탈락: " + "; ".join(r["validation"]["fails"])
                                                                if r["validation"]["fails"] else "승인 전에 'AI 재검증'을 누르세요"))
                st.rerun()

    # --- 원고 근거 ---
    with tab_src, st.container(height=BODY_H, border=False):  # 탭 안에서만 스크롤
        # 미리보기는 저장된 문항(근거 매칭 결과 포함) 기준 — 편집 중 내용은 저장하면 다시 매칭된다 (Codex C8)
        gs = saved.get("source_pages") or ([saved["page"]] if saved.get("page") is not None else [])
        if not gs:
            st.warning("원고에서 확인된 근거 위치가 없다(근거 미매칭 또는 원고 삭제).", icon=":material/link_off:")
        else:
            c1, c2 = st.columns([3, 2], vertical_alignment="center")
            c1.markdown(f"원고 p.{_pages_of(saved)} &nbsp; "
                        f"{_chip('#f5c400', '참 진술 근거')} {_chip('#2f6fe0', '함정이 바꾼 원문')}",
                        unsafe_allow_html=True)
            viewer = c2.toggle("PDF 뷰어(근거로 자동 이동)", value=pdf_viewer is not None, disabled=pdf_viewer is None,
                               help="끄면 페이지 이미지로 본다. 뷰어 표시가 이상하면 끄세요.")
            if viewer:
                pdf, anns, _ = item_viewer_payload(p, saved)
                pdf_viewer(pdf, annotations=anns, scroll_to_annotation=1 if anns else None, render_text=True,
                           annotation_outline_size=3, height=BODY_H - 70,
                           key=f"pdf_{saved['key']}_{saved.get('updated', '')}")
            else:
                for g in gs:
                    st.image(render_item_page(p, saved, g), caption=f"원고 p.{pages[g]['label']}", width="stretch")

    # --- 검증 상세 ---
    with tab_check, st.container(height=BODY_H, border=False):  # 탭 안에서만 스크롤
        g4 = saved["validation"].get("G4")
        if g4:
            st.markdown(f"**AI 독립 풀이(G4)** — 정답을 숨기고 다시 푼 답 `{g4.get('answer')}` (출제 정답 `{saved['answer']}`) · "
                        f"정답 유일 {g4.get('unique')} · 원고로 풀이 가능 {g4.get('solvable')}")
            if g4.get("judgments"):
                st.dataframe(pd.DataFrame(g4["judgments"]), hide_index=True, width="stretch")
            if g4.get("note"):
                st.caption(g4["note"])
        g5 = saved["validation"].get("G5")
        if g5:
            st.markdown(f"**AI 검수(G5)** · 실제 난이도 판정 `{g5.get('assessed_difficulty', '-')}`")
            st.dataframe(pd.DataFrame(g5.get("checks", [])), hide_index=True, width="stretch")
            if g5.get("suggestion"):
                st.info("수정안: " + g5["suggestion"])
        g6 = saved["validation"].get("G6")
        if g6:
            st.markdown(f"**중복 검사(G6)**: 가장 비슷한 문항 {g6.get('key')} (유사도 {g6.get('score')}) {g6.get('stem', '')}")
        st.markdown(f"**난이도 근거**: {saved.get('difficulty_basis', '')}  \n**판단 포인트**: {saved.get('key_point', '')}")

    # --- 이력 ---
    with tab_hist, st.container(height=BODY_H, border=False):  # 탭 안에서만 스크롤
        ev = p.events(key=saved["key"], limit=50)
        st.dataframe(pd.DataFrame([{"시각": e["ts"], "작업": e["kind"], "내용": str(e["detail"])[:200]} for e in ev]),
                     hide_index=True, width="stretch")
