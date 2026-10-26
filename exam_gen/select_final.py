"""최종 문항 자동 선정 — 여유분까지 출제한 문항(예: 315개)에서 목표 수(예: 300)를 고른다. 토큰 0.

고르는 순서
1. 후보: 승인·자동통과·경고(수동확인·삭제·기출 제외). 같은 원리 묶음에서는 기본 1문항만.
2. 문항 점수(높을수록 먼저): 승인 > 자동통과 > 경고. 정답 단서·이의 제기·시의성 '정답 관련'·AI 검증 생략은 감점.
3. 목표 분포(유형 %, 실제 난이도 %, 차시별 분량 비례)를 넘지 않는 범위에서 점수 순으로 채우고,
   모자라면 분포를 조금 넘더라도 점수 순으로 채운다. 목표와 다른 점을 보고한다.
결과는 문항의 selected 표시로 남고, 출력 대상 '선정 문항만'에서 쓴다. 사람이 검수 화면에서 바꿀 수 있다.
"""
from __future__ import annotations

from collections import Counter

from .config import criteria
from .plan.coverage import largest_remainder
from .quality_report import actual_difficulty
from .validate import pool_review

CANDIDATE = ("approved", "auto_pass", "warn")


def _score(it: dict) -> float:
    s = {"approved": 3.0, "auto_pass": 2.0, "warn": 1.0}.get(it.get("status"), 0.0)
    for w in (it.get("validation") or {}).get("warnings", []):
        if "정답 단서" in w:
            s -= 1.5
        elif w.startswith("G5[이의 제기"):
            s -= 1.0
        elif w.startswith("시의성 확인") and "정답 관련" in w:
            s -= 0.5
        elif w.startswith("AI 검증 생략"):
            s -= 2.0
    return s


def select(project, target: int | None = None) -> dict:
    opts = {**criteria()["defaults"], **project.meta.get("options", {})}
    target = target or int(opts.get("total", 300))
    items = [it for it in project.items() if it.get("status") in CANDIDATE]
    # 같은 원리 묶음은 점수가 가장 높은 1문항만 후보로(나머지는 마지막 보충용)
    groups = (pool_review.last(project) or {}).get("groups", [])
    spare = set()
    by_key = {it["key"]: it for it in items}
    for g in groups:
        live = sorted((by_key[k] for k in g["keys"] if k in by_key), key=_score, reverse=True)
        spare |= {it["key"] for it in live[1:]}
    main = sorted((it for it in items if it["key"] not in spare), key=_score, reverse=True)
    extra = sorted((it for it in items if it["key"] in spare), key=_score, reverse=True)

    t_quota = largest_remainder(target, {t: v for t, v in opts["type_pct"].items()})
    d_quota = largest_remainder(target, dict(opts["difficulty_pct"]))
    chars = Counter()
    for p in project.pages:
        if not p["excluded"]:
            chars[p["chasi"]] += max(p["chars"], 1)
    c_quota = largest_remainder(target, dict(chars)) if chars else {}

    chosen, tc, dc, cc = [], Counter(), Counter(), Counter()

    def fits(it, slack):
        lv = actual_difficulty(it)
        return (tc[it["type"]] < t_quota.get(it["type"], 0) + slack and dc[lv] < d_quota.get(lv, 0) + slack
                and (not c_quota or cc[it.get("chasi")] < c_quota.get(it.get("chasi"), 0) + slack))

    def take(it):
        chosen.append(it)
        tc[it["type"]] += 1
        dc[actual_difficulty(it)] += 1
        cc[it.get("chasi")] += 1

    for pool in (main, extra):
        for slack in (0, 2, 10**6):  # 목표 분포 안 → 조금 넘게 → 남은 자리 채우기
            for it in pool:
                if len(chosen) >= target:
                    break
                if it in chosen or not fits(it, slack):
                    continue
                take(it)
    keys = {it["key"] for it in chosen}
    for it in items:
        want = it["key"] in keys
        if bool(it.get("selected")) != want:
            it["selected"] = want
            project.save_item(it, "final_select", {"selected": want})
    short = {lv: d_quota[lv] - dc[lv] for lv in d_quota if dc[lv] < d_quota[lv]}
    report = {"target": target, "selected": len(chosen), "candidates": len(items), "repeat_spare": len(spare),
              "type": {t: [tc[t], t_quota.get(t, 0)] for t in t_quota},
              "difficulty": {lv: [dc[lv], d_quota.get(lv, 0)] for lv in d_quota},
              "chasi": {c: [cc[c], c_quota.get(c, 0)] for c in c_quota},
              "short_difficulty": short, "lacking": max(0, target - len(chosen))}
    project.log("final_select", {k: report[k] for k in ("target", "selected", "lacking")})
    return report
