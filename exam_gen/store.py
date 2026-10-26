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

import contextlib
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import pymupdf

from .config import DATA_DIR, PROJECTS_DIR, criteria
from .ingest import convert, page_map
from .ingest.pdf_text import content_text, detect_ocr, extract_lines

_LOCK = threading.RLock()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = re.sub(r"[^\w가-힣\-]+", "_", name.strip())
    return s.strip("_") or "project"


_UI_STATE = DATA_DIR / "ui_state.json"


def last_project() -> dict:
    """마지막으로 작업한 프로젝트 {"project", "by": "ui"|"cli"} — 대시보드 새로고침·재시작 뒤에도 같은 프로젝트를 연다."""
    try:
        return json.loads(_UI_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_last_project(slug: str, by: str = "ui"):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(_UI_STATE, json.dumps({"project": slug, "by": by, "at": now()}, ensure_ascii=False))
    except OSError:
        pass  # 기억하지 못해도 동작에는 지장 없음


def list_projects() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in PROJECTS_DIR.iterdir() if (p / "project.json").exists())


# ---------- 프로젝트 관리(담당자 협의 2026-10-23) ----------
TRASH_DIR = PROJECTS_DIR / "_삭제됨"


def display_name(name: str, memo: str = "") -> str:
    """화면에 보이는 프로젝트 이름: '과정명 · 구분 메모'."""
    name, memo = name.strip(), memo.strip()
    return f"{name} · {memo}" if memo else name


def name_taken(name: str, exclude: str | None = None) -> bool:
    for s in list_projects():
        if s != exclude and Project(s).meta.get("name", "").strip() == name.strip():
            return True
    return False


def delete_project(slug: str) -> Path:
    """바로 지우지 않고 휴지통(projects/_삭제됨)으로 옮긴다. 반환: 옮긴 위치."""
    src = PROJECTS_DIR / slug
    if not (src / "project.json").exists():
        raise FileNotFoundError(slug)
    from .backup import backup
    backup(Project(slug), "삭제 전")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dst = TRASH_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{slug}"
    with _LOCK:
        shutil.move(str(src), str(dst))
    if last_project().get("project") == slug:
        try:
            _UI_STATE.unlink()
        except OSError:
            pass
    return dst


def list_trash() -> list[dict]:
    if not TRASH_DIR.exists():
        return []
    out = []
    for d in sorted(TRASH_DIR.iterdir(), reverse=True):
        try:
            meta = json.loads((d / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"dir": d.name, "name": meta.get("name", d.name), "slug": meta.get("slug", d.name),
                    "deleted": datetime.strptime(d.name[:15], "%Y%m%d_%H%M%S").isoformat(timespec="seconds")
                    if re.match(r"\d{8}_\d{6}_", d.name) else ""})
    return out


def restore_project(trash_dir: str) -> str:
    """휴지통에서 되살린다. 같은 폴더 이름이 이미 있으면 뒤에 번호를 붙인다. 반환: slug"""
    src = TRASH_DIR / trash_dir
    slug = json.loads((src / "project.json").read_text(encoding="utf-8")).get("slug") or trash_dir[16:]
    dst, n = PROJECTS_DIR / slug, 2
    while dst.exists():
        dst, n = PROJECTS_DIR / f"{slug}_{n}", n + 1
    with _LOCK:
        shutil.move(str(src), str(dst))
    if dst.name != slug:
        m = json.loads((dst / "project.json").read_text(encoding="utf-8"))
        m["slug"] = dst.name
        _atomic_write(dst / "project.json", json.dumps(m, ensure_ascii=False, indent=2))
    return dst.name


def purge_trash(trash_dir: str | None = None):
    """휴지통 비우기(되돌릴 수 없음). trash_dir를 주면 그 프로젝트만."""
    targets = [TRASH_DIR / trash_dir] if trash_dir else (list(TRASH_DIR.iterdir()) if TRASH_DIR.exists() else [])
    for d in targets:
        shutil.rmtree(d, ignore_errors=True)


class Project:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = PROJECTS_DIR / slug
        if not (self.dir / "project.json").exists():
            raise FileNotFoundError(f"프로젝트가 없습니다: {slug}")
        self._init_db()

    # ---------- 생성 / 메타 ----------
    @classmethod
    def create(cls, name: str, memo: str = "") -> "Project":
        """name: 과정명, memo: 구분 메모(예: '2026 상반기 · 300문항 본출제'). 화면 이름 = '과정명 · 구분 메모'."""
        shown = display_name(name, memo)
        if not shown:
            raise ValueError("과정명을 입력하세요.")
        if name_taken(shown):
            raise FileExistsError(f"같은 이름의 프로젝트가 이미 있습니다: {shown} — 구분 메모를 다르게 적어 주세요.")
        slug = slugify(shown)
        d, n = PROJECTS_DIR / slug, 2
        while d.exists():  # 폴더 이름만 겹치면(기호 차이 등) 번호를 붙인다
            slug = f"{slugify(shown)}_{n}"
            d, n = PROJECTS_DIR / slug, n + 1
        for sub in ("sources", "pdf", "exports", "runs"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        meta = {"name": shown, "course": name.strip(), "memo": memo.strip(), "slug": slug, "created": now(),
                "subject_tag": "", "objectives": "", "sources": [], "options": dict(criteria()["defaults"])}
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

    def rename(self, name: str, memo: str = ""):
        shown = display_name(name, memo)
        if not shown:
            raise ValueError("과정명을 입력하세요.")
        if name_taken(shown, exclude=self.slug):
            raise FileExistsError(f"같은 이름의 프로젝트가 이미 있습니다: {shown}")
        self.update_meta(name=shown, course=name.strip(), memo=memo.strip())
        self.log("rename", {"name": shown})

    def info(self) -> dict:
        """프로젝트 관리 화면용 요약: 생성·마지막 수정, 원고, 문항 수, 배치 이력, 사용 모델."""
        meta = self.meta
        items = self.items()
        status: dict[str, int] = {}
        models: dict[str, int] = {}
        last = meta.get("created", "")
        for it in items:
            status[it.get("status", "")] = status.get(it.get("status", ""), 0) + 1
            g = it.get("gen") or {}
            if g.get("provider"):
                m = f"{g['provider']}{(' ' + g['model']) if g.get('model') else ''} · 프롬프트 {g.get('prompt_version', '?')}"
                models[m] = models.get(m, 0) + 1
            last = max(last, it.get("updated") or "")
        with self._conn() as c:
            ev = c.execute("SELECT MAX(ts) FROM events").fetchone()[0]
        last = max(last, ev or "")
        batches = []
        for b in self.batches():
            s = b.get("summary") or {}
            u = s.get("usage") or {}
            try:
                minutes = round((datetime.fromisoformat(s["updated"]) - datetime.fromisoformat(s["started"])).total_seconds() / 60, 1)
            except (KeyError, ValueError, TypeError):
                minutes = None
            batches.append({"batch": b["batch"], "created": b.get("created", ""), "kind": b.get("kind"),
                            "slots": len(b.get("slots") or []), "status": b.get("status"),
                            "providers": ", ".join(u.get("by_provider") or {}) or "-", "calls": u.get("calls"),
                            "minutes": minutes, "accepted": s.get("accepted"), "manual": s.get("manual")})
        n_prior = len(self.items(status=["prior"]))
        if n_prior:
            status["prior"] = n_prior
        return {"slug": self.slug, "name": meta.get("name", self.slug), "created": meta.get("created", ""),
                "updated": last, "sources": [(s.get("filename"), s.get("pages")) for s in meta.get("sources", [])],
                "pages": len(self.pages), "items": len(items), "status": status, "batches": batches, "models": models}

    # ---------- 원고 ----------
    def add_source(self, src_path: Path, filename: str | None = None) -> dict:
        filename = filename or Path(src_path).name
        meta = self.meta
        sid = "s" + uuid.uuid4().hex[:8]  # 삭제 후 재사용되지 않는 고유 ID (Codex C5)
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

    def remove_source(self, sid: str) -> int:
        """원고 삭제. 이 원고를 근거로 쓰던 문항은 근거·승인이 무효화된다. 반환: 영향받은 문항 수."""
        meta = self.meta
        meta["sources"] = [s for s in meta["sources"] if s["id"] != sid]
        self.save_meta(meta)
        return self.rebuild_manuscript()

    def items_using_source(self, sid: str) -> int:
        gs = {p["g"] for p in self.pages if p["src"] == sid}
        n = 0
        for it in self.items():
            used = {it.get("page")} | {(e.get("match") or {}).get("g") for e in it.get("evidence") or []}
            if gs & used:
                n += 1
        return n

    def rebuild_manuscript(self, idx_map: dict | None = None, relocate: str | None = None) -> int:
        """원고 PDF를 이어 붙이고 pages.json을 다시 만든다. 기존 페이지의 차시·제외 설정은 유지한다.
        기존 문항의 페이지·근거 위치를 (원고 ID, 원고 내 페이지) 기준으로 다시 연결한다. 반환: 무효화된 문항 수.
        idx_map: 원고를 새 판으로 교체할 때 {원고ID: {이전 쪽 idx: 새 쪽 idx}} (쪽이 밀리거나 당겨진 것을 따라감)
        relocate: 새 판으로 교체한 원고 ID — 이 원고를 근거로 쓰는 문항은 근거 문장을 새 판에서 다시 찾는다."""
        meta = self.meta
        idx_map = idx_map or {}
        old_pages = list(self.pages) if (self.dir / "pages.json").exists() else []
        old = {}
        for p in old_pages:
            if p["src"] in idx_map:
                ni = idx_map[p["src"]].get(p["idx"])
                if ni is not None:
                    old[(p["src"], ni)] = p
            else:
                old[(p["src"], p["idx"])] = p
        out = pymupdf.open()
        pages = []
        min_chars = criteria()["thresholds"]["min_page_chars"]
        for s in meta["sources"]:
            last_chasi = s.get("chasi", 1)
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
                    # 새 판에 새로 생긴 쪽은 바로 앞 쪽의 차시를 따른다
                    "chasi": prev["chasi"] if prev else last_chasi,
                    "excluded": prev["excluded"] if prev else auto_excl,
                    "chars": chars, "lines": lines,
                    "alt_text": alt[idx] if idx < len(alt) else None,
                    "size": [round(page.rect.width, 1), round(page.rect.height, 1)],
                })
                last_chasi = pages[-1]["chasi"]
            doc.close()
        if len(out):
            out.save(self.dir / "manuscript.pdf", garbage=3, deflate=True)
        out.close()
        _atomic_write(self.dir / "pages.json", json.dumps(pages, ensure_ascii=False))
        self._pages_cache = None
        return self._remap(old_pages, idx_map, relocate) if old_pages else 0

    def _remap(self, old_pages: list[dict], idx_map: dict | None = None, relocate: str | None = None) -> int:
        """전역 페이지 번호(g)가 바뀐 뒤 문항·배치 슬롯을 새 번호로 옮긴다 (Codex C5).
        근거 원고가 사라진 문항은 근거·검증·승인을 무효화하고 '수동 확인'으로 돌린다.
        relocate 원고를 근거로 쓰는 문항은 근거 문장을 새 판에서 다시 찾는다(manuscript_update.relocate_item)."""
        idx_map = idx_map or {}
        new_g = {(p["src"], p["idx"]): p["g"] for p in self.pages}
        old_key = {}
        for p in old_pages:
            if p["src"] in idx_map:
                ni = idx_map[p["src"]].get(p["idx"])
                old_key[p["g"]] = (p["src"], ni) if ni is not None else None
            else:
                old_key[p["g"]] = (p["src"], p["idx"])
        old_src = {p["g"]: p["src"] for p in old_pages}
        labels = {p["g"]: p["label"] for p in self.pages}

        def remap(g):
            return new_g.get(old_key.get(g)) if g is not None else None

        lost_n = 0
        for it in self.items(status=["approved", "auto_pass", "warn", "manual", "deleted", "prior"]):
            before = json.dumps(it, ensure_ascii=False, sort_keys=True)
            touches = relocate and (old_src.get(it.get("page")) == relocate or any(
                old_src.get((e.get("match") or {}).get("g")) == relocate for e in it.get("evidence") or []))
            if touches and it.get("status") not in ("deleted", "prior"):
                from .manuscript_update import relocate_item
                if relocate_item(self, it, remap):
                    lost_n += 1
                if json.dumps(it, ensure_ascii=False, sort_keys=True) != before:
                    self.save_item(it, "manuscript_relocate", {"flag": (it["validation"].get("ms_change") or {}).get("kind")})
                continue
            lost = []
            ng = remap(it.get("page"))
            if ng is None:
                if it.get("page") is not None:
                    lost.append("출제 페이지")
                it["page"] = None  # 삭제된 원고의 페이지 번호가 다른 원고를 가리키지 않도록 비운다
                if it.get("slot"):
                    it["slot"]["page"] = None
            else:
                it["page"] = ng
                if it.get("slot"):
                    it["slot"]["page"] = ng
                    it["slot"]["label"] = labels[ng]
            for ev in it.get("evidence") or []:
                m = ev.get("match")
                if not m:
                    continue
                mg = remap(m.get("g"))
                if mg is None:
                    ev["match"] = None
                    lost.append(f"근거[{ev.get('target')}]")
                else:
                    m["g"], m["label"] = mg, labels[mg]
            it["source_pages"] = sorted({e["match"]["g"] for e in it.get("evidence") or [] if e.get("match")})
            if lost and it.get("status") not in ("deleted", "prior"):
                lost_n += 1
                v = it.setdefault("validation", {})
                v["fails"] = list(v.get("fails") or []) + [f"원고 변경: {', '.join(lost)}의 원고가 삭제·교체됨 — 근거 재확인 필요"]
                v.pop("verified", None)
                it.pop("approved", None)
                it["status"] = "manual"
            if json.dumps(it, ensure_ascii=False, sort_keys=True) != before:
                self.save_item(it, "source_remap", {"lost": lost})
        with _LOCK, self._conn() as c:
            for b in c.execute("SELECT batch, slots FROM batches").fetchall():
                slots = json.loads(b["slots"])
                for s in slots:
                    ng = remap(s.get("page"))
                    s["page"] = ng  # None이면 재개 시 건너뛴다
                    if ng is not None:
                        s["label"] = labels[ng]
                c.execute("UPDATE batches SET slots=? WHERE batch=?", (json.dumps(slots, ensure_ascii=False), b["batch"]))
        return lost_n

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
        self._remap(pages)  # 페이지 번호는 그대로, 근거 위치에 기록된 쪽수 라벨만 갱신

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
    @contextlib.contextmanager
    def _conn(self):
        """연결을 쓰고 나면 반드시 닫는다(sqlite3의 with는 커밋만 하고 닫지 않아 Windows에서 파일이 잠긴 채 남는다)."""
        c = sqlite3.connect(self.dir / "items.sqlite", timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

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

    def set_batch_slots(self, batch: int, slots: list[dict]):
        with _LOCK, self._conn() as c:
            c.execute("UPDATE batches SET slots=? WHERE batch=?", (json.dumps(slots, ensure_ascii=False), batch))

    def next_sid_number(self) -> int:
        """모든 배치에서 쓰지 않은 다음 슬롯 번호."""
        nums = [int(s["sid"][1:]) for b in self.batches() for s in b["slots"] if s["sid"][1:].isdigit()]
        return max(nums, default=0) + 1

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
        """출제 문항 목록. 기출(prior)은 status에 'prior'를 넣었을 때만 포함한다(검수·출력·통계에서 자동 제외)."""
        q, args = "SELECT data FROM items WHERE 1=1", []
        if status:
            q += f" AND status IN ({','.join('?' * len(status))})"
            args += status
        else:
            q += " AND status != 'prior'"
        if batch is not None:
            q += " AND batch=?"
            args.append(batch)
        q += " ORDER BY key"
        with self._conn() as c:
            return [json.loads(r[0]) for r in c.execute(q, args).fetchall()]

    def delete_items(self, keys: list[str]):
        """문항 행을 지운다(기출 정리용). 출제 문항은 상태를 'deleted'로 바꾸는 set_status를 쓴다."""
        if not keys:
            return
        with _LOCK, self._conn() as c:
            c.execute(f"DELETE FROM items WHERE key IN ({','.join('?' * len(keys))})", keys)
        self.log("items_deleted", {"keys": keys[:50], "n": len(keys)})

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
    """임시 파일에 쓴 뒤 바꿔치기한다. Windows에서는 다른 프로세스(대시보드 진행 화면, 백신 검사)가 대상 파일을 잠깐
    열고 있으면 바꿔치기가 PermissionError로 실패한다 — 잠시 기다렸다가 다시 시도한다(간헐 테스트 실패의 원인)."""
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:8]}.tmp")  # 스레드마다 다른 임시 파일
    tmp.write_text(text, encoding="utf-8")
    for i in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (i + 1))
