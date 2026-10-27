"""기출문제 등록 — 이미 출제·서비스한 문항을 올려 두면 추가 출제에서 겹치지 않게 제외한다."""
import tempfile
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

from exam_gen import ui
from exam_gen.ingest import prior_items as P
from exam_gen.llm import settings as llm_settings
from exam_gen.llm.client import LLMClient

TYPE_KO = {"mcq": "선다형", "ox": "진위형", "short": "단답형"}
TYPE_EN = {v: k for k, v in TYPE_KO.items()}

p = ui.require_project(ui.current_project())
ui.header("기출문제 등록", "history_edu",
          "이미 출제·서비스한 문항을 올려 두면, 이 과정에서 추가 출제할 때 같은 내용을 다시 내지 않습니다. "
          "기출 문항은 출력·검수·품질 리포트에는 들어가지 않습니다.")

if not p.pages:
    st.warning("**원고를 먼저 등록하세요.** 기출은 원고에서 가장 관련 있는 쪽에 연결되어야 그 쪽을 출제할 때 AI에게 "
               "'피해야 할 문항'으로 전달됩니다. 원고 없이 올린 기출은 중복 검사에만 쓰이고, 나중에 원고를 등록하면 "
               "자동으로 쪽이 연결됩니다.", icon=":material/warning:")
    st.page_link("app_pages/register.py", label="원고 등록으로 가기", icon=":material/upload_file:")
st.info("**순서**: ① 원고 등록 → ② 기출문제 등록(이 화면) → ③ 출제 옵션 → ④ 출제. "
        "기출은 이 프로젝트 안에만 저장되므로, 같은 과정의 프로젝트에서 출제할 때만 제외됩니다.", icon=":material/info:")

# ---------- 등록된 기출 ----------
priors = p.items(status=["prior"])
with st.container(border=True):
    st.markdown(f"#### :material/inventory_2: 등록된 기출 {len(priors)}문항")
    if priors:
        by_src = Counter(it.get("prior_source", "-") for it in priors)
        st.dataframe(pd.DataFrame([{"파일": k, "문항": v} for k, v in by_src.items()]), hide_index=True, width="stretch")
        st.caption("원고에서 가장 관련 있는 쪽이 자동으로 연결되며, 그 쪽을 추가 출제할 때 '피해야 할 문항'으로 전달됩니다. "
                   "모든 기출은 새 문항의 중복 검사(G6)에도 쓰입니다.")
        unlinked = sum(1 for it in priors if it.get("page") is None)
        l1, l2 = st.columns([3, 1])
        l1.markdown(f"원고 쪽 연결: **{len(priors) - unlinked}문항 연결** · {unlinked}문항 미연결"
                    + (" — 원고와 겹치는 낱말이 적어 관련 쪽을 찾지 못한 문항입니다(중복 검사에는 쓰입니다)." if unlinked else ""))
        if l2.button("기출 쪽 다시 연결", icon=":material/link:", width="stretch", disabled=not p.pages,
                     help="원고를 나중에 올렸거나 새 판으로 바꿨을 때 누릅니다. AI를 쓰지 않습니다."):
            r = P.relink(p)
            st.toast(f"다시 연결했습니다: {r['linked']}/{r['total']}문항 연결, {r['changed']}문항 바뀜", icon=":material/link:")
            st.rerun()
        c1, c2 = st.columns([3, 1])
        rm = c1.selectbox("지울 기출 파일", list(by_src), label_visibility="collapsed")
        if c2.button("이 파일의 기출 지우기", icon=":material/delete:", width="stretch"):
            p.delete_items([it["key"] for it in priors if it.get("prior_source") == rm])
            st.rerun()
    else:
        st.caption("아직 없습니다.")

# ---------- 업로드 ----------
st.subheader(":material/upload_file: 기출 파일 올리기")
st.caption("**제출 양식 엑셀**(이 도구가 출력하는 양식)은 바로 읽습니다(AI 사용 없음). "
           "**그 밖의 엑셀·CSV·한글·워드·PDF**는 AI가 읽어 표준 양식으로 바꿉니다. 어느 경우든 저장 전에 아래 표에서 확인·수정합니다.")
up = st.file_uploader("기출 파일", type=["xlsx", "xlsm", "csv", "txt", "hwp", "hwpx", "docx", "pdf"],
                      label_visibility="collapsed")
state = st.session_state.setdefault("prior_import", {})

if up is not None and state.get("name") != up.name:
    state.clear()
    folder = p.dir / "prior"
    folder.mkdir(exist_ok=True)
    path = folder / up.name
    path.write_bytes(up.getvalue())
    state.update(name=up.name, path=str(path))
    if P.is_template_excel(path):
        state.update(items=P.parse_template_excel(path), how="제출 양식 엑셀로 인식 — AI 없이 바로 읽었습니다")
    else:
        with st.spinner("파일을 읽는 중…"):
            try:
                state["text"] = P.file_to_text(path)
            except Exception as e:  # noqa: BLE001
                state["error"] = f"파일을 읽지 못했습니다: {e}"

if state.get("error"):
    st.error(state["error"])
elif state.get("text") is not None and "items" not in state:
    n = P.estimate_calls(state["text"])
    st.info(f"제출 양식이 아닙니다. **AI가 읽어 표준 양식으로 바꿉니다** — 예상 AI 호출 약 {n}회"
            f"(글자 수 {len(state['text']):,}자). 구독 사용량을 조금 씁니다.", icon=":material/smart_toy:")
    if st.button("AI로 표준 양식 변환", type="primary", icon=":material/auto_fix_high:"):
        bar = st.progress(0.0, text="변환 중…")
        s = llm_settings.load()
        try:
            state["items"] = P.ai_extract(LLMClient(s), s, state["text"],
                                          progress=lambda i, t: bar.progress(i / t, text=f"변환 중… {i}/{t}"))
            state["how"] = "AI가 표준 양식으로 바꿨습니다 — 잘못 읽은 곳이 없는지 꼭 확인하세요"
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"변환에 실패했습니다: {e}")

# ---------- 미리보기·수정·저장 ----------
if state.get("items") is not None:
    items = state["items"]
    st.success(f"{state['how']} · {len(items)}문항", icon=":material/check_circle:")
    rows = []
    for it in items:
        ch = list(it.get("choices") or []) + [""] * 5
        rows.append({"저장": not P.problems(it), "유형": TYPE_KO.get(it["type"], "선다형"), "발문": it["stem"],
                     "진술(ㄱㄴㄷㄹ, 줄마다)": "\n".join(it.get("statements") or []),
                     "보기1": ch[0], "보기2": ch[1], "보기3": ch[2], "보기4": ch[3], "보기5": ch[4],
                     "정답": it.get("answer", ""), "유사답변(쉼표)": ", ".join(it.get("short_variants") or []),
                     "해설": it.get("explanation", ""), "확인 필요": ", ".join(P.problems(it))})
    st.caption("'확인 필요'가 있는 행은 기본으로 저장하지 않습니다. 칸을 고치고 '저장'에 체크하면 저장됩니다.")
    df = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch", num_rows="dynamic",
                        column_config={"유형": st.column_config.SelectboxColumn(options=list(TYPE_KO.values())),
                                       "확인 필요": st.column_config.TextColumn(disabled=True)},
                        key=f"prior_edit_{state['name']}")
    keep = []
    for _, r in df.iterrows():
        if not r["저장"]:
            continue
        keep.append({"type": TYPE_EN.get(r["유형"], "mcq"), "stem": str(r["발문"] or "").strip(),
                     "statements": [s.strip() for s in str(r["진술(ㄱㄴㄷㄹ, 줄마다)"] or "").splitlines() if s.strip()],
                     "choices": [str(r[f"보기{i}"]).strip() for i in range(1, 6) if str(r[f"보기{i}"] or "").strip()],
                     "answer": str(r["정답"] or "").strip(),
                     "short_variants": [v.strip() for v in str(r["유사답변(쉼표)"] or "").split(",") if v.strip()],
                     "explanation": str(r["해설"] or "").strip()})
    c1, c2 = st.columns([1, 3])
    if c1.button(f"기출 {len(keep)}문항 저장", type="primary", disabled=not keep, icon=":material/save:"):
        n = P.save(p, keep, state["name"])
        st.session_state.pop("prior_import", None)
        st.toast(f"기출 {n}문항을 저장했습니다(같은 발문은 한 번만).", icon=":material/check:")
        st.rerun()
    c2.caption("저장한 기출은 원고에서 가장 관련 있는 쪽에 자동으로 연결됩니다.")
