# 06. 근거 PDF 구현 자산 (원예 OCR PDF에서 실전 검증된 코드)

라이브러리: PyMuPDF(`fitz`). 아래 코드는 2026-05-20 원예기능사 최종 근거PDF(30문항, 112 라벨, 298줄 하이라이트)를 만든 로직을 정리한 것.
리포에서는 `src/etest_agent/precheck/` · `validate/evidence.py` · `output/evidence_pdf.py`로 나눠 넣으면 된다.

## 1. 표준 상수
```python
COLOR_CORRECT = (1.0, 0.95, 0.0)        # 정답 근거 노랑
COLOR_WRONG   = (1.0, 0.6, 0.2)         # 함정 원문 주황
LABEL_COLOR_CORRECT = (0.6, 0.5, 0.0)
LABEL_COLOR_WRONG   = (0.9, 0.45, 0.0)
BG_COLOR_CORRECT = (1.0, 0.97, 0.85)
BG_COLOR_WRONG   = (1.0, 0.92, 0.85)
OPACITY = 0.4
KOREAN_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"  # Windows 로컬이면 경로 변경 필요(맑은고딕 등) → config로 뺄 것
```

## 2. OCR 감지
```python
def detect_ocr(doc, sample_pages=(20, 50, 100)):
    import re
    hits = 0
    for i in sample_pages:
        if i >= len(doc): continue
        t = doc[i].get_text()
        if re.search(r'[가-힣]\s[가-힣]\s[가-힣]', t) or re.search(r'\d\s\d\s\d', t) or 'ᄋ' in t:
            hits += 1
    return hits > 0
```

## 3. 페이지 매핑 확인 (footer 시각 확인용 이미지)
```python
def render_footer(doc, idx, out_png):
    page = doc[idx]
    clip = fitz.Rect(0, page.rect.height * 0.92, page.rect.width, page.rect.height)
    page.get_pixmap(dpi=200, clip=clip).save(out_png)
```
- 원예 실측: footer 숫자 = PDF 0-based index (offset 0). 그런데 **출처를 쓸 때 일부 문항이 +1로 잘못 기록**되어 혼재처럼 보였음 → 매칭 시 ±1 fallback 필요

## 4. 줄 추출
```python
def extract_lines_in_order(page):
    d = page.get_text("dict"); h = page.rect.height; lines = []
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            text = "".join(s["text"] for s in ln["spans"])
            if not text.strip(): continue
            y = ln["bbox"][1]
            if y < h * 0.05 or y > h * 0.92: continue   # 머리말·꼬리말 제외
            lines.append({"text": text, "y": y, "x": ln["bbox"][0], "bbox": ln["bbox"]})
    lines.sort(key=lambda l: (round(l["y"]), l["x"]))
    for i, l in enumerate(lines, 1): l["line_no"] = i
    return lines
```

## 5. 정규화 (OCR 대응)
```python
def normalize(text):
    text = re.sub(r'[₀₁₂₃₄₅₆₇₈₉]', lambda m: str('₀₁₂₃₄₅₆₇₈₉'.index(m.group())), text)
    text = re.sub(r'[ᄋᄀ]', 'O', text)          # "CO₂"가 "Cᄋ2"로 OCR된 사례
    return re.sub(r'[\s,·\.\-\(\)\[\]"\'/、，→]', '', text).lower()
```

## 6. 정밀 줄 범위 매칭 (핵심)
```python
def find_precise_line_range(page_lines, search_text):
    if len(search_text) < 5: return None
    ns = normalize(search_text)
    cum, starts, nos = "", [], []
    for l in page_lines:
        starts.append(len(cum)); nos.append(l["line_no"]); cum += normalize(l["text"])
    for k in (10, 8, 6, 5):                       # 마커 길이 점감 fallback
        if len(ns) < k: continue
        pos = cum.find(ns[:k])
        if pos >= 0: break
    else:
        return None
    end = pos + len(ns); s_ln = e_ln = None
    for i, st in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else len(cum)
        if s_ln is None and st <= pos < nxt: s_ln = nos[i]
        if st <= end <= nxt: e_ln = nos[i]; break
    if s_ln is None: return None
    if e_ln is None: e_ln = nos[-1]
    if e_ln - s_ln > 10: e_ln = s_ln + 4          # 과도한 범위 방지
    return (s_ln, e_ln)
```
- 인용문은 `/`, `。`, 개행으로 쪼개고, 60자 초과 조각은 `:`·`,`로 다시 쪼갠 뒤(5자 이상만) 각각 매칭
- 페이지는 출처 페이지의 offset −1, 0 두 곳 모두 시도
- 같은 페이지에서 2줄 이내로 겹치는 범위는 병합

## 7. 하이라이트 + 좌측 라벨 박스
```python
def draw_segment(page, line_lookup, seg, q_no, used_y):
    correct = seg["type"] == "correct"
    color = COLOR_CORRECT if correct else COLOR_WRONG
    tc = LABEL_COLOR_CORRECT if correct else LABEL_COLOR_WRONG
    bg = BG_COLOR_CORRECT if correct else BG_COLOR_WRONG
    for ln in range(seg["start"], seg["end"] + 1):
        if ln in line_lookup:
            a = page.add_highlight_annot(fitz.Rect(line_lookup[ln]["bbox"]))
            a.set_colors(stroke=color); a.set_opacity(OPACITY); a.update()
    if seg["start"] in line_lookup:
        b = line_lookup[seg["start"]]["bbox"]
        x = max(15, b[0] - 65); y = b[1] + (b[3] - b[1]) / 2
        while any(abs(y - u) < 16 for u in used_y): y += 18     # 라벨 겹침 방지
        used_y.append(y)
        r = fitz.Rect(x, y - 8, x + 55, y + 8)
        page.draw_rect(r, color=tc, fill=bg, width=0.7)
        page.insert_textbox(r, f"Q{q_no}-{seg['label']}", fontname="NotoCJK", fontsize=8, color=tc, align=1)
```
- 페이지마다 `page.insert_font(fontname="NotoCJK", fontfile=KOREAN_FONT)` 먼저 호출
- 문서 구성: 표지(색상 범례) → 문항별 [분리자 페이지(문항번호·책 페이지·단원·유형·난이도·이 페이지의 진술 목록) + 원본 페이지]
- 저장: `out.save(path, garbage=4, deflate=True, clean=True)`

## 8. 검증 방법 (반드시 자동 테스트로)
- 문항별 하이라이트 0개인 문항 = 실패로 리포트
- 첫 근거 페이지를 PNG로 렌더링해 눈으로 확인 (노랑/주황/라벨 위치)
- 원예 최종본: 30/30 문항 매핑, 0개 문항 없음
