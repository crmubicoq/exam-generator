"""Streamlit 공통 — 프로젝트 선택과 LLM 설정 사이드바 (tsbookmaker 사이드바 구성 참고)."""
from __future__ import annotations

import streamlit as st

from .llm import settings as llm_settings
from .llm.client import LLMClient
from .store import Project, list_projects

STATUS_KO = {"approved": "승인", "auto_pass": "자동통과", "warn": "경고", "manual": "수동확인", "deleted": "삭제"}
TYPE_KO = {"mcq": "선다형", "ox": "진위형", "short": "단답형"}


def setup(title: str):
    st.set_page_config(page_title=f"평가문항 출제 · {title}", page_icon="📝", layout="wide")


def sidebar() -> Project | None:
    with st.sidebar:
        st.markdown("### 프로젝트")
        projects = list_projects()
        cur = st.session_state.get("project")
        if projects:
            idx = projects.index(cur) if cur in projects else 0
            sel = st.selectbox("과정(원고) 선택", projects, index=idx, label_visibility="collapsed")
            st.session_state["project"] = sel
        with st.expander("새 프로젝트", expanded=not projects):
            name = st.text_input("과정명", placeholder="예: MZ에서 시니어까지 세대공감 금융솔루션 3")
            if st.button("만들기", disabled=not name):
                try:
                    p = Project.create(name)
                    st.session_state["project"] = p.slug
                    st.rerun()
                except FileExistsError as e:
                    st.error(str(e))
        settings_box()
    slug = st.session_state.get("project")
    return Project(slug) if slug in list_projects() else None


def settings_box():
    s = llm_settings.load()
    with st.expander(f"LLM 설정 · {llm_settings.PROVIDERS[s.provider].split(' (')[0]}", expanded=False):
        provider = st.selectbox("공급자", list(llm_settings.PROVIDERS), index=list(llm_settings.PROVIDERS).index(s.provider),
                                format_func=lambda k: llm_settings.PROVIDERS[k])
        presets = llm_settings.MODEL_PRESETS[provider]
        def model_input(label, val, key):
            return st.text_input(label, value=val if provider != "mock" else "mock", key=key,
                                 help="예: " + ", ".join(presets))
        mg = model_input("생성 모델", s.model_generate, "mg")
        ms = model_input("독립 풀이(G4) 모델", s.model_solve, "ms")
        mr = model_input("검수(G5) 모델", s.model_review, "mr")
        ak, bu, ok = s.anthropic_api_key, s.openai_base_url, s.openai_api_key
        if provider == "anthropic":
            ak = st.text_input("Anthropic API 키", value=s.anthropic_api_key, type="password",
                               help="비워 두면 환경변수 ANTHROPIC_API_KEY 사용")
        if provider == "openai_compat":
            bu = st.text_input("LiteLLM 주소", value=s.openai_base_url, placeholder="http://192.168.x.x:4000")
            ok = st.text_input("API 키", value=s.openai_api_key, type="password")
        c1, c2 = st.columns(2)
        chunk = c1.number_input("호출당 슬롯 수", 1, 20, s.chunk_size, help="출력이 잘리면 줄인다")
        workers = c2.number_input("병렬 페이지", 1, 8, s.workers)
        if provider == "mock":
            st.caption("데모 모드: 원고 문장으로 모의 문항을 만든다. 화면 흐름 확인용이며 납품에 쓰지 않는다.")
        b1, b2 = st.columns(2)
        if b1.button("저장", width="stretch"):
            s = llm_settings.LLMSettings(provider=provider, model_generate=mg, model_solve=ms, model_review=mr,
                                         anthropic_api_key=ak, openai_base_url=bu, openai_api_key=ok,
                                         chunk_size=int(chunk), workers=int(workers), max_tokens=s.max_tokens,
                                         timeout_sec=s.timeout_sec)
            llm_settings.save(s)
            st.success("저장했다.")
        if b2.button("연결 테스트", width="stretch"):
            try:
                c = LLMClient(llm_settings.load())
                if llm_settings.load().provider == "mock":
                    st.info("데모 모드는 연결이 필요 없다.")
                else:
                    out = c.structured("JSON으로만 답한다.", "ok 필드에 true를 넣어라.",
                                       {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                                       model=llm_settings.load().model_generate, task="ping")
                    st.success(f"연결 성공: {out}")
            except Exception as e:  # noqa: BLE001
                st.error(f"연결 실패: {e}")


def require_project(p: Project | None) -> Project:
    if p is None:
        st.info("왼쪽에서 프로젝트를 만들거나 선택하세요.")
        st.stop()
    return p


def status_badge(s: str) -> str:
    return {"approved": "🟢 승인", "auto_pass": "🔵 자동통과", "warn": "🟡 경고", "manual": "🔴 수동확인",
            "deleted": "⚫ 삭제"}.get(s, s)
