"""Streamlit 공통 — 페이지 머리말, 상태 배지, 사이드바(프로젝트 선택·LLM 설정).

화면 틀은 app.py의 st.navigation이 만들고, 사이드바는 app.py에서 한 번만 그린다.
각 화면은 header()로 제목을 쓰고 current_project()로 선택된 프로젝트를 받는다.
"""
from __future__ import annotations

import streamlit as st

from .llm import settings as llm_settings
from .llm.client import LLMClient, login_status
from .store import Project, last_project, list_projects, set_last_project

STATUS_KO = {"approved": "승인", "auto_pass": "자동통과", "warn": "경고", "manual": "수동확인", "deleted": "삭제"}
TYPE_KO = {"mcq": "선다형", "ox": "진위형", "short": "단답형"}
_BADGE = {"approved": ("green", "check_circle"), "auto_pass": ("blue", "task_alt"), "warn": ("orange", "warning"),
          "manual": ("red", "error"), "deleted": ("gray", "delete")}


def header(title: str, icon: str, caption: str = ""):
    st.title(f":material/{icon}: {title}")
    if caption:
        st.caption(caption)


def status_badge(s: str) -> str:
    """마크다운 인라인 배지. 표(dataframe) 안에서는 STATUS_KO 글자를 쓴다."""
    color, icon = _BADGE.get(s, ("gray", "help"))
    return f":{color}-badge[:material/{icon}: {STATUS_KO.get(s, s)}]"


def bar_chart(df, x: str, cols: list[str], stack: bool = True, height: int = 220):
    """페이지별 막대그래프. x축 글자를 가로로 쓰고, 페이지가 많아 겹치면 일부 라벨만 보이게 한다."""
    import altair as alt
    long = df.melt(id_vars=[x], value_vars=cols, var_name="구분", value_name="문항")
    enc = dict(
        x=alt.X(f"{x}:N", sort=list(df[x]), title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        y=alt.Y("문항:Q", stack="zero" if stack else None, title=None),
        color=alt.Color("구분:N", sort=cols, legend=alt.Legend(orient="top", direction="horizontal", title=None)),
        tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip("구분:N"), alt.Tooltip("문항:Q", format=".1f")],
    )
    if not stack:
        enc["xOffset"] = alt.XOffset("구분:N", sort=cols)
    st.altair_chart(alt.Chart(long).mark_bar().encode(**enc).properties(height=height), width="stretch")


def current_project() -> Project | None:
    slug = st.session_state.get("project")
    return Project(slug) if slug in list_projects() else None


def setup(title: str):
    """(호환용) 페이지 설정은 app.py에서 한 번만 한다."""


def sidebar() -> Project | None:
    """(호환용) 사이드바는 app.py에서 그린다."""
    return current_project()


def render_sidebar():
    with st.sidebar:
        st.markdown("#### :material/folder_open: 프로젝트")
        projects = list_projects()
        if projects:
            # 이름이 길어 목록에서 잘리므로 선택한 프로젝트의 전체 이름을 아래에 따로 보여 준다
            names = {s: Project(s).meta.get("name", s) for s in projects}
            projects.sort(key=lambda s: names[s])
            # 마지막 작업 프로젝트를 파일로 기억한다: 새로고침·재시작에도 유지.
            # 열려 있는 화면은 CLI에서 바꾼 경우에만 따라간다(다른 사용자의 선택에 화면이 바뀌지 않게)
            state = last_project()
            saved = state.get("project")
            ss = st.session_state
            if ss.get("project_select") not in projects:  # 처음 열었을 때
                ss["project_select"] = saved if saved in projects else (
                    ss.get("project") if ss.get("project") in projects else projects[0])
            elif state.get("by") in ("cli", "page") and saved in projects and state.get("at") != ss.get("_cli_seen"):
                ss["project_select"] = saved
            ss["_cli_seen"] = state.get("at")
            sel = st.selectbox("과정(원고) 선택", projects, key="project_select", format_func=lambda s: names[s],
                               label_visibility="collapsed")
            if sel != saved:
                set_last_project(sel)
            ss["project"] = sel
            st.caption(f"선택한 프로젝트: **{names[sel]}** · 문항 {len(Project(sel).items())}개")
        with st.expander("새 프로젝트", expanded=not projects, icon=":material/add:"):
            name = st.text_input("과정명 (필수)", placeholder="예: MZ에서 시니어까지 세대공감 금융솔루션 3")
            memo = st.text_input("구분 메모", placeholder="예: 2026 상반기 · 300문항 본출제")
            st.caption("화면에는 '과정명 · 구분 메모'로 보입니다. 같은 과정을 여러 번 출제할 때 메모로 구분하세요.")
            if st.button("만들기", disabled=not name.strip()):
                try:
                    p = Project.create(name, memo)
                    st.session_state["project"] = p.slug
                    set_last_project(p.slug)
                    st.rerun()
                except (FileExistsError, ValueError) as e:
                    st.error(str(e))
        settings_box()


def settings_box():
    s = llm_settings.load()
    P = llm_settings.PROVIDERS
    keys = list(P)
    title = " / ".join(sorted({P[x].split(" (")[0] for x in s.providers_in_use()}))
    with st.expander(f"AI 설정 · {title}", expanded=False, icon=":material/smart_toy:"):
        provider = st.selectbox("생성 공급자", keys, index=keys.index(s.provider), format_func=lambda k: P[k])
        same = "(생성과 같음)"
        opts = [""] + keys
        cross = st.toggle("G4·G5는 다른 공급자로 교차 검증", value=bool(s.provider_solve or s.provider_review),
                          help="예: 생성은 Claude, 독립 풀이(G4)는 Codex — 다른 회사 모델이 정답을 다시 풀어 신뢰도를 높입니다.")
        ps, pr = "", ""
        if cross:
            ps = st.selectbox("G4 독립 풀이 공급자", opts, index=opts.index(s.provider_solve) if s.provider_solve in opts else 0,
                              format_func=lambda k: P.get(k, same))
            pr = st.selectbox("G5 검수 공급자", opts, index=opts.index(s.provider_review) if s.provider_review in opts else 0,
                              format_func=lambda k: P.get(k, same))

        def model_input(label, val, key, prov):
            """공급자별 모델 고르기. 목록에 없는 모델은 '직접 입력'. 고른 모델 설명을 바로 아래에 보여 준다."""
            if prov == "mock":
                return "mock"
            choices = llm_settings.model_choices(prov)
            if prov == "codex_cli" and val.startswith("claude"):
                val = ""  # 공급자를 바꾸기 전 Claude 모델명이 남아 있으면 Codex 기본값으로
            if prov in ("claude_agent_sdk", "anthropic") and val and not val.startswith("claude"):
                val = ""
            if prov == "anthropic" and not val:
                val = "claude-opus-5-5"
            keys_m = list(choices) + [llm_settings.CUSTOM]
            cur = val if val in choices else llm_settings.CUSTOM
            pick = st.selectbox(label, keys_m, index=keys_m.index(cur), key=f"{key}_{prov}_sel",
                                format_func=lambda k: "직접 입력(목록에 없는 모델)" if k == llm_settings.CUSTOM
                                else (choices[k].split(" — ")[0] if k else "기본값"))
            if pick == llm_settings.CUSTOM:
                return st.text_input(f"{label} 이름", value=val if val not in choices else "", key=key,
                                     placeholder="예: claude-opus-5-5, gpt-6.1-sol, deepseek-v4-flash").strip()
            st.caption(choices[pick])
            return pick
        mg = model_input("생성 모델", s.model_generate, "mg", provider)
        ms = model_input("G4 독립 풀이 모델", s.model_solve, "ms", ps or provider)
        mr = model_input("G5 검수 모델", s.model_review, "mr", pr or provider)
        st.caption("기본값을 고르면 로그인한 계정의 기본 모델을 씁니다. 실제로 쓴 모델은 프로젝트 관리 → 출제 이력의 '실제 모델'에 남습니다.")
        used = {provider, ps or provider, pr or provider}
        ak, bu, ok = s.anthropic_api_key, s.openai_base_url, s.openai_api_key
        if "anthropic" in used:
            ak = st.text_input("Anthropic API 키", value=s.anthropic_api_key, type="password",
                               help="Windows 자격 증명 저장소에 보관된다. 비워 두면 환경변수 ANTHROPIC_API_KEY 사용")
        if "openai_compat" in used:
            bu = st.text_input("LiteLLM 주소", value=s.openai_base_url, placeholder="http://192.168.x.x:4000")
            ok = st.text_input("API 키", value=s.openai_api_key, type="password", help="Windows 자격 증명 저장소에 보관됩니다")
        if used & set(llm_settings.OAUTH_PROVIDERS):
            st.caption("구독 로그인(OAuth) 방식은 이 PC의 CLI 로그인 정보를 쓴다 — Claude: Claude Code에서 `/login`, "
                       "Codex: 'Codex_로그인.bat'을 실행합니다. 이 도구는 로그인 토큰을 저장하지 않습니다.")
        c1, c2 = st.columns(2)
        chunk = c1.number_input("AI 한 번에 만드는 문항 수", 1, 20, s.chunk_size, help="AI 응답이 잘리면 줄입니다")
        workers = c2.number_input("병렬 페이지", 1, 8, s.workers)
        if provider == "mock":
            st.caption("데모 모드: 원고 문장으로 모의 문항을 만듭니다. 화면 흐름 확인용이며 납품에 쓰지 않습니다.")
        b1, b2 = st.columns(2)
        if b1.button("저장", width="stretch"):
            try:
                llm_settings.save(llm_settings.LLMSettings(
                    provider=provider, provider_solve=ps, provider_review=pr,
                    model_generate=mg, model_solve=ms, model_review=mr,
                    anthropic_api_key=ak, openai_base_url=bu, openai_api_key=ok,
                    chunk_size=int(chunk), workers=int(workers), max_tokens=s.max_tokens, timeout_sec=s.timeout_sec))
                st.success("저장했습니다.")
            except RuntimeError as e:
                st.error(str(e))
        if b2.button("연결·로그인 확인", width="stretch"):
            cur = llm_settings.load()
            for prov in cur.providers_in_use():
                if prov == "mock":
                    st.info("데모: 연결이 필요 없습니다.")
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
        st.info("왼쪽 사이드바에서 프로젝트를 만들거나 선택하세요.", icon=":material/arrow_back:")
        st.stop()
    return p
