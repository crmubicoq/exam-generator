"""G2 근거 매칭 — 문항의 근거 인용문을 원고에서 찾아 페이지·줄·bbox를 확정한다.

LLM이 적은 쪽수는 '힌트'로만 쓰고, 실제 출처 쪽수는 여기서 찾은 위치로 정한다.
(tsbookmaker 산출물에서 책 쪽수와 PDF 순번이 섞였던 문제를 구조적으로 막는다.)

매칭 단계
  L2: 정규화한 인용문 전체가 페이지 텍스트에 그대로 있음
  L1: 8자 이상 조각들로 인용문의 80% 이상이 한곳에 모여 있음 (OCR 오차·줄바꿈·띄어쓰기 차이 흡수)
  실패: 그 외 → 문항 탈락
"""
from __future__ import annotations

import re

from ..config import criteria
from ..ingest.pdf_text import normalize

MIN_QUOTE = 6
MIN_CHUNK = 8


def _orders(lines: list[dict]) -> list[list[dict]]:
    body = [l for l in lines if not l["margin"]]
    reading = sorted(body, key=lambda l: l["no"])
    block = sorted(body, key=lambda l: (l["blk"], l["ln"]))
    return [reading] if [l["no"] for l in reading] == [l["no"] for l in block] else [reading, block]


def _cum(order: list[dict]) -> tuple[str, list[int]]:
    s, owner = [], []
    for l in order:
        n = normalize(l["text"])
        s.append(n)
        owner.extend([l["no"]] * len(n))
    return "".join(s), owner


def _greedy_cover(nq: str, cum: str) -> list[tuple[int, int, int]]:
    """인용문을 앞에서부터 '원고에 있는 가장 긴 조각'으로 덮는다. 반환: [(q_pos, c_pos, length)]"""
    out, i = [], 0
    while i < len(nq):
        lo, hi, best = MIN_CHUNK, len(nq) - i, None
        if hi < MIN_CHUNK:
            break
        if cum.find(nq[i:i + MIN_CHUNK]) < 0:
            i += 1
            continue
        while lo <= hi:  # 가장 긴 일치 길이 이분 탐색
            mid = (lo + hi) // 2
            pos = cum.find(nq[i:i + mid])
            if pos >= 0:
                best = (i, pos, mid)
                lo = mid + 1
            else:
                hi = mid - 1
        out.append(best)
        i += best[2]
    return out


def match_on_page(lines: list[dict], quote: str) -> dict | None:
    nq = normalize(quote)
    if len(nq) < MIN_QUOTE:
        return None
    best = None
    for order in _orders(lines):
        cum, owner = _cum(order)
        if not cum:
            continue
        pos = cum.find(nq)
        if pos >= 0:
            nos = sorted(set(owner[pos:pos + len(nq)]))
            return {"level": "L2", "coverage": 1.0, "lines": nos}
        chunks = _greedy_cover(nq, cum)
        if not chunks:
            continue
        covered = sum(c[2] for c in chunks)
        start = min(c[1] for c in chunks)
        end = max(c[1] + c[2] for c in chunks)
        if end - start > len(nq) * 2 + 40:  # 페이지 여기저기 흩어진 조각은 근거로 인정하지 않음
            chunks = _largest_cluster(chunks, len(nq))
            covered = sum(c[2] for c in chunks)
        cov = covered / len(nq)
        nos = sorted({n for _, p, ln in chunks for n in owner[p:p + ln]})
        cand = {"level": "L1", "coverage": round(cov, 3), "lines": nos}
        if best is None or cov > best["coverage"]:
            best = cand
    if best and best["coverage"] >= criteria()["thresholds"]["evidence_min_coverage"]:
        return best
    return None


def _largest_cluster(chunks, qlen):
    chunks = sorted(chunks, key=lambda c: c[1])
    best, cur = [], []
    for c in chunks:
        if cur and c[1] - (cur[0][1]) > qlen * 2 + 40:
            if sum(x[2] for x in cur) > sum(x[2] for x in best):
                best = cur
            cur = []
        cur.append(c)
    if sum(x[2] for x in cur) > sum(x[2] for x in best):
        best = cur
    return best


def resolve_hint(pages: list[dict], label: str | int | None, near_g: int | None) -> int | None:
    """LLM이 적은 쪽수 라벨('p.17', '17')을 페이지 인덱스로. 같은 라벨이 여럿이면 near_g에 가까운 것."""
    if label is None:
        return near_g
    m = re.search(r"\d+", str(label))
    if not m:
        return near_g
    cands = [p["g"] for p in pages if p["label"] == m.group()]
    if not cands:
        return near_g
    return min(cands, key=lambda g: abs(g - (near_g or 0)))


def locate(pages: list[dict], quote: str, hint_g: int | None) -> dict | None:
    """힌트 페이지 → ±1 → 원고 전체 순으로 찾는다. L2를 찾으면 즉시 반환."""
    order = []
    if hint_g is not None:
        order += [hint_g, hint_g - 1, hint_g + 1]
    order += [p["g"] for p in pages]
    seen, best = set(), None
    for g in order:
        if g in seen or g < 0 or g >= len(pages):
            continue
        seen.add(g)
        near = hint_g is None or abs(g - hint_g) <= 1
        m = match_on_page(pages[g]["lines"], quote) if near else _exact_only(pages[g]["lines"], quote)
        if m:
            m["g"] = g
            m["label"] = pages[g]["label"]
            by_no = {l["no"]: l for l in pages[g]["lines"]}
            m["bboxes"] = [by_no[n]["bbox"] for n in m["lines"]]
            if m["level"] == "L2":
                return m
            if best is None or m["coverage"] > best["coverage"]:
                best = m
    return best


def _exact_only(lines, quote):
    m = match_on_page(lines, quote)
    return m if m and m["level"] == "L2" else None


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def check_item(pages: list[dict], item: dict) -> tuple[list[str], list[str]]:
    """문항의 모든 근거를 매칭하고 결과를 item['evidence'][i]['match']에 기록한다.

    탈락 조건
      - 참인 진술(정답 근거)인데 원고에서 찾지 못함
      - 문항의 정답 대상(answer target)에 근거가 없거나 찾지 못함
      - 참인 진술의 숫자가 근거 인용문에 없음 (수치 환각)
      - 단답형 정답어가 근거 인용문에 없음
    """
    fails, warns = [], []
    slot_g = item.get("page")
    evs = item.get("evidence") or []
    by_target = {}
    for ev in evs:
        q = (ev.get("quote") or "").strip()
        ev["match"] = None
        if q:
            hint = resolve_hint(pages, ev.get("page_label"), slot_g)
            ev["match"] = locate(pages, q, hint)
        by_target.setdefault(str(ev.get("target")), []).append(ev)

    for target in required_targets(item):
        lst = by_target.get(target, [])
        if not lst or not any(e.get("quote") for e in lst):
            fails.append(f"G2: [{target}] 근거 인용문이 없음")
        elif not any(e["match"] for e in lst):
            fails.append(f"G2: [{target}] 근거 인용문을 원고에서 찾지 못함 — \"{lst[0]['quote'][:40]}\"")

    for ev in evs:
        if ev.get("quote") and not ev["match"]:
            if str(ev.get("target")) not in required_targets(item):
                warns.append(f"G2: [{ev.get('target')}] 보조 근거를 원고에서 찾지 못함")
        if ev["match"] and ev.get("is_true"):
            stmt = statement_text(item, str(ev.get("target")))
            # 선다형 보기는 발문에 이미 나온 수치를 빼고 본다. 진위형은 진술이 곧 발문이므로 빼지 않는다.
            in_stem = numbers_in(item.get("stem", "")) if item["type"] == "mcq" else set()
            missing = numbers_in(stmt) - numbers_in(ev["quote"]) - in_stem
            if missing:
                fails.append(f"G2: [{ev.get('target')}] 참 진술의 수치 {sorted(missing)}가 근거에 없음")

    if item["type"] == "short":
        ans = normalize(item.get("answer", ""))
        quotes = normalize(" ".join(e.get("quote", "") for e in by_target.get("A", [])))
        if ans and ans not in quotes:
            fails.append("G2: 단답형 정답어가 근거 인용문에 없음")

    matched = [e["match"] for e in evs if e.get("match")]
    item["source_pages"] = sorted({m["g"] for m in matched})
    item["evidence_level"] = ("L2" if matched and all(m["level"] == "L2" for m in matched)
                              else "L1" if matched else None)
    return fails, warns


def required_targets(item: dict) -> list[str]:
    t = item["type"]
    if t == "ox":
        return ["OX"]
    if t == "short":
        return ["A"]
    if item.get("statements"):
        return [s["label"] for s in item["statements"] if s.get("is_true")]
    req = [str(i + 1) for i, c in enumerate(item.get("choices", [])) if c.get("is_true")]
    ans = str(item.get("answer"))
    if ans not in req:
        req.append(ans)  # 옳지 않은 것 고르기: 정답(거짓 진술)이 바꾼 원문도 반드시 제시
    return req


def statement_text(item: dict, target: str) -> str:
    if item["type"] == "ox":
        return item.get("stem", "")
    if item["type"] == "short":
        return item.get("answer", "")
    for s in item.get("statements") or []:
        if s["label"] == target:
            return s["text"]
    if target.isdigit() and int(target) <= len(item.get("choices", [])):
        return item["choices"][int(target) - 1]["text"]
    return ""
