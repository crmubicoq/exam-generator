"""출제 파이프라인 — 페이지 단위 생성과 관문 G1~G6.

  생성(LLM) → G1 구조 → G2 근거 매칭 → G3 규칙 → G4 독립 풀이 → G5 LLM 검수 → G6 중복 → 저장
  탈락한 슬롯만 사유를 붙여 재출제(최대 max_retries회). 끝까지 실패하면 '수동 확인'(manual)으로 저장한다.
  최종 출력에는 사람이 승인(approved)한 문항만 들어간다(G7, output 모듈).

상태 값: auto_pass(G1~G6 통과·경고 없음) / warn(통과했지만 경고 있음) / manual(수동 확인) / approved / deleted
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import PROMPT_VERSION, criteria
from ..llm.client import LLMClient, LLMError
from ..llm.prompts import render
from ..store import Project, now
from ..validate import dedup, evidence, review_llm, rules, schema, solver
from . import choice_builder

RUNNING: dict[tuple[str, int], "Pipeline"] = {}  # Streamlit 세션 간 공유되는 실행 중 배치


def slot_spec(s: dict) -> str:
    d = f"난이도 {s['difficulty']}"
    if s["type"] == "ox":
        return f"진위형 · {d} · 정답 {s['answer']}"
    if s["type"] == "short":
        return f"단답형 · {d}"
    if s.get("combo"):
        return f"선다형 조합형(ㄱㄴㄷㄹ) · {d} · 참 진술 {s['combo_true']}개(나머지는 거짓) · 발문에 '모두' 포함"
    pol = "옳은 것 고르기(참 보기 1개)" if s.get("polarity") == "positive" else "옳지 않은 것 고르기(거짓 보기 1개)"
    return f"선다형 · {d} · {pol} · 정답 위치 {s['answer']}번"


class Pipeline:
    def __init__(self, project: Project, settings, batch: int, slots: list[dict], options: dict):
        self.p = project
        self.s = settings
        self.batch = batch
        self.slots = slots
        self.opts = options
        self.client = LLMClient(settings)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.meta = project.meta
        self.existing = [it for it in project.items() if it.get("status") != "deleted"]
        for it in self.existing:
            it["_tokens"] = dedup.tokens(it)
        self.prog = {"batch": batch, "status": "running", "message": "", "started": now(), "updated": now(),
                     "slots_total": len(slots), "pages_total": len({s["page"] for s in slots}), "pages_done": 0,
                     "accepted": 0, "manual": 0, "warn": 0, "calls": 0,
                     "gate_fail": {g: 0 for g in ("G1", "G2", "G3", "G4", "G5", "G6", "누락")},
                     "first_try": {"items": 0, "g2_pass": 0}, "retries": 0}

    # ---------- 실행 ----------
    def run(self):
        RUNNING[(self.p.slug, self.batch)] = self
        self.p.set_batch_status(self.batch, "running")
        self.p.log("batch_start", {"slots": len(self.slots), "provider": self.s.provider,
                                   "model": self.s.model_generate, "prompt_version": PROMPT_VERSION}, batch=self.batch)
        try:
            done_sids = {it["slot"]["sid"] for it in self.p.items(batch=self.batch)}
            todo = [s for s in self.slots if s["sid"] not in done_sids]
            by_page: dict[int, list] = {}
            for s in todo:
                by_page.setdefault(s["page"], []).append(s)
            self.prog["pages_total"] = len(by_page)
            self._flush()
            with ThreadPoolExecutor(max_workers=max(1, self.s.workers)) as ex:
                futs = {ex.submit(self._page, g, sl): g for g, sl in by_page.items()}
                for fu in as_completed(futs):
                    exc = fu.exception()
                    if exc:
                        if isinstance(exc, LLMError):
                            self._halt(f"LLM 오류로 중단: {exc}")
                        else:
                            self.p.log("page_error", {"page": futs[fu], "error": repr(exc),
                                                      "trace": "".join(traceback.format_exception(exc))[-2000:]},
                                       batch=self.batch)
                    with self.lock:
                        self.prog["pages_done"] += 1
                    self._autostop_check()
                    self._flush()
            if self.prog["status"] == "running":
                self.prog["status"] = "done"
                self.prog["message"] = "출제 완료"
        except Exception as e:  # noqa: BLE001
            self.prog["status"] = "error"
            self.prog["message"] = f"오류: {e}"
            self.p.log("batch_error", {"error": repr(e), "trace": traceback.format_exc()[-3000:]}, batch=self.batch)
        finally:
            self.prog["usage"] = dict(self.client.usage)
            self._flush()
            self.p.set_batch_status(self.batch, self.prog["status"], self.prog)
            self.p.log("batch_end", self.prog, batch=self.batch)
            RUNNING.pop((self.p.slug, self.batch), None)

    def _halt(self, msg: str):
        with self.lock:
            if self.prog["status"] == "running":
                self.prog["status"] = "stopped"
                self.prog["message"] = msg
        self.stop.set()

    def _autostop_check(self):
        th = criteria()["thresholds"]
        ft = self.prog["first_try"]
        if self.prog["pages_done"] >= th["autostop_check_after_pages"] and ft["items"] >= 10:
            rate = ft["g2_pass"] / ft["items"]
            if rate < th["autostop_evidence_rate"]:
                self._halt(f"자동 중단: 첫 시도 근거 매칭률 {rate:.0%} < {th['autostop_evidence_rate']:.0%} "
                           "— 원고 텍스트 추출 상태(OCR) 또는 프롬프트를 점검하세요.")

    def _flush(self):
        with self.lock:
            self.prog["updated"] = now()
            self.prog["usage"] = dict(self.client.usage)
            self.p.write_progress(self.batch, self.prog)

    # ---------- 페이지 처리 ----------
    def _page(self, g: int, slots: list[dict]):
        pending = list(slots)
        feedback: dict[str, list[str]] = {}
        last: dict[str, dict] = {}
        attempt = 0
        while pending and attempt <= self.opts.get("max_retries", 3) and not self.stop.is_set():
            attempt += 1
            raw = self._generate(g, pending, feedback)
            cands, failed = [], []
            for s in pending:
                r = raw.get(s["sid"])
                if r is None:
                    self._count("누락")
                    feedback[s["sid"]] = ["이전 응답에서 이 슬롯이 누락됨"]
                    failed.append(s)
                    continue
                item = self._to_item(r, s, attempt)
                fails, warns = self._code_gates(item, s, first=attempt == 1)
                item["validation"]["warnings"] = warns
                if fails:
                    self._fail(item, s, fails, feedback, last, failed)
                else:
                    cands.append((item, s))
            if cands and not self.stop.is_set():
                cands = self._llm_gates(cands, feedback, last, failed)
            for item, s in cands:
                self._accept(item)
            pending = failed
            if pending:
                with self.lock:
                    self.prog["retries"] += len(pending)
            self._flush()
        for s in pending:
            item = last.get(s["sid"])
            if item is None:
                self.p.log("slot_missing", {"sid": s["sid"], "page": g}, batch=self.batch)
                continue
            item["status"] = "manual"
            self.p.save_item(item, "auto_manual", {"fails": item["validation"]["fails"]})
            with self.lock:
                self.prog["manual"] += 1

    def _generate(self, g: int, slots: list[dict], feedback: dict) -> dict:
        pages = self.p.pages
        meta = self.meta
        avoid = self._avoid_list(g)
        out = {}
        size = max(1, self.s.chunk_size)
        for i in range(0, len(slots), size):
            if self.stop.is_set():
                break
            chunk = slots[i:i + size]
            fb = [f"{sid}: {r}" for sid in (s["sid"] for s in chunk) for r in feedback.get(sid, [])]
            prev_g, next_g = _neighbor(pages, g, -1), _neighbor(pages, g, 1)
            user = render("generate_user.j2",
                          course=meta["name"], subject_tag=meta.get("subject_tag", ""),
                          objectives=meta.get("objectives", ""), chasi=pages[g]["chasi"],
                          label=pages[g]["label"], text=self.p.page_text(g),
                          prev_label=pages[prev_g]["label"] if prev_g is not None else "",
                          prev_text=_tail(self.p.page_text(prev_g)) if prev_g is not None else "",
                          next_label=pages[next_g]["label"] if next_g is not None else "",
                          next_text=_head(self.p.page_text(next_g)) if next_g is not None else "",
                          slots=[{"sid": s["sid"], "spec": slot_spec(s)} for s in chunk],
                          avoid=avoid, feedback=fb)
            system = render("generate_system.j2", rubric=criteria()["difficulty_rubric"])
            res = self.client.structured(system, user, schema.gen_schema(len(chunk)),
                                         model=self.s.model_generate, task="generate",
                                         context={"project": self.p, "page": g, "slots": chunk,
                                                  "feedback": feedback})
            with self.lock:
                self.prog["calls"] += 1
            for r in res.get("items", []):
                out[str(r.get("slot_id", "")).strip()] = r
        return out

    def _avoid_list(self, g: int) -> list[str]:
        with self.lock:
            same = [it for it in self.existing if it.get("page") == g or g in (it.get("source_pages") or [])]
        return [f"[{it['type']}] {it.get('key_point') or it.get('stem', '')[:80]}" for it in same][-40:]

    def _to_item(self, r: dict, s: dict, attempt: int) -> dict:
        item = {
            "key": None, "batch": self.batch, "slot": s, "page": s["page"], "chasi": s["chasi"],
            "type": s["type"], "difficulty": s["difficulty"], "polarity": s.get("polarity"),
            "combo": bool(s.get("combo")),
            "stem": r.get("stem", ""), "statements": r.get("statements") or [],
            "choices": [{"text": c.get("text", ""), "is_true": bool(c.get("is_true"))} for c in r.get("choices") or []],
            "answer": str(r.get("answer", "")).strip(), "short_variants": r.get("short_variants") or [],
            "explanation": r.get("explanation", ""), "evidence": r.get("evidence") or [],
            "key_point": r.get("key_point", ""), "difficulty_basis": r.get("difficulty_basis", ""),
            "status": None, "history": [],
            "validation": {"attempts": attempt, "fails": [], "warnings": []},
            "gen": {"provider": self.s.provider, "model": self.s.model_generate, "solver": self.s.model_solve,
                    "reviewer": self.s.model_review, "prompt_version": PROMPT_VERSION,
                    "manuscript": self.p.manuscript_hash(), "at": now()},
        }
        if s["type"] != "mcq":
            item["statements"], item["choices"] = [], []
        if s["type"] == "mcq" and not s.get("combo"):
            item["statements"] = []
        if s["type"] == "ox":
            item["answer"] = item["answer"].upper()
        rules.autofix(item)
        if s.get("combo") and len(item["statements"]) == 4:
            correct = [st["label"] for st in item["statements"] if st.get("is_true")]
            if correct:
                sets = choice_builder.build(correct, int(s["answer"]), seed=hash(s["sid"]) % 10000)
                item["choices"] = [{"text": choice_builder.fmt(x), "labels": x, "is_true": x == sorted(correct, key="ㄱㄴㄷㄹ".index)}
                                   for x in sets]
                item["answer"] = s["answer"]
        return item

    def _code_gates(self, item: dict, s: dict, first: bool) -> tuple[list[str], list[str]]:
        fails = schema.check_structure(item, s)
        warns = []
        if not any(f.startswith("G1") and "근거" in f for f in fails):
            ef, ew = evidence.check_item(self.p.pages, item)
            fails += ef
            warns += ew
            if first:
                with self.lock:
                    self.prog["first_try"]["items"] += 1
                    self.prog["first_try"]["g2_pass"] += 0 if ef else 1
        rf, rw = rules.check(item)
        return fails + rf, warns + rw

    def _llm_gates(self, cands, feedback, last, failed):
        items = [c[0] for c in cands]
        # G4 독립 풀이
        v4 = solver.solve(self.client, self.s, self.p, items)
        with self.lock:
            self.prog["calls"] += 1
        keep = []
        for (item, s), (f, w, raw) in zip(cands, v4.values()):
            item["validation"]["G4"] = raw
            item["validation"]["warnings"] += w
            if f:
                self._fail(item, s, f, feedback, last, failed)
            else:
                keep.append((item, s))
        if not keep or self.stop.is_set():
            return []
        # G5 LLM 검수
        v5 = review_llm.review(self.client, self.s, self.p, [k[0] for k in keep], self.meta.get("objectives", ""))
        with self.lock:
            self.prog["calls"] += 1
        keep2 = []
        for (item, s), (f, w, raw) in zip(keep, v5.values()):
            item["validation"]["G5"] = raw
            item["validation"]["warnings"] += w
            if f:
                self._fail(item, s, f, feedback, last, failed)
            else:
                keep2.append((item, s))
        # G6 중복 (실행 중 다른 페이지에서 통과한 문항까지 포함해야 하므로 잠금 안에서 확인·등록)
        out = []
        for item, s in keep2:
            with self.lock:
                f, w, best = dedup.check(item, self.existing)
                item["validation"]["G6"] = best
                item["validation"]["warnings"] += w
                if not f:
                    item["_tokens"] = dedup.tokens(item)
                    self.existing.append(item)
            if f:
                self._fail(item, s, f, feedback, last, failed)
            else:
                out.append((item, s))
        return out

    def _fail(self, item, s, fails, feedback, last, failed):
        item["validation"]["fails"] = fails
        feedback[s["sid"]] = fails
        last[s["sid"]] = item
        failed.append(s)
        for f in fails:
            self._count(f[:2])
        self.p.log("gate_fail", {"sid": s["sid"], "attempt": item["validation"]["attempts"], "fails": fails,
                                 "stem": item.get("stem", "")[:100]}, batch=self.batch)

    def _count(self, gate):
        with self.lock:
            if gate in self.prog["gate_fail"]:
                self.prog["gate_fail"][gate] += 1

    def _accept(self, item):
        item.pop("_tokens", None)
        item["status"] = "warn" if item["validation"]["warnings"] else "auto_pass"
        item["validation"]["fails"] = []
        item["validation"]["passed_at"] = now()
        item["original"] = _snapshot(item)
        self.p.save_item(item, "auto_accept", {"status": item["status"], "attempts": item["validation"]["attempts"]})
        with self.lock:
            self.prog["accepted"] += 1
            if item["status"] == "warn":
                self.prog["warn"] += 1


def _snapshot(item: dict) -> dict:
    return {k: item.get(k) for k in ("stem", "statements", "choices", "answer", "short_variants", "explanation")}


def _neighbor(pages, g, step):
    j = g + step
    if 0 <= j < len(pages) and pages[j]["src"] == pages[g]["src"]:
        return j
    return None


def _tail(text, n=600):
    return text[-n:]


def _head(text, n=600):
    return text[:n]


def start_batch(project: Project, settings, slots: list[dict], options: dict, kind: str = "initial",
                background: bool = True) -> int:
    batch = project.new_batch(kind, options, slots)
    pipe = Pipeline(project, settings, batch, slots, options)
    if background:
        threading.Thread(target=pipe.run, daemon=True, name=f"batch{batch}").start()
    else:
        pipe.run()
    return batch


def resume_batch(project: Project, settings, batch: int, background: bool = True):
    b = next(x for x in project.batches() if x["batch"] == batch)
    pipe = Pipeline(project, settings, batch, b["slots"], b["options"])
    if background:
        threading.Thread(target=pipe.run, daemon=True, name=f"batch{batch}").start()
    else:
        pipe.run()


def stop_batch(project: Project, batch: int):
    p = RUNNING.get((project.slug, batch))
    if p:
        p._halt("사용자가 중지함 — '이어서 출제'로 재개할 수 있습니다.")
