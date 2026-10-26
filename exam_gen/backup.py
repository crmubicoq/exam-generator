"""자동 백업 — 로컬 설치형이라 PC 문제로 데이터가 사라지지 않게 프로젝트를 ZIP으로 보관한다(담당자 협의 2026-10-23).

언제: 출제 배치가 끝날 때, 원고를 새 판으로 교체하기 전, 프로젝트를 삭제하기 전, 복원하기 전.
어디: 백업/<프로젝트 폴더>/YYYYMMDD_HHMMSS_<사유>.zip (exports 출력물은 빼고 문항 DB·원고·설정 포함)
프로젝트당 최근 KEEP개만 남긴다. 백업 폴더는 git과 데이터 이전 ZIP에서 제외한다.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from .config import PROJECTS_DIR

BACKUP_DIR = PROJECTS_DIR.parent / "백업"  # 프로그램 폴더/백업 (테스트는 임시 폴더)
KEEP = 10


def backup(project, reason: str = "자동") -> Path | None:
    """프로젝트를 ZIP으로 보관한다. 실패해도 작업은 계속되도록 None을 반환한다."""
    try:
        d = BACKUP_DIR / project.slug
        d.mkdir(parents=True, exist_ok=True)
        tag = re.sub(r"[^\w가-힣]+", "_", reason).strip("_")[:20] or "자동"
        out = d / f"{datetime.now():%Y%m%d_%H%M%S}_{tag}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(project.dir.rglob("*")):
                rel = f.relative_to(project.dir)
                if f.is_file() and rel.parts[0] != "exports" and not f.name.endswith(".tmp"):
                    z.write(f, rel.as_posix())
        for old in sorted(d.glob("*.zip"))[:-KEEP]:
            old.unlink(missing_ok=True)
        return out
    except Exception:  # noqa: BLE001 — 백업 실패가 출제·교체를 막지 않게
        return None


def list_backups(slug: str) -> list[dict]:
    d = BACKUP_DIR / slug
    out = []
    for f in sorted(d.glob("*.zip"), reverse=True) if d.exists() else []:
        m = re.match(r"(\d{8})_(\d{6})_(.*)\.zip$", f.name)
        when = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}" if m else ""
        out.append({"path": str(f), "name": f.name, "at": when, "reason": m.group(3) if m else "",
                    "mb": round(f.stat().st_size / 1e6, 1)})
    return out


def restore(project, zip_path: str) -> None:
    """백업 시점으로 되돌린다. 되돌리기 전 현재 상태를 한 번 더 백업한다(되돌리기를 되돌릴 수 있게)."""
    backup(project, "복원 전")
    keep_exports = project.dir / "exports"
    for f in list(project.dir.iterdir()):
        if f == keep_exports:
            continue
        shutil.rmtree(f) if f.is_dir() else f.unlink()
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(project.dir)
    project._pages_cache = None
    project.log("backup_restore", {"zip": Path(zip_path).name})
