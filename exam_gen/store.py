"""프로젝트 저장소: projects/<slug>/

  project.json      과정 정보, 원고 목록, 쪽수 매핑, 출제 옵션
  sources/          업로드 원본
  pdf/              원본별 변환 PDF
  manuscript.pdf    모든 원고를 이어 붙인 PDF (근거 표시의 기준)
  pages.json        페이지별 줄·bbox·책 쪽수·차시·제외 여부
  items.sqlite      문항(최신본)·배치·이벤트(감사 로그)
  exports/          엑셀·근거PDF 산출물
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pymupdf

from .config import PROJECTS_DIR, criteria
from .ingest import convert, page_map
from .ingest.pdf_text import content_text, detect_ocr, extract_lines

_LOCK = threading.RLock()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = re.sub(r"[^\w가-힣\-]+", "_", name.strip())
    return s.strip("_") or "project"


def list_projects() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in PROJECTS_DIR.iterdir() if (p / "project.json").exists())


class Project:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = PROJECTS_DIR / slug
        if not (self.dir / "project.json").exists():
            raise FileNotFoundError(f"프로젝트가 없습니다: {slug}")
        self._init_db()

    # ---------- 생성 / 메타 ----------
    @classmethod
    def create(cls, name: str) -> "Project":
        slug = slugify(name)
        d = PROJECTS_DIR / slug
        if (d / "project.json").exists():
            raise FileExistsError(f"같은 이름의 프로젝트가 있습니다: {slug}")
        for sub in ("sources", "pdf", "exports", "runs"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "slug": slug, "created": now(), "subject_tag": "", "objectives": "",
                "sources": [], "options": dict(criteria()["defaults"])}
        (d / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(slug)

    @property
    def meta(self) -> dict:
        return json.loads((self.dir / "project.json").read_text(encoding="utf-8"))

    def save_meta(self, meta: dict):
        with _LOCK:
            _atomic_write(self.dir / "project.json", json.dumps(meta, ensure_ascii=False, indent=2))

    def update_meta(self, **kw):
        m = self.meta
        m.update(kw)
        self.save_meta(m)

    # ---------- 원고 ----------
    def add_source(self, src_path: Path, filename: str | None = None) -> dict:
        filename = filename or Path(src_path).name
        meta = self.meta
        sid = f"s{len(meta['sources']) + 1}"
        dst = self.dir / "sources" / f"{sid}_{filename}"
        shutil.copyfile(src_path, dst)
        pdf_path = self.dir / "pdf" / f"{sid}.pdf"
        convert.to_pdf(dst, pdf_path)
        kind = Path(filename).suffix.lower().lstrip(".")
        doc = pymupdf.open(pdf_path)
        lines = [extract_lines(p) for p in doc]
        if kind in ("ppt", "pptx"):
            mapping = {"offset": 1, "confidence": 1.0, "matched": len(doc), "total": len(doc),
                       "exceptions": {}, "mode": "slide"}
        else:
            mapping = page_map.detect_offset(lines)
            mapping["mode"] = "printed" if mapping["confidence"] >= 0.6 else "pdf"
            if mapping["mode"] == "pdf":
                mapping["offset"] = 1
        if kind == "pptx":
            # PowerPoint가 내보낸 PDF는 텍스트 추출 시 띄어쓰기가 사라진다 → LLM에는 슬라이드 원문을 준다(매칭·하이라이트는 PDF)
            _atomic_write(self.dir / "pdf" / f"{sid}_slides.json",
                          json.dumps(_pptx_texts(dst), ensure_ascii=False))
        source = {"id": sid, "filename": filename, "kind": kind, "pdf": f"pdf/{sid}.pdf",
                  "pages": len(doc), "ocr": detect_ocr(doc), "sha256": _sha256(pdf_path),
                  "page_map": mapping, "page_map_confirmed": False,
                  "chasi": len(meta["sources"]) + 1}
        doc.close()
        meta["sources"].append(source)
        self.save_meta(meta)
        self.rebuild_manuscript()
        return source

    def remove_source(self, sid: str):
        meta = self.meta
        meta["sources"] = [s for s in meta["sources"] if s["id"] != sid]
        self.save_meta(meta)
        self.rebuild_manuscript()

    def rebuild_manuscript(self):
        """원고 PDF를 이어 붙이고 pages.json을 다시 만든다. 기존 페이지의 차시·제외 설정은 유지한다."""
        meta = self.meta
        old = {(p["src"], p["idx"]): p for p in self.pages} if (self.dir / "pages.json").exists() else {}
        out = pymupdf.open()
        pages = []
        min_chars = criteria()["thresholds"]["min_page_chars"]
        for s in meta["sources"]:
            doc = pymupdf.open(self.dir / s["pdf"])
            out.insert_pdf(doc)
            alt_path = self.dir / "pdf" / f"{s['id']}_slides.json"
            alt = json.loads(alt_path.read_text(encoding="utf-8")) if alt_path.exists() else []
            for idx, page in enumerate(doc):
                lines = extract_lines(page)
                text = content_text(lines)
                if s.get("kind") in ("ppt", "pptx"):
                    for l in lines:  # 슬라이드에는 머리말·꼬리말 영역 구분이 없다
                        l["margin"] = False
                    text = content_text(lines)
                chars = len(re.sub(r"\s", "", text))
                prev = old.get((s["id"], idx))
                limit = 20 if s.get("kind") in ("ppt", "pptx") else min_chars  # 슬라이드는 글자가 적다
                auto_excl = chars < limit or bool(re.search(r"^\s*(목\s*차|차\s*례|contents)\s*$", text[:200], re.I | re.M))
                pages.append({
                    "g": len(pages), "src": s["id"], "idx": idx,
                    "label": self.label_for(s, idx),
                    "chasi": prev["chasi"] if prev else s.get("chasi", 1),
                    "excluded": prev["excluded"] if prev else auto_excl,
                    "chars": chars, "lines": lines,
                    "alt_text": alt[idx] if idx < len(alt) else None,
                    "size": [round(page.rect.width, 1), round(page.rect.height, 1)],
                })
            doc.close()
        if len(out):
            out.save(self.dir / "manuscript.pdf", garbage=3, deflate=True)
        out.close()
        _atomic_write(self.dir / "pages.json", json.dumps(pages, ensure_ascii=False))
        self._pages_cache = None

    @staticmethod
    def label_for(source: dict, idx: int) -> str:
        m = source["page_map"]
        exc = {int(k): v for k, v in m.get("exceptions", {}).items()}
        if m.get("mode") == "printed" and idx in exc and m.get("use_exceptions"):
            return str(exc[idx])
        return str(idx + int(m["offset"]))

    def set_page_map(self, sid: str, offset: int, mode: str, confirmed: bool = True):
        meta = self.meta
        for s in meta["sources"]:
            if s["id"] == sid:
                s["page_map"]["offset"] = int(offset)
                s["page_map"]["mode"] = mode
                s["page_map_confirmed"] = confirmed
        self.save_meta(meta)
        pages = self.pages
        srcs = {s["id"]: s for s in meta["sources"]}
        for p in pages:
            p["label"] = self.label_for(srcs[p["src"]], p["idx"])
        self.save_pages(pages)

    def page_map_ready(self) -> bool:
        srcs = self.meta["sources"]
        return bool(srcs) and all(s.get("page_map_confirmed") for s in srcs)

    @property
    def pages(self) -> list[dict]:
        cache = getattr(self, "_pages_cache", None)
        if cache is None:
            p = self.dir / "pages.json"
            cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
            self._pages_cache = cache
        return cache

    def save_pages(self, pages: list[dict]):
        with _LOCK:
            _atomic_write(self.dir / "pages.json", json.dumps(pages, ensure_ascii=False))
            self._pages_cache = None

    def page_text(self, g: int) -> str:
        """LLM에 주는 페이지 텍스트. PPTX는 띄어쓰기가 보존된 슬라이드 원문을 쓴다."""
        pg = self.pages[g]
        return pg.get("alt_text") or content_text(pg["lines"])

    @property
    def manuscript_path(self) -> Path:
        return self.dir / "manuscript.pdf"

    def manuscript_hash(self) -> str:
        return "|".join(s["sha256"][:12] for s in self.meta["sources"])

    # ---------- 문항 DB ----------
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.dir / "items.sqlite", timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with _LOCK, self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS items(
              key TEXT PRIMARY KEY, batch INTEGER, page INTEGER, chasi INTEGER, type TEXT,
              difficulty TEXT, status TEXT, data TEXT, created TEXT, updated TEXT);
            CREATE TABLE IF NOT EXISTS batches(
              batch INTEGER PRIMARY KEY, created TEXT, kind TEXT, options TEXT, slots TEXT, status TEXT, summary TEXT);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, key TEXT, batch INTEGER, kind TEXT, detail TEXT);
            """)

    def new_batch(self, kind: str, options: dict, slots: list[dict]) -> int:
        with _LOCK, self._conn() as c:
            row = c.execute("SELECT COALESCE(MAX(batch),0)+1 FROM batches").fetchone()
            b = row[0]
            c.execute("INSERT INTO batches VALUES(?,?,?,?,?,?,?)",
                      (b, now(), kind, json.dumps(options, ensure_ascii=False),
                       json.dumps(slots, ensure_ascii=False), "pending", "{}"))
            return b

    def batches(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM batches ORDER BY batch").fetchall()
        return [{**dict(r), "options": json.loads(r["options"]), "slots": json.loads(r["slots"]),
                 "summary": json.loads(r["summary"] or "{}")} for r in rows]

    def set_batch_status(self, batch: int, status: str, summary: dict | None = None):
        with _LOCK, self._conn() as c:
            if summary is None:
                c.execute("UPDATE batches SET status=? WHERE batch=?", (status, batch))
            else:
                c.execute("UPDATE batches SET status=?, summary=? WHERE batch=?",
                          (status, json.dumps(summary, ensure_ascii=False), batch))

    def next_key(self) -> str:
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        return f"K{n + 1:05d}"

    def save_item(self, item: dict, event: str | None = None, detail: dict | None = None) -> dict:
        with _LOCK, self._conn() as c:
            if not item.get("key"):
                n = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                item["key"] = f"K{n + 1:05d}"
                item["created"] = now()
            item["updated"] = now()
            c.execute("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (item["key"], item.get("batch"), item.get("page"), item.get("chasi"), item.get("type"),
                       item.get("difficulty"), item.get("status"), json.dumps(item, ensure_ascii=False),
                       item.get("created"), item["updated"]))
            if event:
                c.execute("INSERT INTO events(ts,key,batch,kind,detail) VALUES(?,?,?,?,?)",
                          (now(), item["key"], item.get("batch"), event,
                           json.dumps(detail or {}, ensure_ascii=False)))
        return item

    def log(self, kind: str, detail: dict, key: str | None = None, batch: int | None = None):
        with _LOCK, self._conn() as c:
            c.execute("INSERT INTO events(ts,key,batch,kind,detail) VALUES(?,?,?,?,?)",
                      (now(), key, batch, kind, json.dumps(detail, ensure_ascii=False)))

    def items(self, status: list[str] | None = None, batch: int | None = None) -> list[dict]:
        q, args = "SELECT data FROM items WHERE 1=1", []
        if status:
            q += f" AND status IN ({','.join('?' * len(status))})"
            args += status
        if batch is not None:
            q += " AND batch=?"
            args.append(batch)
        q += " ORDER BY key"
        with self._conn() as c:
            return [json.loads(r[0]) for r in c.execute(q, args).fetchall()]

    def item(self, key: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT data FROM items WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def events(self, key: str | None = None, batch: int | None = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if key:
            q += " AND key=?"
            args.append(key)
        if batch is not None:
            q += " AND batch=?"
            args.append(batch)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in c.execute(q, args)]

    # ---------- 진행 상황 ----------
    def progress_path(self, batch: int) -> Path:
        return self.dir / "runs" / f"batch{batch}_progress.json"

    def write_progress(self, batch: int, data: dict):
        _atomic_write(self.progress_path(batch), json.dumps(data, ensure_ascii=False))

    def read_progress(self, batch: int) -> dict:
        p = self.progress_path(batch)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except json.JSONDecodeError:
            return {}


def _pptx_texts(path: Path) -> list[str]:
    """슬라이드별 텍스트(제목·본문·표·그룹). 발표자 노트는 PDF에 없어 근거 표시가 안 되므로 넣지 않는다."""
    from pptx import Presentation

    def walk(shapes):
        for sh in shapes:
            if sh.shape_type == 6:  # GROUP
                yield from walk(sh.shapes)
            elif getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    yield " | ".join(c.text for c in row.cells)
            elif getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs).strip()
                    if t:
                        yield t

    return ["\n".join(walk(slide.shapes)) for slide in Presentation(str(path)).slides]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
