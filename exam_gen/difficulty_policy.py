"""난이도 기준 — 담당자 지침(또는 담당자가 확인한 기준안)을 출제·AI 검수에 쓴다.

- 기본값은 criteria.yaml의 difficulty_rubric(블룸 인지 수준에 맞춘 기준안: 하=기억, 중=이해, 상=적용, 최상=분석·종합).
- **공통 기준**: data/difficulty_policy.json — 따로 정하지 않은 모든 프로젝트가 쓴다.
- **프로젝트 기준**(담당자 요청 2026-11-03: 과정마다 난이도가 다를 수 있음): projects/<프로젝트>/difficulty_policy.json.
  이 파일이 있으면 그 프로젝트는 공통 기준 대신 이것을 쓴다. 지우면 다시 공통 기준을 따른다.
- status: "임시"(담당자 확인 전) / "확정"(담당자가 확인함). scope: "공통" / "프로젝트". 화면·출력물에 표시한다.
- 문항마다 출제 당시 기준 버전(gen.difficulty_policy)을 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR, criteria
from .store import _atomic_write, now

PATH = DATA_DIR / "difficulty_policy.json"
FILENAME = "difficulty_policy.json"
LEVELS = ("최상", "상", "중", "하")


def default_levels() -> dict[str, str]:
    return {lv: " ".join(str(criteria()["difficulty_rubric"][lv]).split()) for lv in LEVELS}


def _project_path(project) -> Path | None:
    return Path(project.dir) / FILENAME if project is not None else None


def _read(path: Path) -> dict | None:
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
        if all(p.get("levels", {}).get(lv) for lv in LEVELS):
            return p
    except (OSError, ValueError):
        pass
    return None


def has_project_policy(project) -> bool:
    path = _project_path(project)
    return bool(path and _read(path))


def load_common() -> dict:
    p = _read(PATH)
    if p:
        return {**p, "scope": "공통"}
    return {"levels": default_levels(), "status": "임시", "version": "기본안", "source": "개발 기준안(블룸 인지 수준)",
            "confirmed_by": "", "confirmed_at": "", "note": "", "history": [], "scope": "공통"}


def load(project=None) -> dict:
    """project의 기준(프로젝트 기준이 있으면 그것, 없으면 공통 기준). project가 없으면 공통 기준."""
    path = _project_path(project)
    p = _read(path) if path else None
    if p:
        return {**p, "scope": "프로젝트"}
    return load_common()


def rubric(project=None) -> dict[str, str]:
    return load(project)["levels"]


def version(project=None) -> str:
    p = load(project)
    return f"{'프로젝트' if p['scope'] == '프로젝트' else '공통'}·{p['status']}·{p['version']}"


def label(project=None) -> str:
    """화면·엑셀 표시용: '이 프로젝트 기준 · 담당자 확정' 등."""
    p = load(project)
    scope = "이 프로젝트 전용 기준" if p["scope"] == "프로젝트" else "공통 기준"
    return f"{scope} · " + ("담당자 확정" if p["status"] == "확정" else "임시(담당자 확인 전)")


def save(levels: dict[str, str], status: str, by: str = "", source: str = "", note: str = "", project=None) -> dict:
    """project를 주면 그 프로젝트 전용 기준으로, 없으면 공통 기준으로 저장한다."""
    path = _project_path(project) or PATH
    old = _read(path) or (load_common() if project is not None else load_common())
    ts = now()
    new = {"levels": {lv: levels[lv].strip() for lv in LEVELS}, "status": status, "version": ts[:16].replace("T", " "),
           "source": source or old.get("source", ""), "confirmed_by": by if status == "확정" else "",
           "confirmed_at": ts if status == "확정" else "", "note": note,
           "history": ((_read(path) or {}).get("history") or [])[-19:] + [{"at": ts, "by": by, "status": status,
                                                                         "source": source, "note": note}]}
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(new, ensure_ascii=False, indent=1))
    return new


def reset(by: str = "") -> dict:
    """공통 기준을 개발 기준안으로 되돌린다."""
    return save(default_levels(), "임시", by, "개발 기준안(블룸 인지 수준)으로 되돌림")


def use_common(project) -> bool:
    """프로젝트 전용 기준을 지우고 공통 기준을 따르게 한다(지운 내용은 같은 폴더에 .bak으로 남긴다)."""
    path = _project_path(project)
    if not path or not path.exists():
        return False
    bak = path.with_suffix(".json.bak")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    return True
