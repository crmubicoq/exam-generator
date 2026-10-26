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
SAMPLE_PDF = __import__("pathlib").Path(__file__).resolve().parents[1] / "input_17-43.pdf"
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
    # 오답이 모두 거짓 진술 ㄷ 또는 ㄹ을 포함하면(일부만 고른 오답 ['ㄱ']이 없으면) 지름길
    item["choices"] = [{"labels": x} for x in (["ㄱ", "ㄴ"], ["ㄱ", "ㄷ"], ["ㄴ", "ㄷ"], ["ㄷ", "ㄹ"])]
    assert any("조합형 보기" in f for f in schema.check_structure(item, slot))


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


# ---------- Codex 문항 교차 검수(2026-10-21) ----------
def test_combo_label_frequency_shortcut():
    from exam_gen.generate.choice_builder import shortcut_problems
    correct = ["ㄱ", "ㄴ", "ㄹ"]
    # 재출제 K00012: ㄱ·ㄴ·ㄹ 각 3회, ㄷ 2회 → 최빈 라벨 = 정답
    assert any("빈도" in m for m in shortcut_problems(correct, [["ㄱ", "ㄹ"], ["ㄱ", "ㄴ", "ㄷ"], ["ㄴ", "ㄷ", "ㄹ"]]))
    # Codex 제안 대체 보기: ㄱ3·ㄴ3·ㄷ2·ㄹ2 → 단서 없음
    assert shortcut_problems(correct, [["ㄱ", "ㄴ", "ㄷ"], ["ㄴ", "ㄹ"], ["ㄱ", "ㄷ"]]) == []


def test_choice_polish_warnings_not_counted_as_discrimination():
    from exam_gen.quality_report import _issues
    item = {"type": "mcq", "stem": "다음 중 옳은 것은?", "evidence": [], "choices": [],
            "validation": {"warnings": ["G5[보기] 보완 권장: 보기 길이를 맞추면 좋다",
                                        "G5[보기] 정답 단서: 정답에만 상황 단서가 있다"]}}
    out = _issues(item)
    assert len(out["polish"]) == 1 and len(out["discrimination"]) == 1
    item["validation"]["warnings"] = ["G5[보기] 오답이 쉽다"]  # 구분 도입 전 경고는 보수적으로 결함으로 센다
    assert "discrimination" in _issues(item)


def test_llm_recheck_relabels_difficulty_like_generation():
    item = {"difficulty": "최상", "slot": {"difficulty": "최상"},
            "validation": {"G5": {"assessed_difficulty": "상"}, "warnings": ["G5[난이도] 표시 '최상' / 검수 판정 '상'"]}}
    review_llm.relabel_difficulty(item)
    assert item["difficulty"] == "상" and item["difficulty_target"] == "최상"
    assert item["validation"]["warnings"] == ["난이도 재분류: 목표 '최상' → 실제 '상'"]


# ---------- 실제 원고 시험 출제(2026-10-22) ----------
def test_short_answer_with_two_blanks_requires_common():
    item = {"type": "short", "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [기준을 엄격하게 하면 (     )(이)가 줄고, "
            "(     )(은)는 오수락으로 이어진다.]", "answer": "오정합", "explanation": "정답은 오정합이다.",
            "evidence": [], "choices": [], "statements": []}
    assert any("공통" in f for f in rules.check(item)[0])
    rules.autofix(item)
    assert "공통으로 들어갈" in item["stem"] and not any("공통" in f for f in rules.check(item)[0])


# ---------- Codex 실제 원고 검수(2026-10-22) ----------
def _mcq_negative():
    return {"type": "mcq", "difficulty": "중", "answer": "1", "stem": "위험지도에 관한 설명으로 옳지 않은 것은?",
            "choices": [{"text": "취약요인을 중심으로 표시한다.", "is_true": False}] +
                       [{"text": f"참 보기 {i}", "is_true": True} for i in range(3)], "statements": []}


def test_trap_must_contradict_manuscript():
    from exam_gen.validate.review_llm import _trap_fails
    item = _mcq_negative()
    assert any("판정 누락" in f for f in _trap_fails(item, []))
    # 시험2 K00011: '함께 표시' ↔ '중심으로 표시'는 동시에 성립 → 탈락
    assert any("동시에 성립" in f for f in _trap_fails(item, [{"target": "1", "verdict": "compatible", "conflict": "x"}]))
    assert _trap_fails(item, [{"target": "①", "verdict": "contradiction", "conflict": "x"}]) == []
    assert schema.false_targets({"type": "ox", "answer": "O"}) == []
    assert schema.false_targets({"type": "ox", "answer": "X"}) == ["OX"]


def test_allocation_follows_chasi_share_when_items_fewer_than_pages():
    # 3차시 페이지가 글자 수가 많아도 30문항이 3차시에 쏠리지 않아야 한다
    pages = [{"g": g, "chasi": 1 if g < 50 else 2, "chars": 500 if g < 50 else 2000, "excluded": False} for g in range(100)]
    a = allocate(pages, 30)
    per = {c: sum(v for g, v in a.items() if pages[g]["chasi"] == c) for c in (1, 2)}
    assert sum(a.values()) == 30 and per == {1: 6, 2: 24}
    first = sorted(g for g in a if pages[g]["chasi"] == 1)
    assert first[0] < 10 and first[-1] > 40  # 차시 안에서 앞뒤로 고르게


def test_short_answer_exposed_in_stem_fails():
    item = {"type": "short", "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [기록을 확인해 출처를 알아내는 접근을 (     ) 방식이라고 부른다.]",
            "answer": "출처", "explanation": "정답은 출처이다.", "evidence": [], "choices": [], "statements": []}
    assert any("빈칸 밖" in f for f in rules.check(item)[0])


def test_freshness_flags_volatile_facts_on_answer():
    from exam_gen.validate import freshness
    item = {"type": "ox", "answer": "X", "stem": "통신사기피해환급법상 소액 기준인 1만원 이하는 피해 총액 기준이다.",
            "choices": [], "statements": []}
    w = freshness.warnings(item)
    assert w and "정답 관련" in w[0] and "1만원 이하" in w[0]
    assert freshness.warnings({"type": "ox", "answer": "O", "stem": "직원은 확인 경로를 안내한다.", "choices": []}) == []


def test_pool_review_flags_same_principle_items(sample_project):
    from exam_gen.validate import pool_review
    p = sample_project
    base = {"type": "ox", "difficulty": "하", "answer": "O", "stem": "x", "explanation": "", "evidence": [],
            "status": "warn", "chasi": 1, "page": 0, "batch": 999, "slot": {"sid": "S1"}, "validation": {"warnings": []}}
    for i, kp in enumerate(["독립 확인 경로를 써야 한다 A", "독립 확인 경로를 써야 한다 B", "FDS는 목적을 모른다"]):
        p.save_item({**copy.deepcopy(base), "key": f"K9000{i}", "key_point": kp})
    r = pool_review.run(p, MOCK)
    assert len(r["groups"]) == 1 and set(r["groups"][0]["keys"]) == {"K90000", "K90001"}
    w = p.item("K90000")["validation"]["warnings"]
    assert any(x.startswith(pool_review.PREFIX) and "K90001" in x for x in w)
    assert not any(x.startswith(pool_review.PREFIX) for x in p.item("K90002")["validation"]["warnings"])
    pool_review.run(p, MOCK)  # 다시 점검해도 경고가 중복되지 않는다
    assert sum(x.startswith(pool_review.PREFIX) for x in p.item("K90000")["validation"]["warnings"]) == 1
    for i in range(3):  # 세션 공용 프로젝트이므로 다른 테스트(배치·통과 문항 집계)에 섞이지 않게 정리
        review_ops.set_status(p, f"K9000{i}", "deleted", "테스트 정리")


# ---------- Codex 시험 3 검수(2026-10-22) ----------
def test_short_answer_must_be_concept_term(sample_project):
    class Fake:
        def __init__(self, term):
            self.term = term

        def structured(self, system, user, schema, **k):
            return {"results": [{"id": i, "checks": [{"id": c, "result": "pass", "note": ""} for c in schema_checks],
                                 "traps": [], "short_term": self.term, "assessed_difficulty": "하",
                                 "accept_variants": [], "suggestion": ""} for i in k["context"]["ids"]]}

    schema_checks = schema.REVIEW_CHECKS
    item = {"type": "short", "difficulty": "하", "page": 1, "stem": "x", "answer": "조합", "explanation": "", "evidence": []}
    f, _, _ = review_llm.review(Fake("phrase"), MOCK, sample_project, [item])["1"]
    assert any("단답 용어" in x for x in f)
    f, _, _ = review_llm.review(Fake("concept"), MOCK, sample_project, [item])["1"]
    assert f == []


def test_short_variants_drop_josa_only_duplicates():
    item = {"type": "short", "answer": "사후 리뷰",
            "short_variants": ["after-action review", "After Action Review", "사후리뷰를", "사후 리뷰", "AAR", "AAR"]}
    rules.clean_variants(item)
    assert item["short_variants"] == ["after-action review", "AAR"]
    item = {"type": "short", "answer": "보장 리모델링", "short_variants": [],
            "validation": {"G5": {"accept_variants": ["보장리모델링을", "보험 리모델링"]}, "warnings": []}}
    assert review_llm.merge_variants(item) == ["보험 리모델링"]


# ---------- 담당자 협의 반영(2026-10-26) ----------
def test_export_scopes():
    from exam_gen.output import numbering
    items = [{"key": f"K{i}", "status": s, "type": "ox", "chasi": 1, "page": 0}
             for i, s in enumerate(["approved", "auto_pass", "warn", "manual", "deleted", "prior"])]
    assert {it["status"] for it in numbering.select(items, scope="all")} == {"approved", "auto_pass", "warn", "manual"}


def test_short_answer_shows_similar_answers():
    from exam_gen.output.excel import answer_cell, _scoring
    it = {"type": "short", "answer": "장수 리스크", "short_variants": ["장수 위험"]}
    assert answer_cell(it) == "장수 리스크 (유사답변: 장수 위험)"
    assert "유사답변" in _scoring(it, {"scoring": "시스템 자동채점"})


def test_mcq_positive_ratio_at_most_20pct_including_combo():
    pages = [{"g": g, "chasi": 1, "chars": 1000, "excluded": False, "label": str(g)} for g in range(40)]
    opts = {**criteria()["defaults"]}
    slots = build_slots(pages, allocate(pages, 300), opts)
    mcq = [s for s in slots if s["type"] == "mcq"]
    pos = sum(1 for s in mcq if s.get("polarity") == "positive")
    assert pos / len(mcq) <= 0.21 and any(s.get("combo") for s in mcq)


def test_default_provider_is_claude_subscription():
    from exam_gen.llm.settings import LLMSettings
    assert LLMSettings().provider == "claude_agent_sdk"


def test_project_naming_rename_delete_restore():
    from exam_gen import store
    p = store.Project.create("관리 테스트 과정", "2026 상반기")
    assert p.meta["name"] == "관리 테스트 과정 · 2026 상반기" and p.meta["course"] == "관리 테스트 과정"
    with pytest.raises(FileExistsError):
        store.Project.create("관리 테스트 과정", "2026 상반기")
    q = store.Project.create("관리 테스트 과정", "2026 하반기")
    with pytest.raises(FileExistsError):
        q.rename("관리 테스트 과정", "2026 상반기")
    q.rename("관리 테스트 과정", "재출제")
    info = q.info()
    assert info["name"] == "관리 테스트 과정 · 재출제" and info["items"] == 0 and info["created"]
    slug = q.slug
    store.delete_project(slug)
    assert slug not in store.list_projects()
    trash = [t for t in store.list_trash() if t["slug"] == slug]
    assert trash and trash[0]["deleted"]
    assert store.restore_project(trash[0]["dir"]) == slug and slug in store.list_projects()
    store.delete_project(slug)
    store.purge_trash([t for t in store.list_trash() if t["slug"] == slug][0]["dir"])
    assert not [t for t in store.list_trash() if t["slug"] == slug]


def test_prior_items_template_roundtrip_and_dedup(sample_project, tmp_path):
    from exam_gen.ingest import prior_items as P
    from exam_gen.output import excel
    from exam_gen.store import Project
    p = Project.create("기출 테스트", "모의")
    p.add_source(SAMPLE_PDF)
    p.set_page_map(p.meta["sources"][0]["id"], 17, "printed", True)
    items = [{"key": "K00001", "status": "warn", "type": "ox", "chasi": 1, "page": 0, "source_pages": [0],
              "stem": "은퇴 준비기 고객은 노후 자금 마련이 중요한 과제이다.", "answer": "X", "explanation": "틀린 설명이다.",
              "evidence": [], "validation": {"warnings": [], "fails": []}},
             {"key": "K00002", "status": "warn", "type": "short", "chasi": 1, "page": 1, "source_pages": [1],
              "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [( ) 리스크]", "answer": "장수",
              "short_variants": ["장수 위험"], "explanation": "정답은 장수이다.", "evidence": [],
              "validation": {"warnings": [], "fails": []}}]
    out = tmp_path / "prior.xlsx"
    excel.write(p, items, out, scope="all")
    assert P.is_template_excel(out)
    got = P.parse_template_excel(out)
    assert [(g["type"], g["answer"]) for g in got] == [("ox", "X"), ("short", "장수")]
    assert got[1]["short_variants"] == ["장수 위험"]
    assert P.save(p, got, "prior.xlsx") == 2 and P.save(p, got, "prior.xlsx") == 0  # 같은 발문은 한 번만
    assert p.items() == [] and len(p.items(status=["prior"])) == 2
    # 추가 출제의 중복 검사(G6)에 기출이 들어간다
    from exam_gen import review_ops
    new = {**got[0], "key": "K00009", "status": "warn", "statements": [], "choices": [], "key_point": got[0]["stem"],
           "evidence": [], "validation": {"warnings": [], "fails": []}}
    from exam_gen.validate import dedup
    f, _, _ = dedup.check(new, p.items(status=list(review_ops.ACCEPTED)))
    assert f, "기출과 같은 문항이 G6에서 막혀야 한다"


def test_prior_items_ai_extract_with_mock():
    from exam_gen.ingest import prior_items as P
    out = P.ai_extract(LLMClient_mock(), MOCK, "안내문\n1. 문제: 지연이체는 창구 거래에 적용되지 않는다.\n")
    assert out and out[0]["type"] == "ox" and "지연이체" in out[0]["stem"]


def LLMClient_mock():
    from exam_gen.llm.client import LLMClient
    return LLMClient(MOCK)


# ---------- 원고 업데이트(판 교체) ----------
def _edit_pdf(src, dst, edits, insert_blank_front=False):
    """edits: [(page_idx, line_bbox, new_text or None)] — 줄을 지우고(None) 또는 새 글로 바꾼다."""
    d = pymupdf.open(src)
    for idx, bbox, text in edits:
        pg = d[idx]
        pg.add_redact_annot(pymupdf.Rect(bbox))
        pg.apply_redactions()
        if text:
            pg.insert_text((bbox[0], bbox[3] - 2), text, fontname="korea", fontsize=9)
    if insert_blank_front:
        d.insert_page(0)
    d.save(dst)


def _first_line(p, it):
    """정답 근거의 첫 줄과, 그 줄 안에서 근거 인용문이 시작하는 글자(바꿀 위치)."""
    for ev in it["evidence"]:
        m = ev.get("match")
        if m and ev.get("is_true") and m["level"] == "L2":
            pg = p.pages[m["g"]]
            ln = next(l for l in pg["lines"] if l["no"] == m["lines"][0])
            head = ev["quote"].strip()[:4]
            if head and head in ln["text"]:
                return pg["idx"], {**ln, "head": head}
    return None


def _changed(ln, word):
    return ln["text"].replace(ln["head"], word, 1)


def test_manuscript_replace_flags_only_changed_items_without_llm(tmp_path, monkeypatch):
    from exam_gen import manuscript_update as MU
    from exam_gen.generate.generator import start_batch
    from exam_gen.llm.client import LLMClient
    from exam_gen.store import Project
    p = Project.create("원고 교체 테스트", "모의")
    sid = p.add_source(SAMPLE_PDF)["id"]
    p.set_page_map(sid, 17, "printed", True)
    opts = dict(criteria()["defaults"])
    start_batch(p, LLMSettings(provider="mock", workers=1),
                build_slots(p.pages, {1: 2, 3: 2, 5: 2, 7: 2}, opts), opts, background=False)
    ok = [it for it in p.items() if it["status"] in ("auto_pass", "warn") and _first_line(p, it)]
    assert len(ok) >= 3
    a, b, c = ok[0], next(x for x in ok if x["page"] != ok[0]["page"]), ok[-1]
    (ia, la), (ib, lb) = _first_line(p, a), _first_line(p, b)
    v2 = tmp_path / "v2.pdf"
    _edit_pdf(SAMPLE_PDF, v2, [(ia, la["bbox"], _changed(la, "변경된 내용")), (ib, lb["bbox"], None)],
              insert_blank_front=True)
    before = {it["key"]: (it["page"], p.pages[it["page"]]["label"]) for it in p.items()}

    def no_llm(*a, **k):
        raise AssertionError("원고 교체에서 AI를 부르면 안 된다")
    monkeypatch.setattr(LLMClient, "structured", no_llm)
    rep = MU.replace_source(p, sid, v2)
    monkeypatch.undo()
    assert rep["version"] == 2 and rep["pages_new"] == rep["pages_old"] + 1
    flagged = {it["key"]: it["validation"]["ms_change"] for it in MU.flagged_items(p)}
    assert a["key"] in flagged and flagged[a["key"]]["kind"] == "changed" and b["key"] in flagged
    for it in p.items():
        if it["key"] not in flagged and it["status"] != "deleted":
            # 쪽이 하나 밀려도(앞에 빈 쪽) 인쇄 쪽수는 그대로, 전역 쪽 번호는 +1
            assert it["page"] == before[it["key"]][0] + 1 and p.pages[it["page"]]["label"] == before[it["key"]][1]
            assert it.get("manuscript_version") == 2
    est = MU.estimate(MU.flagged_items(p))
    assert est["calls"] > 0 and est["revise"] >= 1
    # 일괄 처리(모의): 문항 키 유지, 고치기 전 내용 보관
    MU.start_revise(p, LLMSettings(provider="mock", workers=1), [a["key"]], background=False)
    na = p.item(a["key"])
    assert na["status"] in ("auto_pass", "warn") and not na["validation"].get("ms_change")
    assert na["manuscript_version"] == 2 and na["history"][-1]["action"].startswith("manuscript_")
    assert na["original"]["stem"] == a["stem"]
    # 3판: 다른 문항 하나만 바뀌면 그 문항만 새로 표시된다(이미 고친 a는 다시 표시되지 않음)
    c = next(x for x in (p.item(o["key"]) for o in ok) if x["key"] not in (a["key"], b["key"])
             and x["status"] in ("auto_pass", "warn") and _first_line(p, x))
    ic, lc = _first_line(p, c)
    v3 = tmp_path / "v3.pdf"
    _edit_pdf(p.dir / "pdf" / f"{sid}.pdf", v3, [(ic, lc["bbox"], _changed(lc, "수정된 내용"))])
    rep3 = MU.replace_source(p, sid, v3)
    f3 = {it["key"]: it["validation"]["ms_change"] for it in MU.flagged_items(p)}
    assert rep3["version"] == 3 and c["key"] in f3 and f3[c["key"]]["version"] == 3
    assert a["key"] not in f3 and b["key"] in f3  # 처리하지 않은 b는 표시가 남는다


def test_fast_mode_skips_llm_gates_and_verify_later():
    from exam_gen import review_ops
    from exam_gen.generate.generator import start_batch
    from exam_gen.llm.client import LLMClient
    from exam_gen.store import Project
    p = Project.create("빠른 출제 테스트", "모의")
    sid = p.add_source(SAMPLE_PDF)["id"]
    p.set_page_map(sid, 17, "printed", True)
    opts = {**criteria()["defaults"], "fast_mode": True}
    calls = []
    orig = LLMClient.structured

    def spy(self, system, user, schema, **k):
        calls.append(k.get("task"))
        return orig(self, system, user, schema, **k)
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(LLMClient, "structured", spy)
    start_batch(p, LLMSettings(provider="mock", workers=1), build_slots(p.pages, {1: 3, 3: 3}, opts), opts,
                background=False)
    assert set(calls) == {"generate"}  # G4(solve)·G5(review)·풀 점검(pool) 호출 없음
    sk = review_ops.skipped_items(p)
    assert sk and all(it["status"] == "warn" and not integrity.is_verified(it) for it in sk)
    assert review_ops.estimate_verify(LLMSettings(provider="mock", chunk_size=8), len(sk)) == 2
    calls.clear()
    r = review_ops.verify_skipped(p, LLMSettings(provider="mock", workers=1), [it["key"] for it in sk])
    mp.undo()
    assert r["ok"] + r["manual"] == len(sk) and calls.count("solve") == calls.count("review") >= 1
    assert not review_ops.skipped_items(p)


def test_final_select_backup_and_exam_paper(tmp_path):
    from exam_gen import backup, select_final
    from exam_gen.generate.generator import start_batch
    from exam_gen.output import exam_paper, numbering
    from exam_gen.store import Project
    p = Project.create("선정 백업 테스트", "모의")
    sid = p.add_source(SAMPLE_PDF)["id"]
    p.set_page_map(sid, 17, "printed", True)
    opts = dict(criteria()["defaults"])
    start_batch(p, LLMSettings(provider="mock", workers=1), build_slots(p.pages, {1: 3, 3: 3, 5: 3}, opts), opts,
                background=False)
    ok = [it for it in p.items() if it["status"] in ("approved", "auto_pass", "warn")]
    rep = select_final.select(p, target=max(1, len(ok) - 2))
    assert rep["selected"] == max(1, len(ok) - 2)
    sel = numbering.select(p.items(), scope="selected")
    assert len(sel) == rep["selected"] and all(it["status"] in ("approved", "auto_pass", "warn") for it in sel)
    # 배치가 끝나면 자동 백업이 있고, 되돌리면 그 시점의 문항 수로 돌아간다
    bks = backup.list_backups(p.slug)
    assert bks and any("출제" in b["reason"] for b in bks)
    n_before = len(p.items())
    p.delete_items([p.items()[0]["key"]])
    backup.restore(p, bks[0]["path"])
    assert len(Project(p.slug).items()) == n_before
    r = exam_paper.build(p, p.items(), tmp_path / "paper.pdf", scope="all")
    assert r["items"] >= 1 and r["pages"] >= 1


def test_export_log_and_appeal(tmp_path):
    """출력 이력(번호↔키↔해시·스냅숏) → 이의신청 검색(내용 일부·문항키·출력번호) → 처리 기록·폐기 → 검토서 PDF."""
    from exam_gen import appeal
    from exam_gen.generate.generator import start_batch
    from exam_gen.output import excel, export_log
    from exam_gen.review_ops import save_edit
    from exam_gen.store import Project
    p = Project.create("이의신청 테스트", "모의")
    sid = p.add_source(SAMPLE_PDF)["id"]
    p.set_page_map(sid, 17, "printed", True)
    opts = dict(criteria()["defaults"])
    start_batch(p, LLMSettings(provider="mock", workers=1), build_slots(p.pages, {1: 2, 3: 2}, opts), opts,
                background=False)
    r = excel.write(p, p.items(), tmp_path / "out.xlsx", scope="all")
    hist = export_log.history(p)
    assert len(hist) == 1 and hist[0]["kind"] == "xlsx" and hist[0]["n"] == r["count"]
    row = hist[0]["items"][0]
    assert appeal.by_number(p, hist[0]["id"], row["no"]) == row["key"]
    it = p.item(row["key"])
    old_stem = it["stem"]
    # 띄어쓰기를 빼고 일부만 붙여넣어도 찾는다
    q = old_stem[2:22].replace(" ", "")
    found = appeal.search(q, [p.slug])
    assert found and found[0]["key"] == it["key"] and found[0]["exports"] == 1
    assert appeal.search(it["key"].lower(), [p.slug])[0]["key"] == it["key"]
    # 출력 뒤에 고치면 '현재와 다름'으로 보이고, 출력 당시 문장으로도 찾는다
    it["stem"] = "완전히 새로 쓴 발문으로 바꾸었다?"
    save_edit(p, it, "테스트")
    ex = export_log.item_exports(p, it["key"], appeal.content_hash(p.item(it["key"])))
    assert ex and not ex[0]["same"] and ex[0]["snap"]["stem"] == old_stem
    f2 = appeal.search(old_stem[2:22], [p.slug])
    assert f2 and f2[0]["key"] == it["key"] and f2[0]["where"].startswith("출력 당시")
    # 처리 기록: 폐기는 삭제 상태로 바꾼다
    appeal.record(p, it["key"], "reject", "원고 근거 확인", "담당자")
    appeal.record(p, it["key"], "discard", "모호함", "담당자")
    recs = appeal.records(p, it["key"])
    assert [x["outcome"] for x in recs] == ["discard", "reject"]
    assert p.item(it["key"])["status"] == "deleted"
    assert any(e["kind"] == "appeal" for e in p.events(it["key"]))
    out = appeal.review_pdf(p, it["key"], tmp_path / "appeal.pdf")
    assert out["pages"] >= 2
    import pymupdf
    with pymupdf.open(out["path"]) as d:
        text = "".join(pg.get_text() for pg in d)
    assert "이의신청 검토서" in text and "기각" in text and "문항 폐기" in text
