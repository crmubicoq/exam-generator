"""담당부서 2차 피드백(2026-11-24) 회귀 테스트: 수정 메모 보관, 메모만 저장 시 상태 유지, 출력 문항ID 순."""
import copy

import openpyxl

from exam_gen import integrity, review_ops
from exam_gen.config import criteria
from exam_gen.generate import generator as gen
from exam_gen.llm.settings import LLMSettings
from exam_gen.output import excel, numbering
from exam_gen.plan.coverage import allocate
from exam_gen.plan.slots import build_slots
from exam_gen.store import Project

MOCK = LLMSettings(provider="mock", workers=1)


def _project(name, n=6):
    from conftest import SAMPLE
    p = Project.create(name)
    p.add_source(SAMPLE)
    p.set_page_map("s1", 17, "printed", True)
    opts = dict(criteria()["defaults"])
    slots = build_slots(p.pages, allocate([x for x in p.pages if x["g"] in (1, 2, 3)], n), opts, start_index=1, seed=1)
    gen.start_batch(p, MOCK, slots, opts, background=False)
    return p


# ---------- #1 수정 메모: 문항에 보관, 메모만 저장하면 상태 그대로 ----------
def test_memo_kept_and_memo_only_save_keeps_status():
    p = _project("메모 보관")
    it = next(x for x in p.items() if x["status"] == "auto_pass")
    r = review_ops.save_edit(p, copy.deepcopy(it), "test 메모")
    cur = p.item(it["key"])
    assert r["_memo_only"] and cur["status"] == "auto_pass" and integrity.is_verified(cur)
    assert cur["review_note"]["text"] == "test 메모"  # 다른 대기열에 다녀와도(다시 읽어도) 남아 있다
    assert cur["history"][-1]["action"] == "memo"
    # 비우고 저장하면 지운다
    review_ops.save_edit(p, copy.deepcopy(cur), "")
    assert "review_note" not in p.item(it["key"])


def test_memo_only_save_keeps_ai_fail_reason():
    p = _project("메모 AI 실패 유지")
    it = next(x for x in p.items() if x["status"] in ("auto_pass", "warn"))
    it["validation"]["fails"] = ["G4: 독립 풀이 정답 2 ≠ 출제 정답"]
    it["status"] = "manual"
    p.save_item(it)
    review_ops.save_edit(p, copy.deepcopy(p.item(it["key"])), "확인 중")
    cur = p.item(it["key"])
    assert cur["status"] == "manual" and any(f.startswith("G4") for f in cur["validation"]["fails"])


def test_content_edit_still_needs_reverify_and_keeps_memo():
    p = _project("내용 수정")
    it = next(x for x in p.items() if x["status"] == "auto_pass")
    it["explanation"] = (it.get("explanation") or "") + " 보충."
    r = review_ops.save_edit(p, it, "해설 보충")
    cur = p.item(it["key"])
    assert not r.get("_memo_only") and cur["validation"].get("llm_stale") and cur["review_note"]["text"] == "해설 보충"


# ---------- #3 출력: 문항ID 오름차순(기본) ----------
def test_numbering_order_key_default_and_form_option(tmp_path):
    p = _project("문항ID 순", n=8)
    items = [it for it in p.items() if it["status"] != "deleted"]
    by_key = numbering.number(items, "continuous")
    assert [x["key"] for x in by_key] == sorted(x["key"] for x in items)
    assert [x["no"] for x in by_key] == list(range(1, len(items) + 1))
    form = numbering.number(items, "continuous", "form")
    order = criteria()["type_order"]
    assert [(x.get("chasi") or 0, order.index(x["type"])) for x in form] == sorted(
        (x.get("chasi") or 0, order.index(x["type"])) for x in form)
    # 엑셀 R열 문항ID가 위에서부터 오름차순
    r = excel.write(p, p.items(), tmp_path / "o.xlsx", scope="all")
    ws = openpyxl.load_workbook(r["path"]).worksheets[0]
    ids = [ws.cell(row, excel.FIRST_EXTRA).value for row in range(4, 4 + r["count"])]
    assert ids == sorted(ids)
    # 양식 순으로 바꾸면 그 순서로
    p.update_meta(options={**p.meta.get("options", {}), "order_by": "form"})
    r2 = excel.write(p, p.items(), tmp_path / "f.xlsx", scope="all")
    ws2 = openpyxl.load_workbook(r2["path"]).worksheets[0]
    ids2 = [ws2.cell(row, excel.FIRST_EXTRA).value for row in range(4, 4 + r2["count"])]
    assert ids2 == [x["key"] for x in numbering.number(numbering.select(p.items(), scope="all"), "continuous", "form")]
