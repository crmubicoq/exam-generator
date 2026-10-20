"""PDF 줄 추출과 정규화.

인계패키지 docs/06의 extract_lines_in_order·normalize를 기반으로 한다.
- 머리말·꼬리말은 버리지 않고 margin 플래그로 표시한다 (쪽수 감지에 쓰고, 출제·매칭에서는 제외).
- 표 셀처럼 같은 높이에 여러 줄이 있는 경우를 위해 블록 순서(blk, ln)도 함께 보관한다.
"""
from __future__ import annotations

import re

import pymupdf

HEADER_FRAC = 0.07
FOOTER_FRAC = 0.895

_SUBSCRIPTS = "₀₁₂₃₄₅₆₇₈₉"
_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_KEEP = re.compile(r"[^0-9a-z가-힣ㄱ-ㅎ]")


def normalize(text: str) -> str:
    """비교용 정규화: 숫자·영문·한글만 남긴다 (OCR 공백, 줄바꿈, 문장부호 차이 흡수)."""
    if not text:
        return ""
    text = re.sub(f"[{_SUBSCRIPTS}]", lambda m: str(_SUBSCRIPTS.index(m.group())), text)
    text = re.sub(f"[{_SUPERSCRIPTS}]", lambda m: str(_SUPERSCRIPTS.index(m.group())), text)
    text = re.sub(r"[ᄋᄀ]", "o", text)  # "CO₂"가 "Cᄋ2"로 OCR된 사례
    return _KEEP.sub("", text.lower())


def extract_lines(page: pymupdf.Page) -> list[dict]:
    """페이지의 텍스트 줄을 읽기 순서(y, x)로 정렬해 번호를 붙인다."""
    h = page.rect.height
    d = page.get_text("dict")
    lines = []
    for bi, b in enumerate(d["blocks"]):
        for li, ln in enumerate(b.get("lines", [])):
            text = "".join(s["text"] for s in ln["spans"])
            if not text.strip():
                continue
            x0, y0, x1, y1 = ln["bbox"]
            lines.append({
                "text": text,
                "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                "blk": bi,
                "ln": li,
                "margin": y0 < h * HEADER_FRAC or y0 > h * FOOTER_FRAC,
            })
    lines.sort(key=lambda l: (round(l["bbox"][1]), l["bbox"][0]))
    for i, l in enumerate(lines, 1):
        l["no"] = i
    return lines


def content_text(lines: list[dict]) -> str:
    return "\n".join(l["text"] for l in lines if not l["margin"])


def detect_ocr(doc: pymupdf.Document, sample_pages=(0, 5, 20, 50)) -> bool:
    """docs/06 detect_ocr: 글자 사이 공백이 끼는 OCR 특유의 패턴이 보이면 True."""
    hits = 0
    for i in sample_pages:
        if i >= len(doc):
            continue
        t = doc[i].get_text()
        spaced = len(re.findall(r"(?:[가-힣] ){4,}[가-힣]", t))
        if spaced >= 5 or "ᄋ" in t:
            hits += 1
    return hits > 0
