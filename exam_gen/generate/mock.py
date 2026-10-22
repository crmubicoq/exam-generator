"""데모(모의) 공급자 — LLM 없이 원고 문장으로 문항을 만든다.

화면 흐름·관문·출력물 확인용이다. 문항 품질은 실제 LLM과 비교할 수 없으며 납품에 쓰지 않는다.
- 생성: 원고 문장을 그대로 쓰거나(참) 반대말로 바꿔(거짓) 문항을 만든다.
- 풀이(G4): 진술이 원고에 그대로 있으면 참으로 판정한다(정답 키를 보지 않음).
- 검수(G5): 모두 pass.
"""
from __future__ import annotations

import random
import re

from ..ingest.pdf_text import normalize

_SWAPS = [("높아", "낮아"), ("높은", "낮은"), ("증가", "감소"), ("크다", "작다"), ("큰", "작은"), ("있다", "없다"),
          ("강화", "약화"), ("확대", "축소"), ("필요하다", "불필요하다"), ("중요", "사소"), ("긍정", "부정"),
          ("안정", "불안정"), ("최소화", "최대화"), ("쉬운", "어려운"), ("많", "적"), ("이전", "이후")]
_PARTICLES = "은는이가을를와과의에도로"


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s*\n\s*", " ", text)
    out = []
    for s in re.split(r"(?<=다\.)\s+", flat):
        s = s.strip()
        if 25 <= len(s) <= 110 and s.endswith("다.") and "?" not in s and not re.match(r"^[\d(①-⑩∙·•-]", s):
            out.append(s)
    return out


def _alter(s: str) -> tuple[str, str] | None:
    for a, b in _SWAPS:
        if a in s:
            return s.replace(a, b, 1), f"'{a}'를 '{b}'로 바꾼 함정"
        if b in s:
            return s.replace(b, a, 1), f"'{b}'를 '{a}'로 바꾼 함정"
    m = re.search(r"\d+", s)
    if m:
        n = int(m.group())
        return s[:m.start()] + str(n * 3 + 7) + s[m.end():], f"수치 {n}을 바꾼 함정"
    return None


def respond(task: str, ctx: dict) -> dict:
    return {"generate": _generate, "solve": _solve, "review": _review}[task](ctx)


def _generate(ctx: dict) -> dict:
    p, g, slots = ctx["project"], ctx["page"], ctx["slots"]
    label = p.pages[g]["label"]
    sents = _sentences(p.page_text(g)) or [p.page_text(g)[:80].strip() + "다."]
    alterable = [s for s in sents if _alter(s)]
    items = []
    for s in slots:
        rng = random.Random(f"{s['sid']}-{len(ctx.get('feedback', {}).get(s['sid'], []))}")
        items.append(_make(s, sents, alterable or sents, rng, label))
    return {"items": items}


def _ev(target, quote, label, is_true, note=""):
    return {"target": target, "quote": quote, "page_label": label, "is_true": is_true, "trap_note": note}


def _make(s, sents, alterable, rng, label):
    base = {"slot_id": s["sid"], "statements": [], "choices": [], "short_variants": [],
            "difficulty_basis": f"{s['difficulty']} — 데모 생성"}
    if s["type"] == "ox":
        src = rng.choice(alterable if s["answer"] == "X" else sents)
        if s["answer"] == "X" and _alter(src):
            stem, note = _alter(src)
            expl = f"틀린 설명이다. 학습 내용에 따르면 {src[:-1]}는 것이 맞다."
            ev = _ev("OX", src, label, False, note)
        else:
            stem, expl = src, "올바른 설명이다. 이 과정에서 학습한 내용과 일치한다."
            ev = _ev("OX", src, label, True)
            s = {**s, "answer": "O"}
        return {**base, "key_point": src[:40], "stem": stem, "answer": s["answer"], "explanation": expl, "evidence": [ev]}

    if s["type"] == "short":
        src = rng.choice(sents)
        words = [w for w in re.findall(r"[가-힣]{3,6}", src) if not w.endswith(("한다", "있다", "이다", "된다"))]
        term = rng.choice(words) if words else src.split()[0]
        i = src.find(term)
        after = src[i + len(term):i + len(term) + 1]
        blank = "(     )"
        if after and after in _PARTICLES:
            pmap = {"은": "(은)는", "는": "(은)는", "이": "(이)가", "가": "(이)가", "을": "(을)를", "를": "(을)를",
                    "와": "(와)과", "과": "(와)과"}
            stem_s = src[:i] + blank + pmap.get(after, after) + src[i + len(term) + 1:]
        else:
            stem_s = src[:i] + blank + src[i + len(term):]
        return {**base, "key_point": f"용어 {term}", "stem": f"다음 괄호 안에 들어갈 용어를 작성하시오. [{stem_s}]",
                "answer": term, "explanation": f"정답은 '{term}'이다. 학습 내용에서 해당 개념을 이 용어로 설명한다.",
                "evidence": [_ev("A", src, label, True)]}

    if s.get("combo"):
        k = s["combo_true"]
        picks = rng.sample(sents, min(4, len(sents))) if len(sents) >= 4 else [rng.choice(sents) for _ in range(4)]
        truth = [True] * k + [False] * (4 - k)
        rng.shuffle(truth)
        st, evs = [], []
        for lb, src, t in zip("ㄱㄴㄷㄹ", picks, truth):
            alt = _alter(src)
            if not t and alt:
                st.append({"label": lb, "text": alt[0], "is_true": False})
                evs.append(_ev(lb, src, label, False, alt[1]))
            else:
                st.append({"label": lb, "text": src, "is_true": True})
                evs.append(_ev(lb, src, label, True))
        return {**base, "key_point": picks[0][:40], "stem": "다음 중 학습 내용에 관한 설명으로 옳은 것을 모두 고른 것은?",
                "statements": st, "answer": "", "explanation": "참인 진술은 학습 내용과 일치하고, 나머지는 핵심 표현을 바꾼 것이다.",
                "evidence": evs}

    pos = int(s["answer"])
    positive = s.get("polarity") == "positive"
    pool = rng.sample(sents, min(4, len(sents))) if len(sents) >= 4 else [rng.choice(sents) for _ in range(4)]
    choices, evs = [], []
    for i, src in enumerate(pool, 1):
        want_true = (i == pos) if positive else (i != pos)
        alt = _alter(src)
        if not want_true and alt:
            choices.append({"text": alt[0], "is_true": False})
            evs.append(_ev(str(i), src, label, False, alt[1]))
        else:
            choices.append({"text": src, "is_true": True})
            evs.append(_ev(str(i), src, label, True))
    stem = "다음 중 학습 내용에 관한 설명으로 옳은 것은?" if positive else "다음 중 학습 내용에 관한 설명으로 옳지 않은 것은?"
    return {**base, "key_point": pool[pos - 1][:40], "stem": stem, "choices": choices, "answer": str(pos),
            "explanation": f"정답은 {pos}번이다. 학습 내용과 {'일치한다' if positive else '다르다'}. 올바른 내용은 다음과 같다. {pool[pos - 1]}",
            "evidence": evs}


def _solve(ctx: dict) -> dict:
    p, items = ctx["project"], ctx["items"]
    corpus = normalize(" ".join(p.page_text(g) for g in range(len(p.pages))))
    results = []
    for i, it in zip(ctx.get("ids") or range(1, len(items) + 1), items):
        truth = lambda txt: normalize(txt) in corpus  # noqa: E731
        if it["type"] == "ox":
            ans = "O" if truth(it["stem"]) else "X"
            results.append({"id": str(i), "answer": ans, "judgments": [{"target": "OX", "is_true": ans == "O", "reason": ""}],
                            "unique": True, "solvable": True, "note": ""})
        elif it["type"] == "short":
            results.append({"id": str(i), "answer": it["answer"], "judgments": [], "unique": True, "solvable": True, "note": ""})
        elif it.get("statements"):
            js = [{"target": s["label"], "is_true": truth(s["text"]), "reason": ""} for s in it["statements"]]
            true_set = [j["target"] for j in js if j["is_true"]]
            ans = next((str(k) for k, c in enumerate(it["choices"], 1) if c.get("labels") == true_set), "0")
            results.append({"id": str(i), "answer": ans, "judgments": js, "unique": True, "solvable": True, "note": ""})
        else:
            js = [{"target": str(k), "is_true": truth(c["text"]), "reason": ""} for k, c in enumerate(it["choices"], 1)]
            positive = "않은" not in it["stem"]
            pick = [j["target"] for j in js if j["is_true"] == positive]
            results.append({"id": str(i), "answer": pick[0] if pick else "0", "judgments": js,
                            "unique": len(pick) == 1, "solvable": True, "note": ""})
    return {"results": results}


def _review(ctx: dict) -> dict:
    from ..validate.schema import REVIEW_CHECKS, false_targets
    return {"results": [{"id": str(i), "checks": [{"id": c, "result": "pass", "note": ""} for c in REVIEW_CHECKS],
                         "traps": [{"target": t, "verdict": "contradiction", "conflict": "데모"} for t in false_targets(it)],
                         "assessed_difficulty": it["difficulty"], "accept_variants": [], "suggestion": ""}
                        for i, it in zip(ctx.get("ids") or range(1, len(ctx["items"]) + 1), ctx["items"])]}
