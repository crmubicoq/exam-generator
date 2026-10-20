"""인쇄된 책 쪽수 ↔ PDF 페이지 인덱스 매핑 (Phase 0).

머리말·꼬리말 줄 앞뒤의 숫자를 후보로 모아, `쪽수 = 인덱스 + offset`에 가장 많이 맞는 offset을 고른다.
교재 권 번호("금융솔루션 3")처럼 쪽수가 아닌 숫자는 다수결에서 자연히 걸러진다.
"""
from __future__ import annotations

import re
from collections import Counter

_LEAD = re.compile(r"^\s*(\d{1,4})(?!\d)")
_TRAIL = re.compile(r"(?<!\d)(\d{1,4})\s*$")


def page_number_candidates(lines: list[dict]) -> set[int]:
    nums = set()
    for l in lines:
        if not l["margin"]:
            continue
        t = l["text"].strip()
        for rx in (_LEAD, _TRAIL):
            m = rx.search(t)
            if m:
                nums.add(int(m.group(1)))
    return nums


def detect_offset(pages_lines: list[list[dict]]) -> dict:
    """반환: {offset, confidence, matched, total, exceptions{idx: 감지된 숫자}}"""
    votes = Counter()
    cands = [page_number_candidates(lines) for lines in pages_lines]
    for idx, nums in enumerate(cands):
        for n in nums:
            votes[n - idx] += 1
    total = len(pages_lines)
    if not votes:
        return {"offset": 1, "confidence": 0.0, "matched": 0, "total": total, "exceptions": {}}
    offset, matched = votes.most_common(1)[0]
    exceptions = {}
    for idx, nums in enumerate(cands):
        if nums and (idx + offset) not in nums and len(nums) == 1:
            exceptions[idx] = next(iter(nums))
    return {"offset": offset, "confidence": matched / total if total else 0.0,
            "matched": matched, "total": total, "exceptions": exceptions}


def format_pages(labels: list[str]) -> str:
    """['17','18','19','22'] -> '17~19, 22'. 숫자가 아닌 라벨은 그대로 쉼표로 잇는다."""
    out, run = [], []

    def flush():
        if not run:
            return
        out.append(str(run[0]) if len(run) == 1 else f"{run[0]}~{run[-1]}")
        run.clear()

    seen = []
    for lb in labels:
        if lb not in seen:
            seen.append(lb)
    nums = [s for s in seen if str(s).isdigit()]
    others = [s for s in seen if not str(s).isdigit()]
    for n in sorted(set(int(x) for x in nums)):
        if run and n == run[-1] + 1:
            run.append(n)
        else:
            flush()
            run.append(n)
    flush()
    return ", ".join(out + others)
