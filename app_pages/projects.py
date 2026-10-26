"""프로젝트 관리 — 목록(생성·마지막 수정·원고·문항·사용 모델), 이름 바꾸기, 삭제(휴지통)·복원, 배치 이력."""
import pandas as pd
import streamlit as st

from exam_gen import backup, ui
from exam_gen.store import (Project, delete_project, list_projects, list_trash, purge_trash, restore_project,
                            set_last_project)

KIND = {"initial": "최초 출제", "additional": "추가 출제", "repair": "페이지 재출제"}
STATUS_KO = {"approved": "승인", "auto_pass": "자동통과", "warn": "경고", "manual": "수동확인", "deleted": "삭제",
             "prior": "기출"}

ui.header("프로젝트 관리", "folder_managed", "과정(프로젝트)별로 언제 만들고 고쳤는지, 어떤 AI로 출제했는지 확인하고 "
                                             "이름 바꾸기·삭제·복원을 합니다.")


def _t(ts: str) -> str:
    return (ts or "")[:16].replace("T", " ")


slugs = list_projects()
if not slugs:
    st.info("프로젝트가 없습니다. 왼쪽 사이드바의 '새 프로젝트'에서 만드세요.")
infos = []
for s in slugs:
    try:
        infos.append(Project(s).info())
    except Exception as e:  # noqa: BLE001 — 손상된 프로젝트가 있어도 목록은 보여 준다
        infos.append({"slug": s, "name": f"{s} (읽기 오류: {e})", "created": "", "updated": "", "sources": [],
                      "pages": 0, "items": 0, "status": {}, "batches": [], "models": {}})
infos.sort(key=lambda x: x["updated"], reverse=True)

if infos:
    st.subheader(f":material/list: 프로젝트 {len(infos)}개")
    st.caption("마지막 수정이 최근인 순서입니다. 행을 고르면 아래에 자세한 정보와 관리 버튼이 나옵니다.")
    df = pd.DataFrame([{
        "프로젝트": x["name"], "만든 날": _t(x["created"]), "마지막 수정": _t(x["updated"]),
        "원고": ", ".join(f"{f}({n}쪽)" for f, n in x["sources"]) or "-", "문항": x["items"],
        "상태": " · ".join(f"{STATUS_KO.get(k, k)} {v}" for k, v in x["status"].items()) or "-",
        "출제 횟수": len(x["batches"]),
        "사용 AI": ", ".join(sorted({b["providers"] for b in x["batches"] if b["providers"] != "-"})) or "-",
    } for x in infos])
    sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                       key="proj_table")
    pick = sel.selection.rows[0] if sel and sel.selection.rows else None

    if pick is not None:
        x = infos[pick]
        p = Project(x["slug"])
        with st.container(border=True):
            st.markdown(f"#### :material/folder_open: {x['name']}")
            st.caption(f"만든 날 {_t(x['created'])} · 마지막 수정 {_t(x['updated'])} · 원고 {x['pages']}쪽 · 문항 {x['items']}개 · "
                       f"폴더 `projects/{x['slug']}`")
            b1, b2 = st.columns([1, 3])
            if b1.button("이 프로젝트 열기", type="primary", icon=":material/open_in_new:", width="stretch"):
                st.session_state["project"] = x["slug"]
                set_last_project(x["slug"], by="page")
                st.switch_page("app_pages/home.py")

            t1, t2, t3, t5, t4 = st.tabs(["출제 이력", "사용 모델", "이름 바꾸기", "백업", "삭제"])
            with t5:
                st.caption("출제가 끝날 때, 원고를 새 판으로 교체하기 전, 삭제하기 전에 자동으로 백업합니다(최근 10개 보관, "
                           "프로그램 폴더의 '백업'). 출력 파일(exports)은 백업하지 않습니다.")
                if st.button("지금 백업", key=f"bk_now_{x['slug']}", icon=":material/backup:"):
                    backup.backup(p, "수동")
                    st.rerun()
                bks = backup.list_backups(x["slug"])
                if not bks:
                    st.caption("백업이 없습니다.")
                for bk in bks:
                    b1, b2 = st.columns([4, 1.3])
                    b1.markdown(f"{bk['at']} · {bk['reason']} · {bk['mb']} MB")
                    if b2.button("이 시점으로 되돌리기", key=f"bk_rs_{bk['name']}", width="stretch",
                                 help="되돌리기 전 현재 상태도 자동으로 백업합니다."):
                        backup.restore(p, bk["path"])
                        st.toast("되돌렸습니다.", icon=":material/restore:")
                        st.rerun()
            with t1:
                if x["batches"]:
                    st.dataframe(pd.DataFrame([{
                        "배치": b["batch"], "날짜": _t(b["created"]), "종류": KIND.get(b["kind"], b["kind"]),
                        "슬롯": b["slots"], "상태": b["status"], "AI": b["providers"], "AI 호출": b["calls"],
                        "걸린 시간(분)": b["minutes"], "통과": b["accepted"], "수동확인": b["manual"],
                    } for b in x["batches"]]), hide_index=True, width="stretch")
                else:
                    st.caption("아직 출제하지 않았습니다.")
            with t2:
                if x["models"]:
                    st.dataframe(pd.DataFrame([{"AI·모델·프롬프트 버전": k, "문항 수": v} for k, v in
                                               sorted(x["models"].items(), key=lambda kv: -kv[1])]),
                                 hide_index=True, width="stretch")
                    st.caption("문항마다 출제한 AI와 프롬프트 버전이 기록됩니다. 모델 칸이 비어 있으면 그 AI의 기본 모델입니다.")
                else:
                    st.caption("출제 기록이 없습니다.")
            with t3:
                meta = p.meta
                n1, n2 = st.columns(2)
                new_name = n1.text_input("과정명", meta.get("course") or meta.get("name", ""), key=f"rn_n_{x['slug']}")
                new_memo = n2.text_input("구분 메모", meta.get("memo", ""), key=f"rn_m_{x['slug']}")
                if st.button("이름 저장", key=f"rn_b_{x['slug']}"):
                    try:
                        p.rename(new_name, new_memo)
                        st.toast("이름을 바꿨습니다.", icon=":material/check:")
                        st.rerun()
                    except (FileExistsError, ValueError) as e:
                        st.error(str(e))
            with t4:
                st.warning("삭제하면 이 프로젝트의 원고·문항·출력 파일이 **휴지통(projects/_삭제됨)으로 옮겨집니다.** "
                           "아래 '휴지통'에서 되살릴 수 있고, 휴지통을 비우면 완전히 지워집니다.", icon=":material/delete:")
                ok = st.checkbox(f"'{x['name']}' 프로젝트를 휴지통으로 옮깁니다", key=f"del_ok_{x['slug']}")
                if st.button("삭제", disabled=not ok, key=f"del_b_{x['slug']}", icon=":material/delete:"):
                    delete_project(x["slug"])
                    st.session_state.pop("project", None)
                    st.toast("휴지통으로 옮겼습니다.", icon=":material/delete:")
                    st.rerun()

# ---------- 휴지통 ----------
trash = list_trash()
with st.expander(f"휴지통 ({len(trash)}개)", icon=":material/delete_sweep:", expanded=False):
    if not trash:
        st.caption("휴지통이 비어 있습니다.")
    for t in trash:
        c1, c2, c3 = st.columns([4, 1, 1])
        c1.markdown(f"**{t['name']}** · 삭제 {_t(t['deleted'])}")
        if c2.button("되살리기", key=f"rs_{t['dir']}", icon=":material/restore_from_trash:", width="stretch"):
            slug = restore_project(t["dir"])
            st.toast(f"되살렸습니다: {slug}", icon=":material/check:")
            st.rerun()
        if c3.button("완전 삭제", key=f"pg_{t['dir']}", icon=":material/delete_forever:", width="stretch",
                     help="되돌릴 수 없습니다."):
            st.session_state[f"pg_confirm_{t['dir']}"] = True
        if st.session_state.get(f"pg_confirm_{t['dir']}"):
            st.error(f"'{t['name']}'을(를) 완전히 지웁니다. 되돌릴 수 없습니다.")
            if st.button("네, 완전히 지웁니다", key=f"pg_yes_{t['dir']}"):
                purge_trash(t["dir"])
                st.session_state.pop(f"pg_confirm_{t['dir']}", None)
                st.rerun()
