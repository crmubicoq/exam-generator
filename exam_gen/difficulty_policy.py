"""난이도 기준 — 담당자 지침(또는 담당자가 확인한 기준안)을 출제·AI 검수에 쓴다.

- 기본값은 criteria.yaml의 difficulty_rubric(블룸 인지 수준에 맞춘 기준안: 하=기억, 중=이해, 상=적용, 최상=분석·종합).
- 담당자가 '난이도 기준' 화면에서 고치면 data/difficulty_policy.json에 저장되고, 이후 출제·AI 재검증부터 쓰인다.
- status: "임시"(담당자 확인 전) / "확정"(담당자가 확인함). 화면·출력물에 표시한다.
- 문항마다 출제 당시 기준 버전(gen.difficulty_policy)을 남긴다.
"""
from __future__ import annotations

import json

from .config import DATA_DIR, criteria
from .store import _atomic_write, now

PATH = DATA_DIR / "difficulty_policy.json"
LEVELS = ("최상", "상", "중", "하")


def default_levels() -> dict[str, str]:
    return {lv: " ".join(str(criteria()["difficulty_rubric"][lv]).split()) for lv in LEVELS}


def load() -> dict:
    try:
        p = json.loads(PATH.read_text(encoding="utf-8"))
        if all(p.get("levels", {}).get(lv) for lv in LEVELS):
            return p
    except (OSError, ValueError):
        pass
    return {"levels": default_levels(), "status": "임시", "version": "기본안", "source": "개발 기준안(블룸 인지 수준)",
            "confirmed_by": "", "confirmed_at": "", "note": "", "history": []}


def rubric() -> dict[str, str]:
    return load()["levels"]


def version() -> str:
    p = load()
    return f"{p['status']}·{p['version']}"


def save(levels: dict[str, str], status: str, by: str = "", source: str = "", note: str = "") -> dict:
    old = load()
    ts = now()
    new = {"levels": {lv: levels[lv].strip() for lv in LEVELS}, "status": status, "version": ts[:16].replace("T", " "),
           "source": source or old.get("source", ""), "confirmed_by": by if status == "확정" else "",
           "confirmed_at": ts if status == "확정" else "", "note": note,
           "history": (old.get("history") or [])[-19:] + [{"at": ts, "by": by, "status": status,
                                                            "source": source, "note": note}]}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(PATH, json.dumps(new, ensure_ascii=False, indent=1))
    return new


def reset(by: str = "") -> dict:
    return save(default_levels(), "임시", by, "개발 기준안(블룸 인지 수준)으로 되돌림")
