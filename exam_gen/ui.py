"""Streamlit 공통 — 프로젝트 선택과 LLM 설정 사이드바 (tsbookmaker 사이드바 구성 참고)."""
from __future__ import annotations

import streamlit as st

from .llm import settings as llm_settings
from .llm.client import LLMClient, login_status
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
    P = llm_settings.PROVIDERS
    keys = list(P)
    title = " / ".join(sorted({P[x].split(" (")[0] for x in s.providers_in_use()}))
    with st.expander(f"LLM 설정 · {title}", expanded=False):
        provider = st.selectbox("생성 공급자", keys, index=keys.index(s.provider), format_func=lambda k: P[k])
        same = "(생성과 같음)"
        opts = [""] + keys
        cross = st.toggle("G4·G5는 다른 공급자로 교차 검증", value=bool(s.provider_solve or s.provider_review),
                          help="예: 생성은 Claude, 독립 풀이(G4)는 Codex — 다른 회사 모델이 정답을 다시 풀어 신뢰도를 높인다.")
        ps, pr = "", ""
        if cross:
            ps = st.selectbox("G4 독립 풀이 공급자", opts, index=opts.index(s.provider_solve) if s.provider_solve in opts else 0,
                              format_func=lambda k: P.get(k, same))
            pr = st.selectbox("G5 검수 공급자", opts, index=opts.index(s.provider_review) if s.provider_review in opts else 0,
                              format_func=lambda k: P.get(k, same))

        def model_input(label, val, key, prov):
            presets = [x for x in llm_settings.MODEL_PRESETS[prov] if x]
            if prov == "mock":
                return "mock"
            hint = ("비워 두면 Codex CLI 기본 모델" if prov == "codex_cli" else "예: " + ", ".join(presets))
            return st.text_input(label, value=val if (prov != "codex_cli" or not val.startswith("claude")) else "",
                                 key=key, help=hint, placeholder=hint)
        mg = model_input("생성 모델", s.model_generate, "mg", provider)
        ms = model_input("G4 모델", s.model_solve, "ms", ps or provider)
        mr = model_input("G5 모델", s.model_review, "mr", pr or provider)
        used = {provider, ps or provider, pr or provider}
        ak, bu, ok = s.anthropic_api_key, s.openai_base_url, s.openai_api_key
        if "anthropic" in used:
            ak = st.text_input("Anthropic API 키", value=s.anthropic_api_key, type="password",
                               help="Windows 자격 증명 저장소에 보관된다. 비워 두면 환경변수 ANTHROPIC_API_KEY 사용")
        if "openai_compat" in used:
            bu = st.text_input("LiteLLM 주소", value=s.openai_base_url, placeholder="http://192.168.x.x:4000")
            ok = st.text_input("API 키", value=s.openai_api_key, type="password", help="Windows 자격 증명 저장소에 보관된다")
        if used & set(llm_settings.OAUTH_PROVIDERS):
            st.caption("구독 로그인(OAuth) 방식은 이 PC의 CLI 로그인 정보를 쓴다 — Claude: Claude Code에서 `/login`, "
                       "Codex: 터미널에서 `codex login`. 이 툴은 로그인 토큰을 저장하지 않는다.")
        c1, c2 = st.columns(2)
        chunk = c1.number_input("호출당 슬롯 수", 1, 20, s.chunk_size, help="출력이 잘리면 줄인다")
        workers = c2.number_input("병렬 페이지", 1, 8, s.workers)
        if provider == "mock":
            st.caption("데모 모드: 원고 문장으로 모의 문항을 만든다. 화면 흐름 확인용이며 납품에 쓰지 않는다.")
        b1, b2 = st.columns(2)
        if b1.button("저장", width="stretch"):
            try:
                llm_settings.save(llm_settings.LLMSettings(
                    provider=provider, provider_solve=ps, provider_review=pr,
                    model_generate=mg, model_solve=ms, model_review=mr,
                    anthropic_api_key=ak, openai_base_url=bu, openai_api_key=ok,
                    chunk_size=int(chunk), workers=int(workers), max_tokens=s.max_tokens, timeout_sec=s.timeout_sec))
                st.success("저장했다.")
            except RuntimeError as e:
                st.error(str(e))
        if b2.button("연결·로그인 확인", width="stretch"):
            cur = llm_settings.load()
            for prov in cur.providers_in_use():
                if prov == "mock":
                    st.info("데모: 연결이 필요 없다.")
                    continue
                with st.spinner(f"{P[prov]} 확인 중…"):
                    if prov in llm_settings.OAUTH_PROVIDERS:
                        good, msg = login_status(prov, cur)
                    else:
                        try:
                            LLMClient(cur).structured(
                                "JSON으로만 답한다.", "ok 필드에 true를 넣어라.",
                                {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                                model=cur.model_generate, task="ping", provider=prov)
                            good, msg = True, "연결 성공"
                        except Exception as e:  # noqa: BLE001
                            good, msg = False, str(e)
                (st.success if good else st.error)(f"{P[prov].split(' (')[0]}: {msg}")


def require_project(p: Project | None) -> Project:
    if p is None:
        st.info("왼쪽에서 프로젝트를 만들거나 선택하세요.")
        st.stop()
    return p


def status_badge(s: str) -> str:
    return {"approved": "🟢 승인", "auto_pass": "🔵 자동통과", "warn": "🟡 경고", "manual": "🔴 수동확인",
            "deleted": "⚫ 삭제"}.get(s, s)
