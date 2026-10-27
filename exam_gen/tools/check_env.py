"""설치 점검 — setup.bat이 설치 뒤에 실행한다. 무엇이 준비됐고 무엇을 더 해야 하는지 한글로 알려 준다.

  python -m exam_gen.tools.check_env          # 기본 점검(LLM 호출 없음)
  python -m exam_gen.tools.check_env --login  # Claude 로그인까지 실제 호출로 확인(구독 사용량 아주 조금 사용)
  python -m exam_gen.tools.check_env --codex  # Codex 로그인 상태와 고를 수 있는 모델 목록(AI 호출 없음)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = [("streamlit", "대시보드"), ("pymupdf", "PDF 처리"), ("openpyxl", "엑셀"), ("pandas", "표"),
            ("yaml", "설정 파일"), ("jinja2", "프롬프트"), ("altair", "그래프"), ("keyring", "API 키 보관"),
            ("streamlit_pdf_viewer", "검수 화면 PDF 보기"), ("claude_agent_sdk", "Claude 구독 로그인"),
            ("pptx", "PPT 원고"), ("anthropic", "Anthropic API")]


def _line(ok: bool | None, title: str, detail: str = ""):
    mark = {True: "[ 확인 ]", False: "[ 필요 ]", None: "[ 참고 ]"}[ok]
    print(f"  {mark} {title}" + (f" — {detail}" if detail else ""))


def bundled_claude() -> Path | None:
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    exe = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
    return exe if exe.exists() else None


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    todo = 0
    print("\n== 평가문항 자동 출제 도구 설치 점검 ==\n")

    v = sys.version_info
    ok = v >= (3, 10)
    _line(ok, f"Python {v.major}.{v.minor}.{v.micro}", "" if ok else "3.10 이상이 필요합니다(3.12 권장)")
    todo += not ok

    missing = [f"{mod}({what})" for mod, what in PACKAGES if importlib.util.find_spec(mod) is None]
    _line(not missing, "필요한 패키지", "모두 설치됨" if not missing else "설치 안 됨: " + ", ".join(missing))
    todo += bool(missing)

    tmpl = ROOT / "엑셀_출력 형식(양식).xlsx"
    _line(tmpl.exists(), "엑셀 양식 파일", tmpl.name if tmpl.exists() else f"{tmpl.name} 이 프로그램 폴더에 없습니다")
    todo += not tmpl.exists()

    projects = sorted(p.name for p in (ROOT / "projects").glob("*") if (p / "project.json").exists())
    _line(None, "출제 프로젝트", f"{len(projects)}개" + (" — 다른 PC의 결과를 옮기려면 '데이터_가져오기.bat'" if not projects else ""))

    # LLM 로그인
    exe = bundled_claude()
    if exe is None:
        _line(False, "Claude 구독 로그인", "claude_agent_sdk 패키지가 없습니다")
        todo += 1
    elif "--login" in argv:
        from exam_gen.llm.client import login_status
        ok, msg = login_status("claude_agent_sdk")
        _line(ok, "Claude 구독 로그인", "로그인됨" if ok else "로그인 필요 — 'Claude_로그인.bat'을 실행해 /login 하세요")
        todo += not ok
    else:
        cred = Path.home() / ".claude" / ".credentials.json"
        _line(None, "Claude 구독 로그인", "로그인 기록 있음" if cred.exists() else
              "로그인 기록을 찾지 못함 — 실제 출제를 하려면 'Claude_로그인.bat'으로 한 번 로그인하세요")

    from exam_gen.llm.client import codex_executable, login_status
    if not codex_executable():
        _line(None, "Codex 구독 로그인(선택)", "설치 안 됨 — Codex를 쓰지 않으면 필요 없습니다('Codex_로그인.bat'으로 설치·로그인)")
    else:
        ok, msg = login_status("codex_cli")  # 'codex login status' — AI 호출 없음
        _line(ok if "--codex" in argv else None, "Codex 구독 로그인(선택)",
              "로그인됨" if ok else "설치됨, 로그인 필요 — 'Codex_로그인.bat'을 실행하세요")
        if "--codex" in argv:
            todo += not ok
            if ok:
                from exam_gen.llm.settings import codex_models
                ms = codex_models()
                print()
                print("  이 계정에서 고를 수 있는 Codex 모델(대시보드 'LLM 설정'에서 선택):")
                for k, v in ms.items():
                    print(f"    - {v}" if not k else f"    - {k}  ({v})")

    if sys.platform == "win32":
        try:
            import winreg
            winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "HWPFrame.HwpObject")
            hwp = True
        except OSError:
            hwp = False
        _line(None, "한글(HWP) 자동 변환(선택)", "한글 설치됨" if hwp else "한글 없음 — HWP 원고는 PDF로 저장해 올리세요")

    print()
    if todo:
        print(f"  해야 할 일이 {todo}건 있습니다. 위의 [ 필요 ] 항목을 확인하세요.\n")
    else:
        print("  준비 완료. 'run.bat'을 두 번 클릭해 실행하세요.\n")
    return 1 if todo else 0


if __name__ == "__main__":
    sys.exit(main())
