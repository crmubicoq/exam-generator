"""원고 파일을 PDF로 변환한다. 근거 표시를 PDF 한 경로로 처리하기 위해 모든 형식을 PDF로 통일한다.

순서: Windows Office/한컴 COM → LibreOffice(soffice) → 실패 시 안내 메시지.
PPT는 슬라이드 1장 = PDF 1페이지이므로 슬라이드 번호가 그대로 쪽수가 된다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SUPPORTED = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".hwp", ".hwpx"}


class ConvertError(RuntimeError):
    pass


def to_pdf(src: Path, out_pdf: Path) -> Path:
    src, out_pdf = Path(src).resolve(), Path(out_pdf).resolve()
    ext = src.suffix.lower()
    if ext not in SUPPORTED:
        raise ConvertError(f"지원하지 않는 형식입니다: {ext}")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".pdf":
        shutil.copyfile(src, out_pdf)
        return out_pdf

    errors = []
    com = {".pptx": _powerpoint, ".ppt": _powerpoint, ".docx": _word, ".doc": _word,
           ".hwp": _hwp_isolated, ".hwpx": _hwp_isolated}[ext]
    for fn in (com, _libreoffice):
        try:
            fn(src, out_pdf)
            if out_pdf.exists() and out_pdf.stat().st_size > 0:
                return out_pdf
        except Exception as e:  # noqa: BLE001 — 변환기마다 예외 형태가 다르다
            errors.append(f"{fn.__name__}: {e}")
    hint = " 한글 프로그램이 없으면 HWP에서 'PDF로 저장'한 파일을 올려 주세요." if ext in (".hwp", ".hwpx") else ""
    raise ConvertError("PDF 변환에 실패했습니다." + hint + " / " + " | ".join(errors))


def _powerpoint(src: Path, out_pdf: Path):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("PowerPoint.Application")
    try:
        pres = app.Presentations.Open(str(src), ReadOnly=True, Untitled=False, WithWindow=False)
        try:
            pres.SaveAs(str(out_pdf), 32)  # ppSaveAsPDF
        finally:
            pres.Close()
    finally:
        app.Quit()


def _word(src: Path, out_pdf: Path):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("Word.Application")
    app.Visible = False
    try:
        doc = app.Documents.Open(str(src), ReadOnly=True)
        try:
            doc.ExportAsFixedFormat(str(out_pdf), 17)  # wdExportFormatPDF
        finally:
            doc.Close(False)
    finally:
        app.Quit()


HWP_TIMEOUT = 180


def _hwp_isolated(src: Path, out_pdf: Path):
    """한글 자동화는 보안 확인 창이 뜨면 무한 대기하므로 별도 프로세스에서 제한 시간을 두고 실행한다."""
    import sys
    code = ("import sys; from pathlib import Path; from exam_gen.ingest.convert import _hwp; "
            "_hwp(Path(sys.argv[1]), Path(sys.argv[2]))")
    root = str(Path(__file__).resolve().parents[2])
    try:
        r = subprocess.run([sys.executable, "-c", code, str(src), str(out_pdf)], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=HWP_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise ConvertError(f"한글 변환이 {HWP_TIMEOUT}초 안에 끝나지 않았다. 한글 보안 확인 창(파일 접근 허용)이 "
                           "떠 있을 수 있다 — 한글에서 'PDF로 저장'한 파일을 올려 주세요.") from e
    if r.returncode != 0:
        raise ConvertError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "한글 변환 실패")


def _hwp(src: Path, out_pdf: Path):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    hwp = win32com.client.DispatchEx("HWPFrame.HwpObject")
    try:
        try:
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception:  # noqa: BLE001 — 보안 모듈이 없으면 확인 창이 뜰 수 있음
            pass
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:  # noqa: BLE001 — 창 제어가 안 되는 버전도 변환은 가능
            pass
        fmt = "HWPX" if src.suffix.lower() == ".hwpx" else "HWP"
        if not hwp.Open(str(src), fmt, "forceopen:true"):
            raise ConvertError("한글에서 원고를 열지 못했다")
        if not hwp.SaveAs(str(out_pdf), "PDF", ""):
            raise ConvertError("한글에서 PDF로 저장하지 못했다")
    finally:
        hwp.Quit()


def _libreoffice(src: Path, out_pdf: Path):
    exe = shutil.which("soffice") or next(
        (p for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                     r"C:\Program Files (x86)\LibreOffice\program\soffice.exe") if Path(p).exists()), None)
    if not exe:
        raise ConvertError("LibreOffice 없음")
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(out_pdf.parent), str(src)],
                   check=True, timeout=300, capture_output=True)
    produced = out_pdf.parent / (src.stem + ".pdf")
    if produced != out_pdf:
        produced.replace(out_pdf)
