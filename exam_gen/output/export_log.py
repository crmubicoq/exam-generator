"""출력 이력 — 엑셀·근거PDF·시험지를 만들 때마다 '그때 몇 번으로 어떤 내용이 나갔는지'를 남긴다(이의신청 대응).

runs/exports.jsonl 한 줄 = 출력 1회: 일시, 종류, 파일, 출력 대상·범위, 문항번호 ↔ 문항키 ↔ 내용 해시, 원고 판,
출력 당시 문항 내용 스냅숏. 나중에 문항을 고쳐도 해시를 비교해 '출력 당시와 같은지'를 알 수 있다.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..integrity import content_hash
from ..store import _LOCK, now

KIND_KO = {"xlsx": "평가문항 엑셀", "extract": "문항별 근거PDF", "overview": "원고 전체 근거PDF", "paper": "시험지 PDF"}
SNAP_FIELDS = ("type", "difficulty", "stem", "statements", "choices", "answer", "short_variants", "explanation")


def _path(project) -> Path:
    return project.dir / "runs" / "exports.jsonl"


def snapshot(it: dict) -> dict:
    return {k: it.get(k) for k in SNAP_FIELDS}


def record(project, kind: str, path, numbered: list[dict], *, scope: str | None = None, batch: int | None = None,
           chasi: int | None = None) -> dict:
    """출력 1회를 기록한다. numbered는 번호(no)가 붙은 출력 문항 목록."""
    pages = project.pages
    entry = {"at": now(), "kind": kind, "file": Path(path).name, "path": str(path), "scope": scope or "approved",
             "batch": batch, "chasi": chasi, "n": len(numbered),
             "manuscript_version": max((s.get("version", 1) for s in project.meta.get("sources", [])), default=1),
             "items": [{"no": it.get("no"), "key": it["key"], "hash": content_hash(it),
                        "page": pages[it["page"]]["label"] if it.get("page") is not None and it["page"] < len(pages) else "",
                        "mv": it.get("manuscript_version", 1), "status": it.get("status"), "snap": snapshot(it)}
                       for it in numbered]}
    f = _path(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def history(project) -> list[dict]:
    """출력 이력(최근 순). 각 항목에 순번 id(1부터, 오래된 순)를 붙인다."""
    f = _path(project)
    if not f.exists():
        return []
    out = []
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        try:
            out.append({**json.loads(line), "id": i})
        except ValueError:
            continue
    return out[::-1]


def item_exports(project, key: str, current_hash: str | None = None) -> list[dict]:
    """이 문항이 나간 출력 목록: 언제, 어느 파일에, 몇 번으로, 지금 내용과 같은지."""
    out = []
    for e in history(project):
        for r in e["items"]:
            if r["key"] == key:
                out.append({"id": e["id"], "at": e["at"], "kind": e["kind"], "file": e["file"], "path": e.get("path"),
                            "scope": e["scope"], "no": r["no"], "hash": r["hash"], "snap": r.get("snap"),
                            "same": current_hash is None or r["hash"] == current_hash})
    return out
