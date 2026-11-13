"""출제 품질 자체 평가 — 출제 결과를 지침·유의사항 기준으로 자동 집계하고 항목별 등급을 매긴다.

대시보드 '품질 리포트' 화면과 CLI가 같은 계산을 쓴다.
  python -m exam_gen.quality_report <프로젝트> [<프로젝트> ...]

등급은 A(납품 수준) · B(경미한 보완) · C(보완 필요) · D(재출제 권장). 기준은 GRADE_RULES에 있다.
"""
from __future__ import annotations

import sys
from collections import Counter

from . import integrity
from .config import criteria
from .generate.choice_builder import shortcut_problems
from .plan.coverage import coverage_stats
from .store import Project
from .validate import rules

LEVELS = ["최상", "상", "중", "하"]
ACCEPTED = ("auto_pass", "warn", "approved")

# 항목: (설명, 지표 설명, [A 상한, B 상한, C 상한]) — 지표가 상한 이하이면 그 등급
GRADE_RULES = {
    "accuracy": ("정답 정확성", "검수에서 정답 오류를 지적받은 문항 비율", [0.0, 0.03, 0.08]),
    "dispute": ("이의 제기 여지", "학습자가 다른 답을 주장할 여지를 지적받은 문항 비율", [0.05, 0.15, 0.30]),
    "evidence": ("출처(근거)", "원문과 글자 단위로 일치하지 않는 근거 비율(L1·미매칭)", [0.05, 0.15, 0.30]),
    "coverage": ("원고 전반 출제", "문항이 없는 출제 대상 페이지 비율 — 문항 수가 페이지 수보다 적어 생기는 빈 페이지는 빼고 센다",
                 [0.0, 0.05, 0.15]),
    "format": ("형식·지침", "지침 형식 위반·경고가 있는 문항 비율", [0.05, 0.15, 0.30]),
    "discrimination": ("변별력(보기)", "검수 AI가 '정답 단서'로 지적한 보기·조합형 지름길 문항 비율 — 실제 응시 결과가 아니라 "
                       "경고 집계다('보완 권장' 경고는 제외, 구분 도입 전 출제분은 보기 경고 전체)", [0.10, 0.25, 0.40]),
    "difficulty": ("난이도 분포", "목표 대비 실제 난이도(검수 판정) 분포 차이(총변동거리)", [0.10, 0.20, 0.35]),
}
_FORMAT_PREFIXES = ("G3:",)
_ACCURACY_TAGS = ("G5[정답]",)
_DISPUTE_TAGS = ("G5[이의 제기 여지]",)
# 등급에 넣지 않는 참고 범주: 정답 선택에 영향이 없는 보기 다듬기 제안(Codex 문항 검수 2026-10-21)
EXTRA_CATS = {"polish": "보기 다듬기(참고)", "freshness": "시의성 확인(참고)", "repeat": "원리 반복(참고)"}


def actual_difficulty(it: dict) -> str:
    """실제 난이도 = 담당자 판정 > 검수(G5) 판정 > 표시 난이도."""
    lv = (it.get("human_difficulty") or {}).get("level") or (it["validation"].get("G5") or {}).get("assessed_difficulty")
    return lv if lv in LEVELS else it["difficulty"]


def _grade(value: float, cuts: list[float]) -> str:
    for g, c in zip("ABC", cuts):
        if value <= c + 1e-9:
            return g
    return "D"


def _issues(it: dict) -> dict[str, list[str]]:
    """문항 하나의 문제를 범주별로."""
    out: dict[str, list[str]] = {}
    warns = it["validation"].get("warnings", [])

    def add(cat, msg):
        out.setdefault(cat, []).append(msg)

    from .validate.freshness import PREFIX as _FRESH
    from .validate.pool_review import PREFIX as _REPEAT
    for w in warns:
        if w.startswith(_FRESH):
            add("freshness", w)
        elif w.startswith(_REPEAT):
            add("repeat", w)
        elif w.startswith(_ACCURACY_TAGS):
            add("accuracy", w)
        elif w.startswith(_DISPUTE_TAGS):
            add("dispute", w)
        elif w.startswith("G5[보기]"):
            add("polish" if w.removeprefix("G5[보기]").strip().startswith("보완 권장") else "discrimination", w)
        elif w.startswith(_FORMAT_PREFIXES) or w.startswith(("G5[오타·비문]", "G5[조건 누락]", "G5[해설]")):
            add("format", w)
    if it["type"] == "mcq" and not rules._ends_with_question(it["stem"]):  # G3와 같은 기준(사례 블록·단서 괄호 인정)
        add("format", "선다형 발문이 질문형이 아님")
    if any(rules._LEAK.search(rules._QUOTED.sub("", t or "")) for _, t in rules._texts(it)):
        add("format", "'원고'·쪽수·절 번호 노출")
    if rules._DEICTIC.search(it.get("stem", "")):
        add("format", "대상 없는 지시어")
    if it.get("statements"):
        correct = [s["label"] for s in it["statements"] if s.get("is_true")]
        d = [c.get("labels") or [] for i, c in enumerate(it["choices"]) if str(i + 1) != str(it["answer"])]
        for m in shortcut_problems(correct, d):
            add("discrimination", f"조합형 — {m}")
    lv = [(e.get("match") or {}).get("level") for e in it["evidence"] if e.get("quote")]
    if any(x != "L2" for x in lv):
        add("evidence", "원문과 글자 단위로 일치하지 않는 근거가 있음(L1·미매칭)")
    return out


def analyze(p: Project, batch: int | None = None) -> dict:
    items = [it for it in p.items() if batch is None or it.get("batch") == batch]
    ok = [it for it in items if it["status"] in ACCEPTED]
    n = len(ok) or 1
    issues = {it["key"]: _issues(it) for it in ok}

    def ratio(cat):
        return sum(1 for v in issues.values() if cat in v) / n

    batches = [b for b in p.batches() if batch is None or b["batch"] == batch]
    target = Counter(s["difficulty"] for b in batches for s in b["slots"] if not s.get("topup"))
    final = Counter(actual_difficulty(it) for it in ok)
    t_tot, f_tot = sum(target.values()) or 1, sum(final.values()) or 1
    tvd = sum(abs(target.get(lv, 0) / t_tot - final.get(lv, 0) / f_tot) for lv in LEVELS) / 2
    ev = [(e.get("match") or {}).get("level") for it in ok for e in it["evidence"] if e.get("quote")]
    elig = [pg for pg in p.pages if not pg["excluded"]]
    cov = coverage_stats(p.pages, Counter(it["page"] for it in ok))
    values = {
        "accuracy": ratio("accuracy"),
        "dispute": ratio("dispute"),
        "evidence": (1 - ev.count("L2") / len(ev)) if ev else 1.0,
        # 100문항을 243쪽에 내면 143쪽은 비는 것이 당연하다 — 문항 수로 피할 수 없는 빈 페이지는 등급에서 뺀다(2026-11-03)
        "coverage": max(0, len(cov["zero"]) - max(0, len(elig) - len(ok))) / (len(elig) or 1),
        "format": ratio("format"),
        "discrimination": ratio("discrimination"),
        "difficulty": tvd,
    }
    grades = {k: _grade(v, GRADE_RULES[k][2]) for k, v in values.items()}
    opts = {**criteria()["defaults"], **p.meta.get("options", {})}
    mcq = [it for it in ok if it["type"] == "mcq"]
    ox = [it for it in ok if it["type"] == "ox"]
    return {
        "project": p.meta["name"], "batch": batch,
        "counts": {"slots": sum(len(b["slots"]) for b in batches), "accepted": len(ok),
                   "manual": sum(1 for it in items if it["status"] == "manual"),
                   "approved": sum(1 for it in items if it["status"] == "approved"),
                   "warn": sum(1 for it in ok if it["status"] == "warn"),
                   "verified": sum(1 for it in ok if integrity.is_verified(it)),
                   "llm_skipped": sum(1 for it in ok if (it.get("validation") or {}).get("llm_skipped")),
                   "hold": sum(1 for it in items if it["status"] == "hold"),
                   "judgment": sum(1 for it in items if it["status"] == "approved" and integrity.has_judgment(it))},
        "values": values, "grades": grades,
        "difficulty": {"target": {lv: target.get(lv, 0) for lv in LEVELS}, "final": {lv: final.get(lv, 0) for lv in LEVELS},
                       # 재분류는 목표 달성이 아니다 — 목표보다 모자란 난이도를 따로 보여 준다(Codex 검수)
                       "shortfall": {lv: target.get(lv, 0) - final.get(lv, 0) for lv in LEVELS
                                     if target.get(lv, 0) > final.get(lv, 0)},
                       "target_pct": opts.get("difficulty_pct", {})},
        "types": {t: sum(1 for it in ok if it["type"] == t) for t in criteria()["type_order"]},
        "answer_pos": {k: sum(1 for it in mcq if str(it["answer"]) == k) for k in "1234"},
        "ox": {k: sum(1 for it in ox if it["answer"] == k) for k in "OX"},
        "polarity": {k: sum(1 for it in mcq if it.get("polarity") == k) for k in ("positive", "negative")},
        "evidence_levels": {k: ev.count(k) for k in ("L2", "L1")} | {"미매칭": sum(1 for x in ev if x is None)},
        "coverage": {"zero": [p.pages[g]["label"] for g in cov["zero"]],
                     "dense": [p.pages[g]["label"] for g in cov["dense"]],
                     "pages": len(elig)},
        "issues": {k: v for k, v in issues.items() if v},
        "items": {it["key"]: it for it in ok},
        "batch_summaries": [b.get("summary") or {} for b in batches],
    }


def main(argv=None):
    for slug in (argv or sys.argv[1:]):
        r = analyze(Project(slug))
        print(f"\n== {r['project']}  ({r['counts']})")
        for k, (name, desc, _) in GRADE_RULES.items():
            print(f"  {name:10} {r['grades'][k]}  {r['values'][k]:.0%}  — {desc}")
        print("  난이도 목표", r["difficulty"]["target"], "/ 실제", r["difficulty"]["final"],
              "/ 부족", r["difficulty"]["shortfall"] or "없음")
        print("  0문항 페이지", r["coverage"]["zero"], "· 문제 문항", len(r["issues"]))


if __name__ == "__main__":
    main()
