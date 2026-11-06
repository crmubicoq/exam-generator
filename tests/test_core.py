"""계획서 '검증 방법'의 단위 테스트 — 샘플 원고 input_17-43.pdf 기준."""
import copy
from collections import Counter

import openpyxl
import pymupdf

from exam_gen.config import TEMPLATE_XLSX, criteria
from exam_gen.generate import choice_builder
from exam_gen.ingest.page_map import format_pages
from exam_gen.plan.coverage import allocate, largest_remainder
from exam_gen.plan.slots import build_slots, summarize
from exam_gen.validate import dedup, evidence, rules, schema, solver


# ---------- Phase 0: 쪽수 매핑 ----------
def test_page_map_detects_printed_numbers(sample_project):
    m = sample_project.meta["sources"][0]["page_map"]
    assert m["mode"] == "printed" and m["offset"] == 17 and m["confidence"] == 1.0
    pages = sample_project.pages
    assert pages[0]["label"] == "17" and pages[25]["label"] == "42"


def test_format_pages():
    assert format_pages(["17", "18", "19", "22"]) == "17~19, 22"
    assert format_pages(["30"]) == "30"


# ---------- ★1 커버리지·슬롯 ----------
def test_allocation_covers_every_page(sample_project):
    a = allocate(sample_project.pages, 315)
    elig = [p for p in sample_project.pages if not p["excluded"]]
    assert sum(a.values()) == 315
    assert all(a.get(p["g"], 0) >= 1 for p in elig)
    assert max(a.values()) <= 2 * 315 / len(elig) + 1


def test_additional_allocation_prefers_thin_pages(sample_project):
    existing = {g: 20 for g in range(6, 26)}
    a = allocate(sample_project.pages, 40, existing=existing)
    assert sum(a.values()) == 40
    assert sum(v for g, v in a.items() if g < 6) >= 35  # 기존 문항이 없는 앞 6페이지에 대부분 배분


def test_slot_distribution_exact(sample_project):
    opts = dict(criteria()["defaults"])
    a = allocate(sample_project.pages, 300)
    s = summarize(build_slots(sample_project.pages, a, opts))
    want_d = largest_remainder(300, opts["difficulty_pct"])
    want_t = largest_remainder(300, opts["type_pct"])
    assert s["difficulty"] == {k: v for k, v in want_d.items() if v}
    assert s["type"] == {k: v for k, v in want_t.items() if v}
    pos = [s["answer"].get(k, 0) for k in "1234"]
    assert max(pos) - min(pos) <= 1
    assert abs(s["answer"]["O"] - s["answer"]["X"]) <= 1
    mcq = s["type"]["mcq"]
    assert abs(s["polarity"]["positive"] - round(mcq * 0.2)) <= 1


def test_difficulty_counts_for_additional(sample_project):
    opts = dict(criteria()["defaults"])
    slots = build_slots(sample_project.pages, {0: 5, 1: 5}, opts, difficulty_counts={"최상": 4, "상": 6, "중": 0, "하": 0})
    assert Counter(s["difficulty"] for s in slots) == {"최상": 4, "상": 6}


# ---------- ★2 근거 매칭 ----------
def test_evidence_exact_multiline(sample_project):
    m = evidence.locate(sample_project.pages, "자산 구조는 부동산(주택 중심) 비중이 크며, 금융자산은 상대적으로 낮은 경우가 많다.", 1)
    assert m and m["level"] == "L2" and m["label"] == "18" and m["lines"] == [3, 4]


def test_evidence_table_cell(sample_project):
    m = evidence.locate(sample_project.pages, "연금 중심의 소득 구조, 경제활동 축소", 0)
    assert m and m["label"] == "17"


def test_evidence_wrong_hint_finds_true_page(sample_project):
    m = evidence.locate(sample_project.pages, "고객은 은퇴 후 매월 안정적 현금흐름을 확보할 수 있을지에 대해 깊은 불안을 느낀다.", 10)
    assert m and m["label"] == "18"


def test_evidence_rejects_hallucination(sample_project):
    assert evidence.locate(sample_project.pages, "은퇴 준비기 고객의 자산은 주로 주식과 채권으로 구성된다.", 1) is None


def _ox(stem, quote, answer="O", is_true=True, page=1):
    return {"type": "ox", "page": page, "stem": stem, "answer": answer, "explanation": "원고의 설명에 부합하는 올바른 설명이다.",
            "choices": [], "statements": [], "evidence": [{"target": "OX", "quote": quote, "page_label": "18",
                                                          "is_true": is_true, "trap_note": ""}]}


def test_check_item_blocks_number_hallucination(sample_project):
    it = _ox("은퇴 후 30년 이상 생존할 가능성이 높다.", "은퇴 후 20년 이상 생존할 가능성이 높으며")
    fails, _ = evidence.check_item(sample_project.pages, it)
    assert any("수치" in f for f in fails)


def test_check_item_sets_source_pages(sample_project):
    it = _ox("은퇴 준비기 고객의 자산 구조는 부동산 비중이 크다.",
             "자산 구조는 부동산(주택 중심) 비중이 크며, 금융자산은 상대적으로 낮은 경우가 많다.")
    fails, _ = evidence.check_item(sample_project.pages, it)
    assert not fails and it["source_pages"] == [1]


# ---------- G1 / G3 ----------
def test_structure_negative_polarity():
    slot = {"type": "mcq", "answer": "2", "polarity": "negative"}
    item = {"stem": "다음 중 옳지 않은 것은?", "explanation": "x" * 20, "evidence": [{}], "answer": "2",
            "choices": [{"text": "a", "is_true": True}, {"text": "b", "is_true": False},
                        {"text": "c", "is_true": True}, {"text": "d", "is_true": True}]}
    assert schema.check_structure(item, slot) == []
    item["choices"][0]["is_true"] = False
    assert any("거짓 보기가 2개" in f for f in schema.check_structure(item, slot))


def test_rules_polite_and_particle_hint():
    it = {"type": "short", "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [(     )는 정신 상태를 말합니다.]",
          "answer": "정신건강", "explanation": "정신건강의 정의에 해당하는 용어이다.", "choices": [], "statements": [], "evidence": []}
    fails, _ = rules.check(it)
    assert any("문어체" in f for f in fails) and any("조사" in f for f in fails)


def test_rules_ox_explanation_same_as_stem():
    it = _ox("보안요원은 사고 현장에 신속히 출동한다.", "")
    it["explanation"] = "보안요원은 사고 현장에 신속히 출동한다."
    fails, _ = rules.check(it)
    assert any("해설이 지문과 동일" in f for f in fails)


def test_rules_autofix_choice_prefix():
    it = {"choices": [{"text": "① 가"}, {"text": "2) 나"}], "statements": [], "stem": " x "}
    rules.autofix(it)
    assert [c["text"] for c in it["choices"]] == ["가", "나"]


# ---------- 조합형 보기 ----------
def test_combo_builder_balance():
    for correct, pos in ((["ㄱ", "ㄴ", "ㄷ"], 3), (["ㄴ", "ㄹ"], 1), (["ㄷ"], 4), (["ㄱ", "ㄴ", "ㄷ", "ㄹ"], 2)):
        sets = choice_builder.build(correct, pos)
        assert sets[pos - 1] == correct and len({tuple(s) for s in sets}) == 4
        if len(correct) < 4:
            assert any(len(s) == len(correct) for i, s in enumerate(sets) if i != pos - 1)
        # 거짓 진술 하나만 찾아서는 정답을 고를 수 없어야 한다 (자체 평가 K00016)
        assert choice_builder.shortcut_problems(correct, [s for i, s in enumerate(sets) if i != pos - 1]) == []


# ---------- G4 비교 ----------
def test_solver_compare_detects_wrong_key():
    item = {"type": "mcq", "answer": "2", "choices": [{"text": "a", "is_true": True}, {"text": "b", "is_true": False},
                                                     {"text": "c", "is_true": True}, {"text": "d", "is_true": True}]}
    js = [{"target": str(i + 1), "is_true": c["is_true"], "reason": ""} for i, c in enumerate(item["choices"])]
    ok = {"answer": "2", "judgments": js, "unique": True, "solvable": True, "note": ""}
    assert solver.compare(item, ok)[0] == []
    bad = {**ok, "answer": "3"}
    assert any("≠" in f for f in solver.compare(item, bad)[0])
    multi = {**ok, "judgments": [{**js[0], "is_true": False, "reason": "원고와 다름"}] + js[1:]}
    assert solver.compare(item, multi)[0]


# ---------- G6 ----------
def test_dedup_blocks_same_and_passes_different():
    a = {"key": "K1", "type": "ox", "stem": "은퇴 준비기 고객은 소득의 정점을 경험하지만 소득 안정성은 낮아진다.", "answer": "O",
         "key_point": "은퇴 준비기 소득 안정성", "evidence": []}
    b = {**a, "key": "K2", "stem": "은퇴 준비기 고객은 소득의 정점을 경험하지만 소득 안정성은 낮아진다고 볼 수 있다."}
    c = {"key": "K3", "type": "ox", "stem": "후기 고령기에는 자산 단순화와 유동화 전략이 필요하다.", "answer": "O",
         "key_point": "후기 고령기 자산 단순화", "evidence": []}
    assert dedup.check(b, [a])[0]
    assert not dedup.check(c, [a])[0]


# ---------- 엑셀 양식 ----------
def test_excel_template_header_preserved(sample_project, tmp_path):
    from exam_gen.output import excel
    item = {"key": "K00001", "batch": 1, "page": 1, "chasi": 1, "type": "ox", "difficulty": "중", "status": "approved",
            "stem": "은퇴 준비기 고객의 자산 구조는 부동산 비중이 크다.", "answer": "O", "explanation": "원고에 부합하는 올바른 설명이다.",
            "choices": [], "statements": [], "short_variants": [], "source_pages": [1], "validation": {},
            "evidence": [{"target": "OX", "quote": "자산 구조는 부동산(주택 중심) 비중이 크며", "is_true": True,
                          "match": {"label": "18", "g": 1}}]}
    short = {**copy.deepcopy(item), "key": "K00002", "type": "short", "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [(     )]", "answer": "후기 고령기"}
    from exam_gen import integrity
    for it in (item, short):  # 최종 출력 조건: 현재 내용 검증 + 같은 해시로 승인
        integrity.mark_verified(it)
        it["approved"] = {"hash": integrity.content_hash(it)}
    r = excel.write(sample_project, [item, short], tmp_path / "o.xlsx")
    ws = openpyxl.load_workbook(r["path"]).worksheets[0]
    tpl = openpyxl.load_workbook(TEMPLATE_XLSX).worksheets[0]
    assert {str(x) for x in ws.merged_cells.ranges} == {str(x) for x in tpl.merged_cells.ranges}
    assert [ws.cell(2, c).value for c in range(1, 17)] == [tpl.cell(2, c).value for c in range(1, 17)]
    assert [ws.cell(4, c).value for c in (1, 2, 3, 4, 6, 7, 8, 9, 10)] == [1, "진위형", "18", 1, "O", "X", None, None, 1]
    assert ws.cell(5, 2).value == "단답형" and ws.cell(5, 6).value is None and ws.cell(5, 10).value == "후기 고령기"
    assert ws.column_dimensions["R"].hidden


# ---------- 파이프라인(모의 공급자) + 근거PDF ----------
def test_mock_pipeline_and_evidence_pdf(sample_project, tmp_path):
    from exam_gen.generate.generator import start_batch
    from exam_gen.llm.settings import LLMSettings
    from exam_gen.output import evidence_pdf
    opts = dict(criteria()["defaults"])
    slots = build_slots(sample_project.pages, allocate(sample_project.pages, 20), opts)
    b = start_batch(sample_project, LLMSettings(provider="mock", workers=2), slots, opts, background=False)
    pr = sample_project.read_progress(b)
    # 실제 난이도가 모자라면 배치 끝에 보충 슬롯이 추가된다(slots_total ≥ 20)
    # 간헐 실패 진단용: 실패하면 배치 진행 상황과 문제 문항을 함께 보여 준다
    assert pr["status"] == "done" and pr["accepted"] + pr["manual"] <= pr["slots_total"], pr
    assert pr["slots_total"] == 20 or pr.get("topup"), pr
    items = sample_project.items(batch=b)
    passed = [it for it in items if it["status"] in ("auto_pass", "warn")]
    assert passed, (pr, [(it["key"], it["status"], it["validation"].get("fails")) for it in items])
    assert all(it["source_pages"] for it in passed), [it["key"] for it in passed if not it["source_pages"]]
    r = evidence_pdf.build(sample_project, items, tmp_path / "e.pdf", mode="extract", draft=True, batch=b)
    assert r["no_highlight"] == []
    doc = pymupdf.open(r["path"])
    assert sum(1 for pg in doc for _ in pg.annots()) > 0


def test_difficulty_calibration_plan():
    """난이도 보정 출제: 판정이 한 단계 낮아지는 표에서는 어려운 슬롯을 더 배정하고, 유형별 허용 난이도를 넘지 않는다."""
    from exam_gen.plan import calibrate
    from exam_gen.plan.coverage import largest_remainder
    from exam_gen.plan.slots import joint_allocation
    tab = calibrate.default_table()
    target = {"최상": 5, "상": 15, "중": 60, "하": 20}
    tc = largest_remainder(315, {"mcq": 50, "ox": 30, "short": 20})
    plan = calibrate.plan(315, target, tc, tab)
    assert sum(plan.values()) == 315
    assert plan["최상"] + plan["상"] > 315 * 0.20          # 보정 없이 20%보다 많이 배정
    assert plan["최상"] <= 315 * 0.2 + 1                   # 최상 슬롯 상한(시간)
    joint_allocation(tc, plan)                              # 유형별 허용 난이도 안에서 배정 가능
    assert plan["최상"] >= round(315 * target["최상"] / 100)   # 최상 슬롯은 목표 수만큼은 둔다
    assert plan["최상"] + plan["상"] <= 2 * 315 * (target["최상"] + target["상"]) / 100 + 1  # 보정 폭 상한
    e = calibrate.expected(plan, tab)
    assert e["상"] >= 315 * target["상"] / 100 * 0.9       # 상은 판정 기준으로 목표의 90% 이상
    # (중 슬롯의 '하' 판정은 슬롯 배정으로 메울 수 없다 — 판정 기준은 담당부서가 정한다)
    # 판정이 그대로인 표라면 목표 비율 그대로 배정
    same = {s: {a: 1.0 if a == s else 0.0 for a in calibrate.LEVELS} for s in calibrate.LEVELS}
    assert calibrate.plan(100, target, largest_remainder(100, {"mcq": 50, "ox": 30, "short": 20}), same) == \
        {"최상": 5, "상": 15, "중": 60, "하": 20}


def test_difficulty_policy_and_human_judgment(tmp_path, monkeypatch):
    """난이도 기준: 기본은 임시 기준안, 담당자가 확정하면 출제·검수 프롬프트에 들어간다. 담당자 판정 난이도가 AI 판정보다 우선."""
    from exam_gen import difficulty_policy as DP
    from exam_gen.llm.prompts import render
    from exam_gen.quality_report import actual_difficulty
    from exam_gen.validate import review_llm
    monkeypatch.setattr(DP, "PATH", tmp_path / "difficulty_policy.json")
    assert DP.load()["status"] == "임시" and "(적용)" in DP.rubric()["상"]
    lv = {**DP.rubric(), "상": "담당자 지침: 두 개념을 상황에 적용"}
    DP.save(lv, "확정", "담당자", "평가 지침")
    assert DP.load()["status"] == "확정" and DP.version().startswith("공통·확정")
    assert "담당자 지침: 두 개념을 상황에 적용" in render("generate_system.j2", rubric=DP.rubric())
    assert "담당자 지침: 두 개념을 상황에 적용" in render("review_system.j2", rubric=DP.rubric(), policy_status="담당자 확정 기준")
    it = {"slot": {"difficulty": "상"}, "difficulty": "상", "validation": {"G5": {"assessed_difficulty": "중"}, "warnings": []}}
    review_llm.relabel_difficulty(it)
    assert it["difficulty"] == "중" and actual_difficulty(it) == "중"
    it["human_difficulty"] = {"level": "상", "by": "담당자"}
    review_llm.relabel_difficulty(it)
    assert it["difficulty"] == "상" and actual_difficulty(it) == "상"
    it.pop("human_difficulty")
    review_llm.relabel_difficulty(it)
    assert it["difficulty"] == "중"


def test_chasi_detect_headings():
    """차시 자동 제안: 큰 제목·첫 줄의 '제N절'만 차례 번호로 잡고, '제1절을 마치며'·본문 참조·부록 반복은 무시한다."""
    from exam_gen.ingest.chasi_detect import detect

    def ln(t, h=11, margin=False):
        return {"text": t, "bbox": [0, 0, 100, h], "margin": margin}
    body = [ln("본문 문장입니다.") for _ in range(5)]
    pages = [
        {"g": 0, "lines": [ln("표지")]},
        {"g": 1, "lines": body + [ln("제1절 첫 단원", 16)]},
        {"g": 2, "lines": body + [ln("제3절에서 다룬다", 11)]},  # 본문 참조(작은 글씨·첫 줄 아님)
        {"g": 3, "lines": [ln("제1절을 마치며", 16)] + body},     # 조사가 붙은 맺음말
        {"g": 4, "lines": [ln("제2절 둘째 단원", 16)] + body},
        {"g": 5, "lines": [ln("제2절 머리말", 16, margin=True)] + body},
        {"g": 6, "lines": [ln("제1절 — 부록 반복", 16)] + body},  # 번호가 차례가 아님
    ]
    d = detect(pages)
    assert d["unit"] == "절" and [h["g"] for h in d["heads"]] == [1, 4]
    assert detect([{"g": 0, "lines": body}]) is None


def test_difficulty_policy_per_project(tmp_path, monkeypatch):
    """난이도 기준: 프로젝트 전용 기준이 있으면 그 프로젝트만 그것을 쓰고, 다른 프로젝트는 공통 기준. 지우면 공통으로 돌아간다."""
    from types import SimpleNamespace
    from exam_gen import difficulty_policy as DP
    monkeypatch.setattr(DP, "PATH", tmp_path / "common.json")
    a, b = SimpleNamespace(dir=tmp_path / "a"), SimpleNamespace(dir=tmp_path / "b")
    DP.save({**DP.default_levels(), "상": "공통 상 기준"}, "확정", "담당자")
    assert DP.load(a)["scope"] == "공통" and DP.rubric(a)["상"] == "공통 상 기준"
    DP.save({**DP.default_levels(), "상": "A과정 상 기준"}, "임시", project=a)
    assert DP.has_project_policy(a) and not DP.has_project_policy(b)
    assert DP.rubric(a)["상"] == "A과정 상 기준" and DP.rubric(b)["상"] == "공통 상 기준" and DP.rubric()["상"] == "공통 상 기준"
    assert DP.version(a).startswith("프로젝트·임시") and DP.label(b).startswith("공통 기준")
    assert DP.use_common(a) and not DP.has_project_policy(a) and DP.rubric(a)["상"] == "공통 상 기준"
    assert (tmp_path / "a" / "difficulty_policy.json.bak").exists()


def test_move_source_keeps_items_and_renumbers_chasi(tmp_path):
    """원고 순서 바꾸기: 문항의 페이지·근거 위치는 (원고, 원고 안 페이지) 기준으로 따라가고, 차시는 새 파일 순서대로 다시 매긴다."""
    import shutil
    from exam_gen.generate.generator import start_batch
    from exam_gen.llm.settings import LLMSettings
    from exam_gen.store import Project
    from pathlib import Path
    SAMPLE = Path(__file__).resolve().parents[1] / "input_17-43.pdf"
    a, b = tmp_path / "01_앞.pdf", tmp_path / "02_뒤.pdf"
    shutil.copyfile(SAMPLE, a)
    shutil.copyfile(SAMPLE, b)
    p = Project.create("원고 순서 시험")
    s1, s2 = p.add_source(a, a.name), p.add_source(b, b.name)
    for s in (s1, s2):
        p.set_page_map(s["id"], 17, "printed", True)
    assert [x["chasi"] for x in p.pages][0] == 1 and p.pages[-1]["chasi"] == 2
    opts = dict(criteria()["defaults"])
    first = [x["g"] for x in p.pages if x["src"] == s1["id"] and not x["excluded"]][:3]
    start_batch(p, LLMSettings(provider="mock", workers=1), build_slots(p.pages, {g: 1 for g in first}, opts), opts,
                background=False)
    before = {it["key"]: (p.pages[it["page"]]["src"], p.pages[it["page"]]["idx"]) for it in p.items()}
    assert before and all(src == s1["id"] for src, _ in before.values())
    assert p.move_source(s1["id"], 1) and not p.move_source(s1["id"], 1)  # 맨 뒤에서 더 내려갈 수 없다
    assert [s["id"] for s in p.meta["sources"]] == [s2["id"], s1["id"]]
    after = {it["key"]: (p.pages[it["page"]]["src"], p.pages[it["page"]]["idx"]) for it in p.items()}
    assert after == before  # 같은 원고·같은 페이지를 그대로 가리킨다
    assert p.pages[0]["src"] == s2["id"] and p.pages[0]["chasi"] == 1 and p.pages[-1]["chasi"] == 2
    assert all(it["chasi"] == p.pages[it["page"]]["chasi"] == 2 for it in p.items())


def test_version_and_update_backup(tmp_path, monkeypatch):
    """프로그램 버전: 주.부.수 형식, 문항 기록에 버전이 남고, 버전이 바뀐 뒤 첫 실행에서 모든 프로젝트를 한 번만 자동 백업한다."""
    import re
    from exam_gen import backup, ui
    from exam_gen import store as S
    from exam_gen.version import APP_VERSION, label
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION) and label().startswith(f"v{APP_VERSION}")
    state = {"app_version": "0.9.9"}
    monkeypatch.setattr(ui, "last_project", lambda: dict(state))
    monkeypatch.setattr(ui, "set_pref", lambda **kw: state.update(kw))
    monkeypatch.setattr(ui, "list_projects", lambda: ["a", "b"])
    monkeypatch.setattr(ui, "Project", lambda slug: slug)
    done = []
    monkeypatch.setattr(backup, "backup", lambda p, reason: done.append((p, reason)) or tmp_path / "x.zip")
    msg = ui.check_version_update()
    assert [d[0] for d in done] == ["a", "b"] and "0.9.9" in done[0][1] and APP_VERSION in done[0][1]
    assert state["app_version"] == APP_VERSION and "자동 백업" in msg
    assert ui.check_version_update() is None and len(done) == 2  # 같은 버전이면 다시 백업하지 않는다


def test_skip_reverify_override_flow(sample_project, tmp_path):
    """수정 후 AI 재검증 생략(사람 책임 승인): 고치면 승인이 막히고, 건너뛰기 승인은 무료 검사를 통과해야 하며 출력에 들어간다.
    다시 고치면 무효, 나중에 '생략한 AI 검증'을 통과하면 승인이 유지된다. 엑셀 Q열에 난이도가 보인다."""
    import openpyxl
    from exam_gen import integrity, review_ops
    from exam_gen.generate.generator import start_batch
    from exam_gen.llm.settings import LLMSettings
    from exam_gen.output import excel, numbering
    p = sample_project
    opts = dict(criteria()["defaults"])
    mock = LLMSettings(provider="mock", workers=1)
    b = start_batch(p, mock, build_slots(p.pages, allocate(p.pages, 6), opts), opts, background=False)
    it = next(x for x in p.items(batch=b) if x["status"] in ("auto_pass", "warn") and x["type"] == "mcq"
              and not integrity.needs_evidence_confirm(x))
    key = it["key"]
    it["explanation"] = it.get("explanation", "") + " (검수자 보완)"
    review_ops.save_edit(p, it, "해설 보완")
    ok, why = review_ops.approve(p, key, "검수자")
    assert not ok and integrity.AI_STALE in why                      # 고친 뒤에는 AI 재검증 전까지 승인 불가
    assert not review_ops.approve_without_reverify(p, key, "")[0]    # 검수자 이름 필수
    ok, why = review_ops.approve_without_reverify(p, key, "검수자")
    assert ok, why
    cur = p.item(key)
    assert cur["status"] == "approved" and integrity.is_exportable(cur) and cur["validation"]["llm_skipped"]
    assert any(w.startswith("수정 후 AI 재검증 생략") for w in cur["validation"]["warnings"])
    assert key in [x["key"] for x in review_ops.skipped_items(p)]
    assert key in [x["key"] for x in numbering.select(p.items(), scope="approved")]
    # 엑셀: Q열 난이도
    out = excel.write(p, p.items(), tmp_path / "q.xlsx", scope="approved")
    ws = openpyxl.load_workbook(out["path"]).worksheets[0]
    assert str(ws["Q2"].value).startswith("난이도") and ws["Q4"].value in ("최상", "상", "중", "하")
    # 나중에 AI 검증 통과 → 승인 유지
    r = review_ops.verify_skipped(p, mock, [key])
    cur = p.item(key)
    if r["ok"]:
        assert cur["status"] == "approved" and integrity.is_verified(cur) and not cur["validation"]["llm_skipped"]
    # 다시 고치면 사람 책임 승인도 무효
    cur["explanation"] = cur.get("explanation", "") + " 다시 보완"
    review_ops.save_edit(p, cur, "또 수정")
    assert not integrity.is_exportable(p.item(key))
    # 검수 남은 문항 수
    pc = review_ops.pending_counts(p.items())
    assert set(pc) == {"manual", "warn", "auto_pass"} and sum(pc.values()) >= 1
