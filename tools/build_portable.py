"""휴대용 패키지 만들기 — Python을 따로 설치하지 않아도 되는 ZIP을 만든다(개발 PC에서 실행).

    python tools/build_portable.py            # 전체 패키지(내장 Python + 패키지 + 프로그램)
    python tools/build_portable.py --lock     # requirements.lock.txt만 다시 만들기
    python tools/build_portable.py --app-only # 프로그램 파일만(업데이트용, 내장 Python 재사용)

결과(dist/, git 제외)
- 출제도구_휴대용_YYYYMMDD.zip   : 풀고 run.bat 실행. 인터넷 없이도 실행된다(AI 호출에는 인터넷 필요).
- 출제도구_프로그램만_YYYYMMDD.zip: 이미 설치한 폴더에 덮어쓰기(python 폴더·projects·백업은 그대로).

순서
1. python.org의 Windows embeddable Python(이 PC와 같은 3.x.y)을 받는다(dist/_cache에 보관).
2. python3xx._pth에 'import site'와 프로그램 폴더('..')를 넣는다.
3. requirements.lock.txt(이 PC에서 검증된 버전)를 python/Lib/site-packages에 미리 설치한다.
4. git archive HEAD(커밋된 프로그램 파일만 — 원고·projects는 들어가지 않는다)와 합쳐 ZIP으로 묶는다.
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import date
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CACHE = DIST / "_cache"
LOCK = ROOT / "requirements.lock.txt"
PY = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
EMBED_URL = f"https://www.python.org/ftp/python/{PY}/python-{PY}-embed-amd64.zip"


def make_lock() -> list[str]:
    """requirements.txt와 그 의존 패키지를 이 PC에 설치된 버전으로 고정한다."""
    env = default_environment()
    seen: dict[str, str] = {}
    todo = [Requirement(line.split("#")[0].strip()) for line in (ROOT / "requirements.txt").read_text().splitlines()
            if line.split("#")[0].strip()]
    while todo:
        req = todo.pop()
        if req.marker and not req.marker.evaluate({**env, "extra": ""}):
            continue
        name = req.name.lower().replace("_", "-")
        if name in seen:
            continue
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            raise SystemExit(f"설치되지 않은 패키지: {req.name} — pip install -r requirements.txt 후 다시 실행하세요.")
        seen[name] = dist.version
        for r in dist.requires or []:
            sub = Requirement(r)
            if sub.marker and not sub.marker.evaluate({**env, "extra": ""}):
                continue
            todo.append(sub)
    lines = [f"{n}=={v}" for n, v in sorted(seen.items())]
    LOCK.write_text("# tools/build_portable.py --lock 로 만든 검증 버전 고정본 (Python " + PY + ")\n"
                    + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"requirements.lock.txt: {len(lines)}개 패키지")
    return lines


def _embed_zip() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / Path(EMBED_URL).name
    if not f.exists():
        print(f"내장 Python 받는 중: {EMBED_URL}")
        urllib.request.urlretrieve(EMBED_URL, f)
    return f


def _git_files(dest: Path):
    """커밋된 프로그램 파일만 꺼낸다(작업 중인 변경·원고·projects는 들어가지 않는다)."""
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("[주의] 커밋하지 않은 변경이 있습니다. 패키지에는 마지막 커밋 내용만 들어갑니다.")
    data = subprocess.run(["git", "archive", "--format=zip", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(dest)


def _zip(src: Path, out: Path):
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, Path(src.name) / f.relative_to(src))
    print(f"만듦: {out} ({out.stat().st_size / 1e6:.0f} MB)")


def build_python(stage: Path):
    py = stage / "python"
    with zipfile.ZipFile(_embed_zip()) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    pth.write_text("\n".join(lines + ["Lib\\site-packages", "..", "import site"]) + "\n")
    if not LOCK.exists():
        make_lock()
    print("패키지 설치 중(수 분)…")
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                    "--target", str(py / "Lib" / "site-packages"), "-r", str(LOCK)], check=True)
    # 내장 Python이 실제로 프로그램을 읽는지 확인
    r = subprocess.run([str(py / "python.exe"), "-c", "import streamlit, pymupdf, claude_agent_sdk, exam_gen.store; print('ok')"],
                       cwd=stage, capture_output=True, text=True)
    if "ok" not in r.stdout:
        raise SystemExit(f"내장 Python 점검 실패:\n{r.stdout}\n{r.stderr}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true", help="requirements.lock.txt만 다시 만든다")
    ap.add_argument("--app-only", action="store_true", help="프로그램 파일만 묶는다(업데이트용)")
    a = ap.parse_args(argv)
    if a.lock:
        make_lock()
        return
    if sys.platform != "win32":
        raise SystemExit("Windows에서 실행하세요.")
    stamp = date.today().strftime("%Y%m%d")
    DIST.mkdir(exist_ok=True)
    stage = DIST / "출제도구"  # 풀었을 때 경로가 짧도록(윈도우 경로 길이 제한)
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    _git_files(stage)
    if a.app_only:
        _zip(stage, DIST / f"출제도구_프로그램만_{stamp}.zip")
    else:
        build_python(stage)
        _zip(stage, DIST / f"출제도구_휴대용_{stamp}.zip")
    print(f"풀어 둔 폴더(시험용): {stage}")


if __name__ == "__main__":
    main()
