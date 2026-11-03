"""다른 PC로 옮기기 — 원고와 출제 결과(코드 저장소에 올리지 않는 데이터)를 ZIP 하나로 묶고 푼다.

  python -m exam_gen.tools.transfer export [저장 폴더]     # 기본: 바탕화면
  python -m exam_gen.tools.transfer import [ZIP 경로]      # 기본: 바탕화면·프로그램 폴더에서 가장 최근 ZIP

포함: 원고/, projects/, data/ui_state.json(마지막 작업 프로젝트), data/settings.json(공급자 선택 — 비밀값 없음),
      data/difficulty_policy.json(공통 난이도 기준; 프로젝트 전용 기준은 projects/ 안에 있음)
제외: API 키(Windows 자격 증명 저장소에 있음), Claude·Codex 로그인 — 새 PC에서 다시 로그인한다.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "출제도구_이전데이터_"
INCLUDE_DIRS = ["원고", "projects"]
INCLUDE_FILES = ["data/ui_state.json", "data/settings.json", "data/difficulty_policy.json"]


def _desktop() -> Path:
    d = Path.home() / "Desktop"
    return d if d.exists() else ROOT


def export(dest: Path | None = None) -> Path:
    dest = Path(dest) if dest else _desktop()
    out = dest / f"{PREFIX}{datetime.now():%Y%m%d_%H%M}.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in INCLUDE_DIRS:
            for f in sorted((ROOT / d).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(ROOT).as_posix())
                    n += 1
        for rel in INCLUDE_FILES:
            if (ROOT / rel).exists():
                z.write(ROOT / rel, rel)
                n += 1
    print(f"파일 {n}개를 묶었습니다: {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    print("이 ZIP을 USB 등으로 새 PC의 바탕화면이나 프로그램 폴더에 옮긴 뒤 '데이터_가져오기.bat'을 실행하세요.")
    print("주의: 원고가 들어 있으므로 외부 공유 드라이브·메일로 보내지 마세요.")
    return out


def _find_zip() -> Path | None:
    cands = [p for d in (_desktop(), ROOT, ROOT.parent) for p in d.glob(f"{PREFIX}*.zip")]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def import_(src: Path | None = None) -> int:
    src = Path(src) if src else _find_zip()
    if not src or not src.exists():
        print(f"가져올 ZIP을 찾지 못했습니다. '{PREFIX}…zip' 파일을 바탕화면이나 프로그램 폴더에 두세요.")
        return 1
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts or
               not (n.split("/")[0] in INCLUDE_DIRS or n in INCLUDE_FILES)]
        if bad:
            print(f"예상하지 못한 경로가 있어 중단합니다: {bad[:3]}")
            return 1
        # 이미 있는 데이터는 지우지 않고 옆으로 옮겨 둔다
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        for d in INCLUDE_DIRS:
            if (ROOT / d).exists() and any((ROOT / d).iterdir()) and any(n.startswith(d + "/") for n in names):
                backup = ROOT / f"{d}_이전백업_{stamp}"
                shutil.move(str(ROOT / d), str(backup))
                print(f"기존 {d}/ 폴더는 {backup.name}/ 로 옮겨 두었습니다.")
        z.extractall(ROOT)
    projects = sorted(p.name for p in (ROOT / "projects").glob("*") if (p / "project.json").exists())
    print(f"가져오기 완료: {src.name} → 프로젝트 {len(projects)}개")
    for p in projects:
        print("  -", p)
    return 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in ("export", "import"):
        print(__doc__)
        return 2
    arg = Path(argv[1]) if len(argv) > 1 else None
    if argv[0] == "export":
        export(arg)
        return 0
    return import_(arg)


if __name__ == "__main__":
    sys.exit(main())
