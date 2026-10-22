"""최종 풀 점검 — 같은 판단 원리를 반복해 묻는 문항 묶음을 찾는다(Codex 실제 원고 검수 제안, 2026-10-22).

문장이 달라도 같은 원리를 묻는 문항(예: '상대가 준 번호로는 독립 확인이 안 된다'를 세 문항이 반복)은
G6 중복 검사(단어 유사도)로 잡히지 않는다. 배치가 끝나면 통과 문항 전체의 판단 포인트를 한 번에 보고 묶는다.
탈락시키지 않고 '원리 반복' 경고만 붙인다 — 어느 문항을 남길지는 검수자가 정한다.
"""
from __future__ import annotations

import json

from ..llm.client import LLMClient
from ..llm.prompts import render
from ..store import now

PREFIX = "원리 반복:"
ACCEPTED = ("auto_pass", "warn", "approved")
SCHEMA = {"type": "object", "required": ["groups"], "properties": {"groups": {"type": "array", "items": {
    "type": "object", "required": ["principle", "keys", "note"],
    "properties": {"principle": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}},
                   "note": {"type": "string"}}}}}}


def _lines(items: list[dict]) -> str:
    return "\n".join(f"{it['key']} | {it['chasi']}차시 | {it['type']} | {it.get('difficulty')} | "
                     f"판단 포인트: {it.get('key_point', '')} | 발문: {it.get('stem', '')[:80]}" for it in items)


def run(project, settings, client: LLMClient | None = None) -> dict:
    """통과 문항 전체를 점검해 묶음을 저장하고 해당 문항에 경고를 붙인다. 반환: {"groups", "flagged"}"""
    items = [it for it in project.items() if it["status"] in ACCEPTED]
    if len(items) < 2:
        return {"groups": [], "flagged": 0}
    client = client or LLMClient(settings)
    res = client.structured(render("pool_review_system.j2"), "# 문항 목록\n" + _lines(items), SCHEMA,
                            model=settings.model_for("review"), task="pool", context={"items": items},
                            provider=settings.provider_for("review"))
    valid = {it["key"] for it in items}
    groups = []
    for g in res.get("groups") or []:
        keys = [k for k in dict.fromkeys(str(k).strip() for k in g.get("keys") or []) if k in valid]
        if len(keys) >= 2:
            groups.append({"principle": g.get("principle", "").strip(), "keys": keys, "note": g.get("note", "").strip()})
    by_key = {k: g for g in groups for k in g["keys"]}
    flagged = 0
    for it in items:
        warns = [w for w in it["validation"].get("warnings", []) if not w.startswith(PREFIX)]
        g = by_key.get(it["key"])
        if g:
            others = ", ".join(k for k in g["keys"] if k != it["key"])
            warns.append(f"{PREFIX} '{g['principle']}' — 같은 원리 문항 {others} (하나만 남기거나 묻는 조건을 달리할지 검수자가 판단)")
            flagged += 1
        if warns != it["validation"].get("warnings", []):
            it["validation"]["warnings"] = warns
            project.save_item(it, "pool_review", {"group": g["principle"] if g else None})
    out = {"at": now(), "groups": groups, "flagged": flagged, "items": len(items)}
    (project.dir / "runs").mkdir(exist_ok=True)
    (project.dir / "runs" / "pool_review.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    project.log("pool_review", {"groups": len(groups), "flagged": flagged})
    return out


def last(project) -> dict | None:
    f = project.dir / "runs" / "pool_review.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
