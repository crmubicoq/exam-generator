"""G2 근거 매칭 — 문항의 근거 인용문을 원고에서 찾아 페이지·줄·bbox를 확정한다.

LLM이 적은 쪽수는 '힌트'로만 쓰고, 실제 출처 쪽수는 여기서 찾은 위치로 정한다.
(tsbookmaker 산출물에서 책 쪽수와 PDF 순번이 섞였던 문제를 구조적으로 막는다.)

매칭 단계
  L2: 정규화한 인용문 전체가 페이지 텍스트에 그대로 있음
  L1: 8자 이상 조각들이 원문 순서대로 인용문의 80% 이상을 덮고, 어긋난 틈(gap)이 '의미 변화 없는' 차이일 때
      (Codex C3) 틈에 숫자·수치 기호, 부정·양태·반대말 표현이 있으면 L1로 인정하지 않는다.
      L1 문항은 승인 시 검수자의 근거 확인이 필요하다(integrity.needs_evidence_confirm).
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
    """인용문을 앞에서부터 '원고에 있는 가장 긴 조각'으로 덮는다. 직전 조각 뒤쪽 위치를 우선 찾는다.
    반환: [(q_pos, c_pos, length)]"""
    out, i, after = [], 0, 0
    while i < len(nq):
        if out:
            # 직전 조각 바로 뒤에서 1~2글자만 어긋난 경우(조사 하나 차이 등)는 짧은 조각도 이어 붙인다
            anchored = _anchored(nq, cum, i, after)
            if anchored:
                out.append(anchored)
                after = anchored[1] + anchored[2]
                i = anchored[0] + anchored[2]
                continue
        lo, hi, best = MIN_CHUNK, len(nq) - i, None
        if hi < MIN_CHUNK:
            break
        if cum.find(nq[i:i + MIN_CHUNK]) < 0:
            i += 1
            continue
        while lo <= hi:  # 가장 긴 일치 길이 이분 탐색
            mid = (lo + hi) // 2
            sub = nq[i:i + mid]
            pos = cum.find(sub, after)
            if pos < 0:
                pos = cum.find(sub)
            if pos >= 0:
                best = (i, pos, mid)
                lo = mid + 1
            else:
                hi = mid - 1
        if not out:
            # 첫 조각 앞의 짧은 구간(8자 미만)도 1~2글자 어긋남까지 거꾸로 이어 맞춘다
            back = []
            q0, c0 = best[0], best[1]
            while q0 > 0:
                b = _anchored_back(nq, cum, q0, c0)
                if not b:
                    break
                back.insert(0, b)
                q0, c0 = b[0], b[1]
            out.extend(back)
        out.append(best)
        after = best[1] + best[2]
        i += best[2]
    return out


def _anchored_back(nq: str, cum: str, q_end: int, c_end: int, min_len: int = 2):
    """인용문 q_end, 원문 c_end 앞쪽에서 0~2글자를 건너뛴 뒤 거꾸로 이어지는 가장 긴 공통 접미 조각."""
    best = None
    for dq in range(0, 3):
        for dc in range(0, 3):
            if dq == dc == 0:
                continue
            qi, ci = q_end - dq, c_end - dc
            n = 0
            while qi - n > 0 and ci - n > 0 and nq[qi - n - 1] == cum[ci - n - 1]:
                n += 1
            if n >= min_len and (best is None or n > best[2]):
                best = (qi - n, ci - n, n)
    return best


def _anchored(nq: str, cum: str, i: int, after: int, min_len: int = 3):
    """인용문 i, 원문 after 위치에서 각각 0~2글자를 건너뛴 뒤 이어지는 가장 긴 공통 접두 조각."""
    best = None
    for dq in range(0, 3):
        for dc in range(0, 3):
            if dq == dc == 0:
                continue
            qi, ci = i + dq, after + dc
            n = 0
            while qi + n < len(nq) and ci + n < len(cum) and nq[qi + n] == cum[ci + n]:
                n += 1
            if n >= min_len and (best is None or n > best[2]):
                best = (qi, ci, n)
    return best


def _ordered_chain(chunks: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """원문에서도 같은 순서로 이어지는 조각 사슬 중 가장 많이 덮는 것 (순서 뒤바뀐 인용 차단)."""
    n = len(chunks)
    best_len = [c[2] for c in chunks]
    prev = [-1] * n
    for j in range(n):
        for i in range(j):
            if chunks[i][1] + chunks[i][2] <= chunks[j][1] and best_len[i] + chunks[j][2] > best_len[j]:
                best_len[j] = best_len[i] + chunks[j][2]
                prev[j] = i
    if not n:
        return []
    k = max(range(n), key=lambda x: best_len[x])
    chain = []
    while k >= 0:
        chain.append(chunks[k])
        k = prev[k]
    return chain[::-1]


# 틈(gap)에 있으면 의미가 바뀔 수 있는 표현 — 정규화된(공백 없는) 텍스트 기준
_RISKY = ("않", "못", "없", "아니", "불가", "금지", "말아", "말고",                     # 부정
          "해야", "하여야", "할수", "수있", "가능", "권장", "권고", "반드시", "필수", "의무",  # 양태
          "높", "낮", "증가", "감소", "많", "적", "크", "작", "늘", "줄", "강화", "약화", "확대", "축소",
          "이상", "이하", "미만", "초과", "최대", "최소", "이전", "이후", "상승", "하락", "우선")  # 반대말·범위
_NUMERIC = re.compile(r"[0-9.%~\-]")


def _gap_problem(q_gap: str, s_gap: str, edge: bool = False) -> str | None:
    """인용문 쪽 틈(q_gap)과 원문 쪽 틈(s_gap)이 의미를 바꿀 수 있으면 사유를 반환."""
    for g in (q_gap, s_gap):
        if _NUMERIC.search(g):
            return f"수치가 원문과 다름('{q_gap}'↔'{s_gap}')"
        hit = next((r for r in _RISKY if r in g), None)
        if hit:
            return f"의미가 바뀔 수 있는 차이('{q_gap}'↔'{s_gap}')"
    if q_gap and s_gap and (len(q_gap) > 2 or len(s_gap) > 2):
        return f"원문과 다른 표현('{q_gap}'↔'{s_gap}')"
    if q_gap and not s_gap and len(q_gap) > 3:
        return f"원문에 없는 문구('{q_gap}')"
    if s_gap and not q_gap and not edge and len(s_gap) > 20:
        return "원문 중간의 긴 부분을 생략"
    return None


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
        chain = _ordered_chain(_greedy_cover(nq, cum))
        if not chain:
            continue
        cov = sum(c[2] for c in chain) / len(nq)
        problem = None
        q_end, c_end = 0, None
        for k, (qp, cp, ln) in enumerate(chain):
            q_gap = nq[q_end:qp]
            s_gap = cum[c_end:cp] if c_end is not None else ""
            problem = problem or _gap_problem(q_gap, s_gap, edge=(k == 0))
            q_end, c_end = qp + ln, cp + ln
        problem = problem or _gap_problem(nq[q_end:], "", edge=True)
        if problem:
            continue
        nos = sorted({n for _, p, ln in chain for n in owner[p:p + ln]})
        cand = {"level": "L1", "coverage": round(cov, 3), "lines": nos}
        if best is None or cov > best["coverage"]:
            best = cand
    if best and best["coverage"] >= criteria()["thresholds"]["evidence_min_coverage"]:
        return best
    return None


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


_NUM = re.compile(r"(?<![\d.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])-?\d+(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """수치 토큰: 1,234,567 → 1234567 / 1.5 / -100 (Codex C16). 한글 수사('이십 년')는 다루지 않는다."""
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
