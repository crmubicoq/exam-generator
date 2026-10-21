"""Codex 검수(2026-10-20) 반례를 고정하는 회귀 테스트. 항목 번호는 docs/TODO.md의 (Cn)."""
import copy
import os

import pymupdf
import pytest

from exam_gen import integrity, review_ops
from exam_gen.config import criteria
from exam_gen.generate import generator as gen
from exam_gen.ingest.pdf_text import normalize
from exam_gen.llm.settings import LLMSettings
from exam_gen.plan.coverage import allocate
from exam_gen.plan.slots import SlotError, build_slots
from exam_gen.validate import dedup, evidence, review_llm, rules, schema, solver

MOCK = LLMSettings(provider="mock", workers=1)
ORIG = "이 시기 고객은 대체로 소득의 정점을 경험하지만, 정년이 가까워질수록 소득 안정성은 낮아진다."


def _run(project, n=6, pages=(1, 2)):
    opts = dict(criteria()["defaults"])
    start = sum(len(b["slots"]) for b in project.batches()) + 1
    slots = build_slots(project.pages, allocate([p for p in project.pages if p["g"] in pages], n), opts,
                        start_index=start, seed=start)
    return gen.start_batch(project, MOCK, slots, opts, background=False)


def _passed(project, batch):
    return [it for it in project.items(batch=batch) if it["status"] in ("auto_pass", "warn")]


# ---------- C1: 승인·출력 관문 ----------
def test_c1_g4_failed_item_cannot_be_approved_or_exported(sample_project):
    b = _run(sample_project)
    it = _passed(sample_project, b)[0]
    it["validation"]["fails"] = ["G4: 독립 풀이 정답 9 ≠ 출제 정답"]
    it["validation"].pop("verified", None)
    it["status"] = "manual"
    sample_project.save_item(it)
    ok, why = review_ops.approve(sample_project, it["key"], "t")
    assert not ok and why
    it = sample_project.item(it["key"])
    it["status"] = "approved"  # 상태만 강제로 바꿔도 출력되지 않아야 한다
    assert not integrity.is_exportable(it)


def test_c1_edited_item_needs_llm_recheck(sample_project):
    b = _run(sample_project)
    it = copy.deepcopy(_passed(sample_project, b)[0])
    assert review_ops.approve(sample_project, it["key"], "t", evidence_confirmed=True)[0]
    it = sample_project.item(it["key"])
    it["explanation"] += " (수정)"
    review_ops.save_edit(sample_project, it)
    it = sample_project.item(it["key"])
    assert it["status"] != "approved" and not integrity.is_exportable(it)
    ok, why = review_ops.approve(sample_project, it["key"], "t", evidence_confirmed=True)
    assert not ok and any("재검증" in w for w in why)
    review_ops.rerun_llm_gates(sample_project, MOCK, it["key"])
    assert review_ops.approve(sample_project, it["key"], "t", evidence_confirmed=True)[0]
    assert integrity.is_exportable(sample_project.item(it["key"]))


def test_c1_l1_evidence_needs_human_confirmation(sample_project):
    b = _run(sample_project)
    it = _passed(sample_project, b)[0]
    it["evidence"][0]["match"] = {**(it["evidence"][0].get("match") or {}), "level": "L1"}
    assert integrity.needs_evidence_confirm(it)


# ---------- C2: 중지 ----------
def test_c2_stop_after_generation_saves_nothing_unverified(sample_project, monkeypatch):
    opts = dict(criteria()["defaults"])
    start = sum(len(b["slots"]) for b in sample_project.batches()) + 1
    slots = build_slots(sample_project.pages, {3: 4}, opts, start_index=start, seed=start)
    batch = sample_project.new_batch("initial", opts, slots)
    pipe = gen.Pipeline(sample_project, MOCK, batch, slots, opts)
    real = pipe._generate

    def gen_then_stop(*a, **k):
        out = real(*a, **k)
        pipe._halt("테스트 중지")
        return out

    monkeypatch.setattr(pipe, "_generate", gen_then_stop)
    pipe.run()
    assert sample_project.items(batch=batch) == []
    assert gen.batch_state(sample_project, batch) == "stopped"
    gen.resume_batch(sample_project, MOCK, batch, background=False)
    assert sample_project.items(batch=batch)


# ---------- C3·C4: 근거 매칭 ----------
@pytest.mark.parametrize("quote", [
    ORIG.replace("낮아진다", "높아진다"),
    ORIG.replace("낮아진다", "낮아지지 않는다"),
    ORIG.replace("대체로", "항상"),
    "정년이 가까워질수록 소득 안정성은 낮아진다. 이 시기 고객은 대체로 소득의 정점을 경험하지만",
    "은퇴 후 30년 이상 생존할 가능성이 높으며, 그 기간 동안 매월 얼마가 필요한가",
    "금융기관에 따르면 " + ORIG,
])
def test_c3_meaning_changed_quote_rejected(sample_project, quote):
    assert evidence.locate(sample_project.pages, quote, 1) is None


@pytest.mark.parametrize("quote", [
    ORIG,
    ORIG.replace("안정성은", "안정성이"),
    ORIG.replace("고객은 대체로", "고객이 대체로"),
    "자산구조는 부동산 비중이 크며 금융자산은 상대적으로 낮은 경우가 많다",
])
def test_c3_harmless_differences_still_match(sample_project, quote):
    m = evidence.locate(sample_project.pages, quote, 1)
    assert m and m["label"] == "18"


def test_c4_normalize_keeps_numeric_meaning():
    assert normalize("1.5퍼센트") != normalize("15퍼센트")
    assert normalize("-100원") != normalize("100원")
    assert normalize("1,234,567명") == normalize("1234567명")
    assert normalize("55~64세") == normalize("55∼64 세")
    assert evidence.numbers_in("1,234,567명, 1.5%, -100원") == {"1234567", "1.5", "-100"}


# ---------- C5: 원고 삭제 ----------
def test_c5_source_removal_remaps_and_invalidates(tmp_path):
    from exam_gen.store import Project
    src = pymupdf.open(os.path.join(os.path.dirname(__file__), "..", "input_17-43.pdf"))
    for name, a, b in (("a.pdf", 0, 2), ("b.pdf", 10, 12)):
        d = pymupdf.open()
        d.insert_pdf(src, from_page=a, to_page=b)
        d.save(tmp_path / name)
    p = Project.create("원고삭제 테스트")
    sa, sb = p.add_source(tmp_path / "a.pdf"), p.add_source(tmp_path / "b.pdf")
    for s in (sa, sb):
        p.set_page_map(s["id"], s["page_map"]["offset"], s["page_map"]["mode"], True)
    _run(p, n=6, pages=(0, 3))
    before = {it["key"]: [p.pages[g]["label"] for g in it["source_pages"]] for it in p.items()}
    p.remove_source(sa["id"])
    for it in p.items():
        if it["status"] == "manual" and it.get("page") is None:
            assert not it.get("approved") and it["source_pages"] == []
        elif it.get("source_pages"):
            assert [p.pages[g]["label"] for g in it["source_pages"]] == before[it["key"]]
            assert all(p.pages[g]["src"] == sb["id"] for g in it["source_pages"])
    assert p.add_source(tmp_path / "a.pdf")["id"] not in (sa["id"], sb["id"])


# ---------- C6·C7: 중복 ----------
def test_c6_unsaved_candidates_are_compared():
    a = {"key": None, "_tmp": "x1", "type": "ox", "stem": "은퇴 준비기 고객은 소득 정점을 경험하지만 소득 안정성은 낮아진다.",
         "answer": "O", "key_point": "소득 안정성", "evidence": []}
    b = {**copy.deepcopy(a), "_tmp": "x2"}
    assert dedup.check(b, [a])[0]
    assert not dedup.check(a, [a])[0]  # 자기 자신은 제외


def test_c7_manual_candidates_do_not_block():
    a = {"key": "K1", "type": "ox", "stem": "후기 고령기에는 자산 단순화가 필요하다.", "answer": "O", "key_point": "x", "evidence": []}
    m = {**a, "key": "K2", "status": "manual"}
    assert not dedup.check(a, [m])[0]


# ---------- C9·C10: 배치 상태 ----------
def test_c9_page_error_marks_partial(sample_project, monkeypatch):
    opts = dict(criteria()["defaults"])
    start = sum(len(b["slots"]) for b in sample_project.batches()) + 1
    slots = build_slots(sample_project.pages, {4: 2, 5: 2}, opts, start_index=start, seed=start)
    batch = sample_project.new_batch("initial", opts, slots)
    pipe = gen.Pipeline(sample_project, MOCK, batch, slots, opts)
    real = pipe._page

    def boom(g, sl):
        if g == 5:
            raise ValueError("테스트 오류")
        return real(g, sl)

    monkeypatch.setattr(pipe, "_page", boom)
    pipe.run()
    assert sample_project.read_progress(batch)["status"] == "partial"
    assert gen.batch_state(sample_project, batch) in gen.RESUMABLE


def test_c10_orphan_running_batch_is_resumable(sample_project):
    opts = dict(criteria()["defaults"])
    slots = build_slots(sample_project.pages, {6: 2}, opts, start_index=9000, seed=9000)
    batch = sample_project.new_batch("initial", opts, slots)
    sample_project.write_progress(batch, {"status": "running", "pid": os.getpid()})
    assert gen.batch_state(sample_project, batch) == "orphan"
    gen.resume_batch(sample_project, MOCK, batch, background=False)
    assert gen.batch_state(sample_project, batch) in ("done", "partial")


# ---------- C11: 불완전한 검증 응답 ----------
def test_c11_g4_requires_every_judgment():
    item = {"type": "mcq", "answer": "2", "choices": [{"text": t, "is_true": t != "b"} for t in "abcd"]}
    r = {"answer": "2", "judgments": [], "unique": True, "solvable": True, "note": ""}
    assert any("누락" in f for f in solver.compare(item, r)[0])


def test_c11_g5_missing_checks_fail(sample_project):
    class Fake:
        def structured(self, *a, **k):
            return {"results": [{"id": "1", "checks": [], "assessed_difficulty": "중", "suggestion": ""}]}

    item = {"type": "ox", "difficulty": "중", "page": 1, "stem": "x", "answer": "O", "explanation": "", "evidence": []}
    v = review_llm.review(Fake(), MOCK, sample_project, [item])
    assert v["1"][0]


# ---------- C12·C13: 설정 충돌 ----------
def test_c12_c13_option_conflicts_raise(sample_project):
    opts = dict(criteria()["defaults"])
    with pytest.raises(SlotError):
        build_slots(sample_project.pages, {0: 20}, opts, difficulty_counts={"최상": 4, "상": 6})
    with pytest.raises(SlotError):
        build_slots(sample_project.pages, {0: 20}, {**opts, "combo_pct": 50, "mcq_positive_pct": 20})


# ---------- C14: 조합형 최종 검사 ----------
def test_c14_combo_choices_checked():
    slot = {"type": "mcq", "combo": True, "combo_true": 2, "answer": "1"}
    st = [{"label": l, "text": l, "is_true": l in "ㄱㄴ"} for l in "ㄱㄴㄷㄹ"]
    item = {"stem": "모두 고른 것은?", "explanation": "x" * 20, "evidence": [{}], "statements": st, "choices": [], "answer": "4"}
    assert any("4개" in f for f in schema.check_structure(item, slot))
    item["choices"] = [{"labels": x} for x in (["ㄱ", "ㄴ"], ["ㄱ"], ["ㄴ", "ㄷ"], ["ㄷ", "ㄹ"])]
    assert any("정답 위치" in f for f in schema.check_structure(item, slot))
    item["answer"] = "1"
    assert schema.check_structure(item, slot) == []


# ---------- C16: 규칙 ----------
def test_c16_particle_after_space_and_quoted_polite():
    base = {"type": "short", "answer": "x", "explanation": "정의에 해당하는 용어이다.", "choices": [], "statements": [], "evidence": []}
    f, _ = rules.check({**base, "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [(     ) 는 상태를 말한다.]"})
    assert any("조사" in x for x in f)
    f, _ = rules.check({**base, "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [(     )(은)는 상태를 말한다.]"})
    assert not any("조사" in x for x in f)
    ox = {"type": "ox", "stem": "교사는 “수업을 시작합니다.”라고 말한다.", "answer": "O",
          "explanation": "원고의 수업 진행 절차에 대한 올바른 설명이다.", "choices": [], "statements": [], "evidence": []}
    assert not any("문어체" in x for x in rules.check(ox)[0])


# ---------- C17: 라벨 배치 ----------
def test_c17_labels_stay_on_page():
    from exam_gen.output.evidence_pdf import _label_slot
    used = {}
    for _ in range(75):
        x, y = _label_slot(used, 700, 110, 40, 561, 757)
        assert 0 <= x and x + 40 <= 561 and 6 <= y <= 757 - 6


# ---------- 실제 LLM 시험에서 발견: 검증기 응답의 일부 누락 ----------
def test_verifier_missing_results_are_reasked(sample_project):
    calls = []

    class Flaky:
        def structured(self, system, user, schema, **k):
            calls.append(k["context"]["ids"])
            ids = k["context"]["ids"]
            keep = ids[1:] if len(calls) == 1 else ids  # 첫 응답은 첫 문항 결과를 빠뜨린다
            return {"results": [{"id": f"[{i}]", "checks": [{"id": c, "result": "pass", "note": ""} for c in
                                                          ("answer", "explanation", "choices", "language", "dispute",
                                                           "completeness", "importance", "difficulty", "fairness")],
                                 "assessed_difficulty": "중", "suggestion": ""} for i in keep]}

    item = {"type": "ox", "difficulty": "중", "page": 1, "stem": "x", "answer": "O", "explanation": "", "evidence": []}
    v = review_llm.review(Flaky(), MOCK, sample_project, [item, dict(item)])
    assert calls == [["1", "2"], ["1"]]
    assert v["1"][0] == [] and v["2"][0] == []
