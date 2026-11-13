"""담당부서 피드백(2026-11-13) 반영 v1.2.0 회귀 테스트."""
import copy

import openpyxl

from exam_gen import integrity, review_ops
from exam_gen.config import criteria
from exam_gen.generate import generator as gen
from exam_gen.ingest import prior_items as P
from exam_gen.llm.settings import LLMSettings
from exam_gen.output import excel, numbering
from exam_gen.plan.coverage import allocate, repair_alloc
from exam_gen.plan.slots import build_slots
from exam_gen.store import Project
from exam_gen.validate import evidence, review_llm, rules

MOCK = LLMSettings(provider="mock", workers=1)


def _project(name):
    from conftest import SAMPLE
    p = Project.create(name)
    p.add_source(SAMPLE)
    p.set_page_map("s1", 17, "printed", True)
    return p


def _run(p, n=6, pages=(1, 2)):
    opts = dict(criteria()["defaults"])
    start = sum(len(b["slots"]) for b in p.batches()) + 1
    slots = build_slots(p.pages, allocate([x for x in p.pages if x["g"] in pages], n), opts, start_index=start, seed=start)
    return gen.start_batch(p, MOCK, slots, opts, background=False)


def _passed(p):
    return [it for it in p.items() if it["status"] in ("auto_pass", "warn")]


# ---------- 버그: 기출을 지운 뒤 출제하면 문항을 덮어씀 ----------
def test_keys_not_reused_after_prior_delete():
    p = _project("키 재사용 방지")
    P.save(p, [{"type": "ox", "stem": f"다른 과정 기출 {i}이다.", "answer": "O", "explanation": ""} for i in range(3)], "a.xlsx")
    _run(p)
    before = {it["key"]: it["stem"] for it in p.items()}
    p.delete_items([it["key"] for it in p.items(status=["prior"])][:1])  # P00001만 지움
    _run(p, pages=(3, 4))
    after = {it["key"]: it["stem"] for it in p.items()}
    assert all(after[k] == v for k, v in before.items())  # 이전 문항이 그대로 있다
    # 기출 키도 행 수가 아니라 가장 큰 번호 다음
    P.save(p, [{"type": "ox", "stem": "새 기출 1이다.", "answer": "O", "explanation": ""}], "b.xlsx")
    assert sorted(x["key"] for x in p.items(status=["prior"])) == ["P00002", "P00003", "P00004"]


def test_best_page_accepts_saved_choices(sample_project):
    it = {"stem": "은퇴 준비기 고객", "choices": [{"text": "자산 구조"}], "statements": [{"text": "부동산"}], "answer": 1}
    P.best_page(sample_project, it)  # 저장된 보기({"text": …})에서도 오류가 나지 않는다


# ---------- #1 검수자 판단으로 승인 ----------
def test_judgment_approval_overrides_block_and_is_exportable(tmp_path):
    p = _project("판단 승인")
    _run(p)
    it = _passed(p)[0]
    it["validation"]["fails"] = ["G2: [1] 근거 인용문을 원고에서 찾지 못함"]
    it["validation"].pop("verified", None)
    it["status"] = "manual"
    p.save_item(it)
    assert not review_ops.approve_by_judgment(p, it["key"], "")[0]  # 이름 필요
    ok, _ = review_ops.approve_by_judgment(p, it["key"], "검수자", "원고 표현을 쉽게 바꿈")
    cur = p.item(it["key"])
    assert ok and cur["status"] == "approved" and integrity.is_exportable(cur) and integrity.block_reasons(cur) == []
    assert cur["validation"]["judgment"]["by"] == "검수자"
    assert cur["key"] in {x["key"] for x in numbering.select(p.items(), scope="approved")}
    r = excel.write(p, p.items(), tmp_path / "j.xlsx", scope="approved")
    ws = openpyxl.load_workbook(r["path"]).worksheets[0]
    memo_col = excel.FIRST_HIDDEN + excel.HIDDEN_COLS.index("검증 메모")
    assert any("검수자 판단 승인" in str(ws.cell(row, memo_col).value or "") for row in range(4, 4 + r["count"]))
    # 내용을 고치면 판단 승인은 풀린다
    cur["explanation"] += " 수정"
    review_ops.save_edit(p, cur, "")
    assert not integrity.is_exportable(p.item(cur["key"]))


# ---------- #3 단답형 근거 대조·난이도 차이 ----------
def test_short_answer_variant_in_quote_passes(sample_project):
    q = "자산 구조는 부동산(주택 중심) 비중이 크며"
    item = {"type": "short", "stem": "다음 괄호 안에 들어갈 용어를 작성하시오. [(     )]", "answer": "주택 비중",
            "short_variants": ["부동산"], "page": 1, "choices": [], "statements": [],
            "evidence": [{"target": "정답", "quote": q, "is_true": True}]}
    fails, _ = evidence.check_item(sample_project.pages, item)
    assert not any("단답형" in f for f in fails) and not any("근거 인용문이 없음" in f for f in fails)
    item["short_variants"] = []
    fails, _ = evidence.check_item(sample_project.pages, copy.deepcopy(item))
    assert any("주택 비중" in f and "인용문:" in f for f in fails)  # 무엇이 안 맞는지 보여 준다


def test_difficulty_gap_is_warning_not_fail():
    assert review_llm.is_difficulty_only("G5[난이도] 표시 '상'인데 실제 '하' 수준 — 슬롯 난이도에 맞게 다시 출제")
    assert not review_llm.is_difficulty_only("G5: 실제 난이도 판정 누락")


def test_refresh_rules_releases_difficulty_only_manual():
    p = _project("난이도 실패 풀기")
    _run(p)
    it = _passed(p)[0]
    it["validation"]["fails"] = ["G5[난이도] 표시 '상'인데 실제 '하' 수준 — 슬롯 난이도에 맞게 다시 출제"]
    it["validation"].pop("verified", None)
    it["status"] = "manual"
    p.save_item(it)
    r = review_ops.refresh_rules(p)
    cur = p.item(it["key"])
    assert r["released"] >= 1 and cur["status"] in ("auto_pass", "warn") and integrity.is_verified(cur)
    assert not cur["validation"]["fails"]
    assert review_ops.approve(p, cur["key"], "검수자")[0]


# ---------- #9 같은 사유 한 번만 ----------
def test_duplicate_fail_messages_collapsed(sample_project):
    q = "자산 구조는 부동산(주택 중심) 비중이 크며"
    item = {"type": "ox", "stem": "은퇴 준비기 고객의 자산 구조는 부동산 비중이 99% 이상이다.", "answer": "O", "page": 1,
            "choices": [], "statements": [],
            "evidence": [{"target": "OX", "quote": q, "is_true": True}, {"target": "OX", "quote": q, "is_true": True}]}
    fails, _ = evidence.check_item(sample_project.pages, item)
    assert len(fails) == len(set(fails)) and sum("99" in f for f in fails) == 1


# ---------- #10 원고에 있는 '1페이지' 같은 용어 ----------
def test_leak_rule_allows_term_present_in_source():
    item = {"type": "ox", "stem": "관리자는 핵심 내용을 1페이지 보고서로 정리한다.", "answer": "O", "explanation": "",
            "choices": [], "statements": []}
    assert any("쪽수" in f for f in rules.check(item)[0])
    assert not any("쪽수" in f for f in rules.check(item, "관리자에게 1페이지 보고서로 요약한다")[0])
    assert any("쪽수" in f for f in rules.check({**item, "stem": "원고 3쪽에 따르면 옳다."}, "관리자 1페이지 보고서")[0])


# ---------- #5 출력 엑셀을 같은 프로젝트 기출로 다시 올림 ----------
def test_own_export_reuploaded_as_prior_is_skipped(tmp_path):
    p = _project("자기 문항 기출")
    _run(p)
    for it in _passed(p):
        review_ops.approve(p, it["key"], "검수자")
    r = excel.write(p, p.items(), tmp_path / "own.xlsx", scope="all")
    got = P.parse_template_excel(r["path"])
    assert got and all(g["source_key"].startswith("K") for g in got)
    new, own = P.split_self_copies(p, got)
    assert not new and len(own) == len(got)
    assert P.save(p, got, "own.xlsx") == 0
    # 예전 버전에서 이미 등록된 자기 복사본은 찾아서 지울 수 있다
    own_item = _passed(p)[0] if _passed(p) else p.items()[0]
    p.save_item({"key": "P00001", "status": "prior", "type": own_item["type"], "stem": own_item["stem"],
                 "answer": own_item["answer"], "choices": [], "statements": [], "evidence": [], "validation": {}})
    assert [x["key"] for x in P.registered_self_copies(p)] == ["P00001"]


# ---------- #7 보류 ----------
def test_hold_status_flow(tmp_path):
    p = _project("보류")
    _run(p)
    it = _passed(p)[0]
    review_ops.set_hold(p, it["key"], "중복 의심", "다른 문항과 비슷함", "검수자")
    cur = p.item(it["key"])
    assert cur["status"] == "hold" and review_ops.pending_counts(p.items())["hold"] == 1
    sel = numbering.select(p.items(), scope="approved")
    assert cur["key"] in {x["key"] for x in sel}  # 보류는 출력 대상과 상관없이 포함(빼려면 화면에서 끔)
    r = excel.write(p, p.items(), tmp_path / "h.xlsx", scope="approved")
    ws = openpyxl.load_workbook(r["path"]).worksheets[0]
    st_col = excel.FIRST_HIDDEN + excel.HIDDEN_COLS.index("상태")
    assert any(ws.cell(row, st_col).value == "보류" for row in range(4, 4 + r["count"]))
    assert "보류" in ws["A1"].value
    review_ops.set_status(p, it["key"], "restore", "")
    cur = p.item(it["key"])
    assert cur["status"] != "hold" and "hold" not in cur


# ---------- #6 페이지 재출제 자동 배정 ----------
def test_repair_alloc_targets_empty_pages_first(sample_project):
    pages = sample_project.pages
    elig = [x["g"] for x in pages if not x["excluded"]]
    existing = {g: 2 for g in elig[:3]}
    a = repair_alloc(pages, 4, existing, 0)
    assert sum(a.values()) == 4 and not set(a) & set(elig[:3])
    assert repair_alloc(pages, 3, {g: 1 for g in elig}, 0) == {}
