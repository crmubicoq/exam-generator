"""Claude 구독 계정 — 대시보드에서 로그인 계정 확인·로그아웃·다른 계정으로 로그인(사용자 요청 2026-11-05).

패키지에 들어 있는 Claude 프로그램(claude_agent_sdk/_bundled/claude.exe)의 명령을 쓴다.
- 확인: `claude auth status --json` → 로그인 여부, 이메일, 요금제(subscriptionType), 조직 이름
- 로그아웃: `claude auth logout`
- 로그인: 새 창에서 `claude auth logout` → `claude auth login --claudeai`(브라우저가 열리면 계정으로 승인)
계정 정보(이메일)는 화면에만 보여 주고 출제 기록·로그에는 남기지 않는다. AI 호출이 아니라 사용량이 들지 않는다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLAN_KO = {"max": "Max", "pro": "Pro", "team": "Team", "enterprise": "Enterprise", "free": "무료"}


def claude_exe() -> Path | None:
    from .tools.check_env import bundled_claude
    return bundled_claude()


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    exe = claude_exe()
    if not exe:
        raise RuntimeError("Claude 프로그램을 찾지 못했습니다. 설치 폴더가 맞는지 확인하세요.")
    return subprocess.run([str(exe), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def parse_status(text: str) -> dict:
    """`auth status --json` 출력 → {"logged_in", "email", "plan", "org", "method"}."""
    try:
        d = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return {"logged_in": False, "email": "", "plan": "", "org": "", "method": "", "error": text.strip()[:200]}
    sub = str(d.get("subscriptionType") or "").lower()
    return {"logged_in": bool(d.get("loggedIn")), "email": d.get("email") or "",
            "plan": PLAN_KO.get(sub, sub), "org": d.get("orgName") or "", "method": d.get("authMethod") or ""}


def status() -> dict:
    try:
        r = _run(["auth", "status", "--json"])
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return {"logged_in": False, "email": "", "plan": "", "org": "", "method": "", "error": str(e)}
    return parse_status((r.stdout or "") + (r.stderr or ""))


def logout() -> bool:
    try:
        return _run(["auth", "logout"]).returncode == 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False


def login_script(exe: Path) -> str:
    """새 콘솔 창에서 실행할 배치 내용: 지금 계정 로그아웃 → 구독 계정으로 로그인 → 확인 안내."""
    q = f'"{exe}"'
    return "\r\n".join([
        "@echo off", "chcp 65001 > nul", "title Claude 계정 로그인", "echo.",
        "echo  브라우저가 열리면 사용할 Claude 계정으로 로그인하고 승인하세요.",
        "echo  브라우저가 열리지 않으면 아래에 나오는 주소를 복사해 브라우저에 붙여 넣으세요.", "echo.",
        f"call {q} auth logout > nul 2>&1",
        f"call {q} auth login --claudeai",
        "echo.", "echo  끝났으면 이 창을 닫고, 대시보드에서 [계정 다시 확인]을 누르세요.", "pause", ""])


def open_login_window() -> bool:
    """새 창을 띄워 로그인 절차를 시작한다(대시보드는 기다리지 않는다). 윈도우가 아니면 False."""
    exe = claude_exe()
    if not exe or sys.platform != "win32":
        return False
    import tempfile
    bat = Path(tempfile.gettempdir()) / "exam_gen_claude_login.bat"
    bat.write_text(login_script(exe), encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return True
