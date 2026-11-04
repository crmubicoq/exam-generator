"""배포 문서 만들기 — docs/의 소개서·사용 매뉴얼 HTML을 Word(.docx)와 PDF로 바꾼다(개발 PC에서 실행).

  python tools/build_docs.py            # → dist/배포문서/ 에 .html .docx .pdf

- 원본은 HTML 하나(docs/평가문항_출제도구_소개서.html, docs/평가문항_출제도구_사용매뉴얼.html)다. 내용을 고칠 때는 HTML만 고친다.
- Word: 설치된 Microsoft Word로 HTML을 열어 .docx로 저장한다(pywin32 COM).
- PDF: Microsoft Edge(없으면 Chrome)의 헤드리스 인쇄로 만든다(브라우저에서 보는 모양 그대로, A4).
- dist/는 git에 올리지 않는다. 문서에는 원고 문장을 넣지 않는다.
"""
from __future__ import annotations

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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
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
