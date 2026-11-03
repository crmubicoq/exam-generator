"""차시 자동 제안 — 원고 본문의 장·절 제목('제1절', '2장', 'Chapter 3', '4차시' 등)을 찾아 차시 나누기안을 만든다(AI 사용 안 함).

- 제목 줄의 조건: 머리말·꼬리말이 아닌 본문 줄, 60자 이하, 단위 뒤에 조사가 붙지 않음('제1절을 마치며'는 제외),
  그리고 글자가 본문보다 크거나(줄 높이 1.2배 이상) 페이지의 첫 줄.
- 번호가 1, 2, 3…처럼 차례로 이어지는 첫 등장만 시작점으로 본다(본문 속 '제3절에서 다룬다' 같은 참조·부록의 반복 제외).
- 단위 우선순위: 차시 → 장(Chapter·Lesson·Unit) → 부·편 → 절 → 과. 시작점이 2개 이상 나온 첫 단위를 쓴다.
- 첫 제목 앞쪽(표지·머리말·목차)은 첫 차시에 붙인다. 확정은 사람이 원고 등록 ③에서 한다.
"""
from __future__ import annotations

import re
import statistics

_B = r"(?=\s|[.:·—\-–)\]]|$)"  # 단위 뒤: 공백·문장부호·줄 끝(조사 '을·의·에서'가 붙으면 본문 참조로 본다)
LEVELS = [
    ("차시", re.compile(rf"^\s*(?:제\s*)?(\d{{1,2}})\s*차시{_B}")),
    ("장", re.compile(rf"^\s*(?:제\s*(\d{{1,2}})\s*장{_B}|(\d{{1,2}})\s*장{_B}|(?:chapter|lesson|unit)\s*(\d{{1,2}}){_B})", re.I)),
    ("부·편", re.compile(rf"^\s*제\s*(\d{{1,2}})\s*[부편]{_B}")),
    ("절", re.compile(rf"^\s*제\s*(\d{{1,2}})\s*절{_B}")),
    ("과", re.compile(rf"^\s*제\s*(\d{{1,2}})\s*과{_B}")),
]


def _num(m: re.Match) -> int:
    return int(next(g for g in m.groups() if g))


def _height(l: dict) -> float:
    return l["bbox"][3] - l["bbox"][1]


def detect(pages: list[dict]) -> dict | None:
    """pages: 한 원고 파일의 페이지(순서대로, lines 포함). 반환: {"unit": "절", "heads": [{"g", "num", "title"}...]} 또는 None."""
    body = [l for p in pages for l in p["lines"] if not l.get("margin")]
    if not body:
        return None
    med = statistics.median(_height(l) for l in body)
    for unit, pat in LEVELS:
        heads, expect = [], None
        for p in pages:
            content = [l for l in p["lines"] if not l.get("margin")]
            for i, l in enumerate(content):
                t = l["text"].strip()
                if len(t) > 60 or not (m := pat.match(t)):
                    continue
                if not (_height(l) >= med * 1.2 or i == 0):
                    continue
                n = _num(m)
                if expect is None or n == expect:
                    heads.append({"g": p["g"], "num": n, "title": t})
                    expect = n + 1
                    break  # 한 페이지에 시작점 하나
        if len(heads) >= 2:
            return {"unit": unit, "heads": heads}
    return None


def suggest(project) -> list[dict]:
    """원고 파일마다 제목을 찾아 전체 차시 나누기안을 만든다(파일 순서대로 이어서 번호).
    반환: [{"chasi", "src", "start", "end", "title", "found"}] — g 범위는 start~end(포함)."""
    pages = project.pages
    out, ch = [], 0
    for s in project.meta["sources"]:
        sp = [p for p in pages if p["src"] == s["id"]]
        if not sp:
            continue
        d = detect(sp)
        if not d:  # 제목을 못 찾으면 이 파일 전체를 한 차시로
            ch += 1
            out.append({"chasi": ch, "src": s["id"], "start": sp[0]["g"], "end": sp[-1]["g"],
                        "title": s["filename"], "found": False, "unit": None})
            continue
        starts = [h["g"] for h in d["heads"]]
        starts[0] = sp[0]["g"]  # 첫 제목 앞쪽(표지·머리말·목차)은 첫 차시에 붙인다
        for i, h in enumerate(d["heads"]):
            ch += 1
            end = starts[i + 1] - 1 if i + 1 < len(starts) else sp[-1]["g"]
            out.append({"chasi": ch, "src": s["id"], "start": starts[i], "end": end,
                        "title": h["title"], "found": True, "unit": d["unit"]})
    return out


def apply(project, plan: list[dict], only_src: str | None = None) -> int:
    """나누기안을 페이지에 적용하고, 이미 출제된 문항의 차시도 맞춘다. 반환: 차시가 바뀐 페이지 수."""
    pages = project.pages
    changed = 0
    for r in plan:
        if only_src and r["src"] != only_src:
            continue
        for g in range(r["start"], r["end"] + 1):
            if pages[g]["chasi"] != r["chasi"]:
                pages[g]["chasi"] = r["chasi"]
                changed += 1
    project.save_pages(pages)
    sync_item_chasi(project)
    return changed


def sync_item_chasi(project) -> int:
    """문항의 차시를 그 문항 페이지의 차시에 맞춘다(엑셀 A열 '차시'가 페이지 설정과 어긋나지 않게)."""
    pages = project.pages
    n = 0
    for it in project.items():
        g = it.get("page")
        if g is None or not (0 <= g < len(pages)):
            continue
        if it.get("chasi") != pages[g]["chasi"]:
            it["chasi"] = pages[g]["chasi"]
            project.save_item(it)
            n += 1
    return n


def apply_new_source(project, sid: str) -> dict | None:
    """새로 올린 원고 파일 하나에 제목 기준 차시를 미리 채운다(앞 파일들의 차시 다음 번호부터).
    반환: detect 결과(못 찾으면 None — 기본값인 파일 하나 = 한 차시 그대로)."""
    pages = project.pages
    sp = [p for p in pages if p["src"] == sid]
    d = detect(sp) if sp else None
    if not d:
        return None
    base = max((p["chasi"] for p in pages if p["src"] != sid), default=0)
    starts = [h["g"] for h in d["heads"]]
    starts[0] = sp[0]["g"]
    for p in sp:
        i = max(k for k, s in enumerate(starts) if s <= p["g"])
        p["chasi"] = base + i + 1
    project.save_pages(pages)
    return d
