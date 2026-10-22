"""G4 독립 풀이 — 정답을 숨긴 문항을 원고 발췌만으로 풀게 하고 출제 정답과 대조한다.

탈락: 정답 불일치 / 보기별 참·거짓 판정 불일치 / 정답이 하나로 정해지지 않음 / 원고로 풀 수 없음
"""
from __future__ import annotations

import re

from ..ingest.pdf_text import normalize
from ..llm.prompts import render
from .schema import SOLVE_SCHEMA


def present(item: dict, idx: str, with_key: bool = False) -> str:
    t = item["type"]
    lines = [f"[{idx}] 유형: " + {"mcq": "선다형", "ox": "진위형", "short": "단답형"}[t]
             + (" (조합형)" if item.get("statements") else "")]
    lines.append(f"발문: {item.get('stem', '')}")
    for s in item.get("statements") or []:
        lines.append(f"  {s['label']}. {s['text']}" + (f"  ← {'참' if s.get('is_true') else '거짓'}" if with_key else ""))
    for i, c in enumerate(item.get("choices") or [], 1):
        mark = ""
        if with_key and not item.get("statements"):
            mark = f"  ← {'참' if c.get('is_true') else '거짓'}"
        lines.append(f"  {i}) {c['text']}{mark}")
    if t == "ox":
        lines.append("  (O 또는 X)")
    if with_key:
        lines.append(f"정답: {item.get('answer')}" + (f" (허용: {', '.join(item['short_variants'])})"
                                                     if item.get("short_variants") else ""))
        lines.append(f"해설: {item.get('explanation', '')}")
        lines.append(f"표시 난이도: {item.get('difficulty')}")
        from .schema import false_targets
        ft = false_targets(item)
        lines.append("함정 판정 대상(traps): " + (", ".join(ft) if ft else "없음"))
        for ev in item.get("evidence") or []:
            if ev.get("quote"):
                lines.append(f"  근거[{ev.get('target')}] {'참' if ev.get('is_true') else '바꾼 원문'}: \"{ev['quote']}\""
                             + (f" / 함정: {ev['trap_note']}" if ev.get("trap_note") else ""))
    return "\n".join(lines)


def excerpt(project, items: list[dict]) -> str:
    """출제 페이지·근거 페이지와 그 앞뒤 페이지(같은 원고 파일)를 함께 준다 — 페이지를 넘어가는 문장 대비."""
    pages = project.pages
    gs = set()
    for it in items:
        for g in {it.get("page"), *(it.get("source_pages") or [])} - {None}:
            gs |= {x for x in (g - 1, g, g + 1) if 0 <= x < len(pages) and pages[x]["src"] == pages[g]["src"]}
    return "\n\n".join(f"[p.{pages[g]['label']}]\n{project.page_text(g)}" for g in sorted(gs))


def norm_id(v) -> str:
    """응답 ID 표기 차이('[1]', 'Q1', '문항 1')를 흡수한다."""
    m = re.search(r"\d+", str(v or ""))
    return m.group() if m else ""


def ask_until_complete(call, items: list[dict], ids: list[str], tries: int = 2) -> dict:
    """검증기 응답에서 빠진 문항만 다시 묻는다. 검증기 쪽 누락이 문항의 재출제 기회를 소모하지 않게 한다."""
    res: dict[str, dict] = {}
    for _ in range(tries):
        sub = [(it, i) for it, i in zip(items, ids) if i not in res]
        if not sub:
            break
        out = call([s[0] for s in sub], [s[1] for s in sub]) or {}
        for r in out.get("results") or []:
            rid = norm_id(r.get("id"))
            if rid in ids and rid not in res:
                res[rid] = {**r, "id": rid}
    return res


def solve(client, settings, project, items: list[dict]) -> dict:
    """반환: {item_key_or_idx: (fails, warns, raw)}"""
    ids = [str(i + 1) for i in range(len(items))]

    def call(sub_items, sub_ids):
        user = "# 원고 발췌\n" + excerpt(project, sub_items) + "\n\n# 문항 (정답 비공개)\n" + \
            "\n\n".join(present(it, i) for it, i in zip(sub_items, sub_ids))
        return client.structured(render("solve_system.j2"), user, SOLVE_SCHEMA, model=settings.model_solve,
                                 task="solve", context={"items": sub_items, "project": project, "ids": sub_ids})

    res = ask_until_complete(call, items, ids)
    verdicts = {}
    for it, i in zip(items, ids):
        r = res.get(i)
        verdicts[i] = compare(it, r) + (r,)
    return verdicts


def compare(item: dict, r: dict | None) -> tuple[list[str], list[str]]:
    if r is None:
        return ["G4: 독립 풀이 결과가 없음"], []
    fails, warns = [], []
    t = item["type"]
    key = str(item.get("answer", "")).strip()
    got = str(r.get("answer", "")).strip()
    if t == "short":
        ok = {normalize(key)} | {normalize(v) for v in item.get("short_variants") or []}
        if normalize(got) not in ok:
            fails.append(f"G4: 독립 풀이 답 '{got}' ≠ 출제 정답 '{key}' — 다른 답이 성립할 수 있음")
    elif got.upper().strip("()①②③④ ") != key.upper() and not _circled(got, key):
        fails.append(f"G4: 독립 풀이 정답 {got} ≠ 출제 정답 {key}")
    if not got:
        fails.append("G4: 독립 풀이 답이 비어 있음")
    truth = {}
    if item.get("statements"):
        truth = {s["label"]: bool(s.get("is_true")) for s in item["statements"]}
    elif t == "mcq":
        truth = {str(i + 1): bool(c.get("is_true")) for i, c in enumerate(item.get("choices") or [])}
    seen = {}
    for j in r.get("judgments") or []:
        tg = str(j.get("target", "")).strip("() .")
        tg = {"①": "1", "②": "2", "③": "3", "④": "4"}.get(tg, tg)
        if tg in seen:
            fails.append(f"G4: [{tg}] 판정이 중복됨")
        seen[tg] = j
        if truth and tg not in truth:
            fails.append(f"G4: 알 수 없는 판정 대상 [{tg}]")
        elif tg in truth and bool(j.get("is_true")) != truth[tg]:
            fails.append(f"G4: [{tg}] 독립 판정 {'참' if j.get('is_true') else '거짓'} ≠ 출제 의도 "
                         f"{'참' if truth[tg] else '거짓'} — {j.get('reason', '')[:80]}")
    # Codex C11: 선다형·조합형은 보기(진술)마다 판정이 있어야 한다 — 정답 번호만 맞힌 응답은 통과시키지 않는다
    missing = [tg for tg in truth if tg not in seen]
    if missing:
        fails.append(f"G4: 보기별 판정 누락 {missing}")
    if not isinstance(r.get("unique"), bool) or not isinstance(r.get("solvable"), bool):
        fails.append("G4: unique/solvable 판정 누락")
    if r.get("unique") is False:
        fails.append(f"G4: 정답이 하나로 정해지지 않음 — {r.get('note', '')[:100]}")
    if r.get("solvable") is False:
        fails.append(f"G4: 원고만으로 풀 수 없음 — {r.get('note', '')[:100]}")
    elif r.get("note") and not fails:
        warns.append(f"G4 메모: {r['note'][:120]}")
    return fails, warns


def _circled(got: str, key: str) -> bool:
    m = {"①": "1", "②": "2", "③": "3", "④": "4"}
    return m.get(got[:1]) == key
