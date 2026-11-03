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


DONE, DOING, TODO = "done", "doing", "todo"


def flow(p: Project | None):
    """프로젝트 진행 상태 → ({단계: done|doing|todo}, 지금 할 일(문장, 화면, 아이콘), 문항 상태 Counter).
    홈 화면과 사이드바 단계 메뉴가 같이 쓴다."""
    from collections import Counter

    from .generate.generator import batch_state
    state = {k: TODO for k in ("register", "prior", "options", "progress", "quality", "review", "export")}
    nxt = ("왼쪽 사이드바의 **새 프로젝트**를 펼쳐 과정명을 입력하고 **만들기**를 누르세요.", None, "add")
    c = Counter()
    if p:
        m = p.meta
        items = p.items()
        c = Counter(it["status"] for it in items)
        batches = p.batches()
        states = [batch_state(p, b["batch"]) for b in batches]
        pending = c.get("manual", 0) + c.get("warn", 0) + c.get("auto_pass", 0)
        exports = list((p.dir / "exports").glob("*")) if (p.dir / "exports").exists() else []

        if m["sources"] and p.page_map_ready():
            state["register"] = DONE
        elif m["sources"]:
            state["register"] = DOING
        if p.items(status=["prior"]):
            state["prior"] = DONE
        if batches:
            state["options"] = DONE
        if any(s in ("running_here", "running_elsewhere") for s in states):
            state["progress"] = DOING
        elif batches:
            state["progress"] = DONE
            state["quality"] = DONE
        if items and pending == 0 and c.get("approved"):
            state["review"] = DONE
        elif c.get("approved"):
            state["review"] = DOING
        if exports:
            state["export"] = DONE

        if not m["sources"]:
            nxt = ("**원고 등록**에서 원고 파일(PDF·PPTX·DOCX·HWP)을 올리세요.", "app_pages/register.py", "upload_file")
        elif not p.page_map_ready():
            nxt = ("**원고 등록**에서 책 쪽수가 맞는지 확인하고 **이 쪽수로 확정**을 누르세요. 확정해야 출제할 수 있습니다.",
                   "app_pages/register.py", "format_list_numbered")
        elif not batches:
            nxt = ("**출제 옵션**에서 문항 수와 난이도 비율을 정하고 '출제 시작'을 누르세요.", "app_pages/options.py", "tune")
        elif state["progress"] == DOING:
            nxt = ("출제가 진행 중입니다. **출제 진행**에서 진행률과 예상 남은 시간을 볼 수 있습니다(300문항+여유 15% 기준 약 3시간 15분, 난이도 보정 출제 포함 실측 추정).",
                   "app_pages/progress.py", "autorenew")
        elif any(s in ("stopped", "partial", "error", "orphan") for s in states):
            nxt = ("중단되거나 일부만 끝난 출제가 있습니다. **출제 진행**에서 '이어서 출제'를 누르세요.",
                   "app_pages/progress.py", "replay")
        elif pending:
            nxt = (f"검수할 문항이 **{pending}개** 남았습니다. **검수**에서 확인하고 승인하세요. "
                   "승인한 문항만 최종 출력에 들어갑니다.", "app_pages/review.py", "fact_check")
        elif c.get("approved"):
            nxt = (f"승인된 문항 **{c['approved']}개**를 **출력**에서 엑셀과 근거PDF로 내려받으세요.",
                   "app_pages/export.py", "download")
    return state, nxt, c


def render_sidebar():
    with st.sidebar:
        st.markdown("#### :material/folder_open: 프로젝트")
        projects = list_projects()
        if projects:
            # 같은 과정을 여러 번 출제하면 긴 과정명에 가려 구분 메모가 안 보인다 — 목록에는 메모를 앞에 둔다.
            # 전체 이름은 목록 아래에 따로 보여 준다. 최근에 만든 프로젝트가 위로
            metas = {s: Project(s).meta for s in projects}
            names = {s: m.get("name", s) for s, m in metas.items()}

            def _label(s):
                m = metas[s]
                course, memo = (m.get("course") or m.get("name") or s).strip(), (m.get("memo") or "").strip()
                return f"{memo} — {course}" if memo else course  # 닫힌 칸에서는 메모가 먼저 보이고, 펼치면 전체가 보인다

            projects.sort(key=lambda s: metas[s].get("created", ""), reverse=True)
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
            sel = st.selectbox("과정(원고) 선택", projects, key="project_select", format_func=_label,
                               label_visibility="collapsed")
            if sel != saved:
                set_last_project(sel)
            ss["project"] = sel
            _m = metas[sel]
            st.caption(f"과정: **{_m.get('course') or names[sel]}**"
                       + (f"  \n구분: **{_m['memo']}**" if _m.get("memo") else "")
                       + f"  \n만든 날: {(_m.get('created') or '')[:10]}")
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
        _step_menu()
        settings_box()


# 단계 메뉴: (단계 키, 화면, 아이콘, 이름). 단계 키가 None이면 상태 표시 없음
FLOW_MENU = [
    (None, "app_pages/home.py", "home", "홈 · 지금 할 일"),
    ("register", "app_pages/register.py", "upload_file", "1 원고 등록"),
    ("prior", "app_pages/prior.py", "history_edu", "2 기출문제 등록 (선택)"),
    ("options", "app_pages/options.py", "tune", "3 출제 옵션"),
    ("progress", "app_pages/progress.py", "autorenew", "4 출제 진행"),
    ("review", "app_pages/review.py", "fact_check", "5 검수"),
    ("export", "app_pages/export.py", "download", "6 출력"),
]
MANAGE_MENU = [
    ("app_pages/quality.py", "analytics", "품질 리포트"),
    ("app_pages/appeal.py", "gavel", "이의신청 조회"),
    ("app_pages/difficulty.py", "school", "난이도 기준"),
    ("app_pages/projects.py", "folder_managed", "프로젝트 관리"),
]


def _step_menu():
    """진행 순서대로 번호를 붙인 메뉴 — 단계마다 완료(✓)·진행 중·지금 할 일 표시. 메뉴 자체가 단계 안내 역할을 한다."""
    st.divider()
    st.markdown("**진행 순서**")
    state, nxt, _ = flow(current_project())
    for k, page, icon, title in FLOW_MENU:
        mark = ""
        if k:
            if page == nxt[1]:
                mark = "  :orange[◀ 지금 할 일]"
            elif state.get(k) == DONE:
                mark = "  :green[✓]"
            elif state.get(k) == DOING:
                mark = "  :orange[진행 중]"
        st.page_link(page, label=title + mark, icon=f":material/{icon}:")
    st.markdown("**관리**")
    for page, icon, title in MANAGE_MENU:
        st.page_link(page, label=title, icon=f":material/{icon}:")
    st.divider()


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
                               help="Windows 자격 증명 저장소에 보관됩니다. 비워 두면 환경변수 ANTHROPIC_API_KEY를 씁니다.")
        if "openai_compat" in used:
            bu = st.text_input("LiteLLM 주소", value=s.openai_base_url, placeholder="http://192.168.x.x:4000")
            ok = st.text_input("API 키", value=s.openai_api_key, type="password", help="Windows 자격 증명 저장소에 보관됩니다")
        if used & set(llm_settings.OAUTH_PROVIDERS):
            st.caption("구독 로그인 방식은 이 PC에 로그인한 계정을 씁니다. Claude는 'Claude_로그인.bat', "
                       "Codex는 'Codex_로그인.bat'을 처음 한 번 실행해 로그인합니다. 이 도구는 로그인 정보를 저장하지 않습니다.")
        st.markdown("**출제 속도 설정**")
        st.caption("문항 내용·품질에는 영향이 없습니다. 보통은 기본값(8 · 3) 그대로 둡니다.")
        chunk = st.number_input("AI 한 번에 만드는 문항 수 (기본 8)", 1, 20, s.chunk_size)
        st.caption("한 페이지에 배정된 문항을 AI에 몇 개씩 묶어 요청할지 정합니다. 페이지당 1~2문항이 보통이라 거의 영향이 없습니다. "
                   "'AI 응답이 잘렸다'는 오류가 날 때만 줄이세요.")
        workers = st.number_input("병렬 페이지 (기본 3)", 1, 8, s.workers)
        st.caption("동시에 출제할 페이지 수입니다. 예상 시간은 3 기준 실측입니다. 올리면 빨라질 수 있지만 AI 사용 한도에 "
                   "더 빨리 걸릴 수 있고, 내리면 느려지는 대신 한도에 덜 걸립니다.")
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
