"""출제 결과 품질 집계 — 자체 평가(2026-10-21) 항목을 자동으로 센다. 프로젝트·배치 간 비교용.

  python -m exam_gen.quality_report <프로젝트> [<프로젝트> ...]
"""
from __future__ import annotations

import re
import sys
from collections import Counter

from . import integrity
from .generate.choice_builder import shortcut_problems
from .plan.coverage import coverage_stats
from .store import Project
from .validate import rules

LEVELS = ["최상", "상", "중", "하"]


def measure(p: Project) -> dict:
    items = p.items()
    ok = [it for it in items if it["status"] in ("auto_pass", "warn", "approved")]
    mcq = [it for it in ok if it["type"] == "mcq"]
    leak = [it for it in ok if any(rules._LEAK.search(rules._QUOTED.sub("", t or "")) for _, t in rules._texts(it))]
    deictic = [it for it in ok if rules._DEICTIC.search(it.get("stem", ""))]
    nonq = [it for it in mcq if not it["stem"].rstrip().endswith("?")]
    shortcut = []
    for it in mcq:
        if it.get("statements"):
            correct = [s["label"] for s in it["statements"] if s.get("is_true")]
            d = [c.get("labels") or [] for i, c in enumerate(it["choices"]) if str(i + 1) != str(it["answer"])]
            if shortcut_problems(correct, d):
                shortcut.append(it)
    g5 = [(it.get("difficulty_target") or it["slot"]["difficulty"], (it["validation"].get("G5") or {}).get("assessed_difficulty"))
          for it in ok]
    mismatch = sum(1 for t, a in g5 if a and a != t)
    choices_warn = [it for it in ok if any(w.startswith("G5[보기]") for w in it["validation"].get("warnings", []))]
    target = Counter(s["difficulty"] for b in p.batches() for s in b["slots"] if not s.get("topup"))
    final = Counter(it["difficulty"] for it in ok)
    ev = [(e.get("match") or {}).get("level") for it in ok for e in it["evidence"] if e.get("quote")]
    cov = coverage_stats(p.pages, Counter(it["page"] for it in ok))
    return {
        "project": p.meta["name"], "slots": sum(len(b["slots"]) for b in p.batches()),
        "accepted": len(ok), "manual": sum(1 for it in items if it["status"] == "manual"),
        "warn": sum(1 for it in ok if it["status"] == "warn"),
        "mcq_not_question": f"{len(nonq)}/{len(mcq)}", "leak_원고": len(leak), "deictic": len(deictic),
        "combo_shortcut": len(shortcut), "g5_choices_warn": len(choices_warn),
        "difficulty_mismatch(목표≠판정)": f"{mismatch}/{len(ok)}",
        "difficulty_target": {lv: target.get(lv, 0) for lv in LEVELS},
        "difficulty_final": {lv: final.get(lv, 0) for lv in LEVELS},
        "difficulty_G5판정": {lv: sum(1 for _, x in g5 if x == lv) for lv in LEVELS},
        "evidence_L2": f"{ev.count('L2')}/{len(ev)}", "zero_pages": [p.pages[g]["label"] for g in cov["zero"]],
        "verified": sum(1 for it in ok if integrity.is_verified(it)),
    }


def main(argv=None):
    for slug in (argv or sys.argv[1:]):
        r = measure(Project(slug))
        print(f"\n== {r.pop('project')}")
        for k, v in r.items():
            print(f"  {k:28} {v}")


if __name__ == "__main__":
    main()
