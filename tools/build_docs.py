"""배포 문서 만들기 — docs/의 소개서·사용 매뉴얼 HTML을 Word(.docx)와 PDF로 바꾼다(개발 PC에서 실행).

  python tools/build_docs.py            # → dist/배포문서/ 에 .html .docx .pdf

- 원본은 HTML 하나(docs/평가문항_출제도구_소개서.html, docs/평가문항_출제도구_사용매뉴얼.html)다. 내용을 고칠 때는 HTML만 고친다.
- Word: 설치된 Microsoft Word로 HTML을 열어 .docx로 저장한다(pywin32 COM).
- PDF: Microsoft Edge(없으면 Chrome)의 헤드리스 인쇄로 만든다(브라우저에서 보는 모양 그대로, A4).
- 패치노트: 원본은 docs/패치노트_v{버전}.md(지금 버전, exam_gen/version.py). 같은 모양의 HTML로 바꿔 함께 넣는다(담당부서 전달용).
- dist/는 git에 올리지 않는다. 문서에는 원고 문장을 넣지 않는다.
"""
from __future__ import annotations

import html as html_lib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["평가문항_출제도구_소개서", "평가문항_출제도구_사용매뉴얼"]
OUT = ROOT / "dist" / "배포문서"
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def to_pdf(html: Path, pdf: Path) -> None:
    exe = next((b for b in BROWSERS if Path(b).exists()), None)
    if not exe:
        raise RuntimeError("Edge·Chrome을 찾지 못했습니다.")
    pdf.unlink(missing_ok=True)
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", html.as_uri()], check=True, capture_output=True, timeout=120)
    if not pdf.exists():
        raise RuntimeError(f"PDF가 만들어지지 않았습니다: {pdf.name}")


def to_docx(html: Path, docx: Path) -> None:
    import win32com.client  # pywin32
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Open(str(html), ReadOnly=True, ConfirmConversions=False)
        try:
            doc.ActiveWindow.View.Type = 3  # 인쇄 모양(웹 모양으로 저장되지 않게)
            ps = doc.PageSetup
            ps.PaperSize = 7  # A4
            for side in ("TopMargin", "BottomMargin", "LeftMargin", "RightMargin"):
                setattr(ps, side, 51)  # 약 18mm (pt)
            docx.unlink(missing_ok=True)
            doc.SaveAs2(str(docx), FileFormat=16)  # wdFormatDocumentDefault(.docx)
        finally:
            doc.Close(False)
    finally:
        word.Quit()


def _inline(s: str) -> str:
    """굵게(**…**)·코드(`…`)만 HTML로 — 패치노트에 쓰는 표기."""
    s = html_lib.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def md_to_html(md: str) -> tuple[str, str]:
    """패치노트 MD(제목 #·##·###, 목록 -·1., 두 칸 들여쓴 하위 목록, 문단) → (제목, 본문 HTML)."""
    out, stack, title = [], [], ""  # stack: 열린 목록 [(태그, 들여쓰기)]

    def close_to(indent):
        while stack and stack[-1][1] >= indent:
            out.append(f"</li></{stack.pop()[0]}>")

    for raw in md.splitlines():
        line = raw.rstrip()
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            indent, tag = len(m.group(1)), ("ol" if m.group(2)[0].isdigit() else "ul")
            if stack and stack[-1][1] > indent:
                close_to(indent + 1)
            if stack and stack[-1][1] == indent:
                if stack[-1][0] != tag:
                    close_to(indent)
                    out.append(f"<{tag}><li>")
                else:
                    out.append("</li><li>")
            else:
                out.append(f"<{tag}><li>")
                stack.append((tag, indent))
            out.append(_inline(m.group(3)))
            continue
        close_to(0)
        if not line:
            continue
        h = re.match(r"^(#{1,3})\s+(.*)$", line)
        if h:
            n = len(h.group(1))
            title = title or h.group(2)
            out.append(f"<h{n}>{_inline(h.group(2))}</h{n}>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    close_to(0)
    return title, "\n".join(out)


def build_patch_note() -> Path | None:
    """docs/패치노트_v{버전}.md → 배포문서 HTML(매뉴얼과 같은 모양). 담당부서는 MD보다 HTML이 보기 편하다(2026-11-16)."""
    sys.path.insert(0, str(ROOT))
    from exam_gen.version import APP_VERSION, RELEASE_DATE
    src = ROOT / "docs" / f"패치노트_v{APP_VERSION}.md"
    if not src.exists():
        return None
    title, body = md_to_html(src.read_text(encoding="utf-8"))
    css = re.search(r"<style>.*?</style>", (ROOT / "docs" / f"{DOCS[1]}.html").read_text(encoding="utf-8"), re.S).group(0)
    page = (f'<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{html_lib.escape(title)}</title>\n{css}\n'
            f'<style>h1{{margin-bottom:10px}} ul ul,ol ul{{margin:2px 0}}</style>\n</head>\n<body>\n<div class="wrap">\n'
            f'<p class="kicker">업데이트 안내 · v{APP_VERSION} · {RELEASE_DATE}</p>\n{body}\n'
            f'<footer>평가문항 자동 출제 도구 v{APP_VERSION} 업데이트 안내. 자세한 사용법은 사용 매뉴얼을 봅니다.</footer>\n'
            f'</div>\n</body>\n</html>\n')
    dst = OUT / f"패치노트_v{APP_VERSION}.html"
    dst.write_text(page, encoding="utf-8")
    return dst


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    note = build_patch_note()
    if note:
        print(f"{note.stem}: html")
    for name in DOCS:
        src = ROOT / "docs" / f"{name}.html"
        html = OUT / f"{name}.html"
        shutil.copyfile(src, html)
        to_pdf(html, OUT / f"{name}.pdf")
        to_docx(html, OUT / f"{name}.docx")
        print(f"{name}: html · pdf · docx")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
