"""G3 규칙 검사 — 지침·유의사항 중 코드로 판정할 수 있는 항목.

반환: (fails, warns). fails는 재출제 사유가 되고 warns는 검수 화면에 표시된다.
보기 앞 번호 기호처럼 의미 변화 없이 고칠 수 있는 것은 여기서 자동으로 고친다.
"""
from __future__ import annotations

import re

from ..config import criteria
from ..ingest.pdf_text import normalize

_POLITE = re.compile(r"(입니다|합니다|습니다|됩니다|십시오|세요)[.?!]?\s*$|(입니다|합니다|습니다)[.,]")
_PREFIX = re.compile(r"^\s*(?:[①②③④⑤]|\(?\d\)|\d[.)]|[ㄱㄴㄷㄹ][.)])\s*")
_BLANK = re.compile(r"\(\s{2,}\)|\(\s*_+\s*\)|（\s+）")
_NEG = re.compile(r"않은|아닌|틀린|거리가\s*먼|부적절|잘못된|옳지|해당하지")
_NOANSWER = re.compile(r"정답\s*없음|모두\s*정답|모두\s*옳다|모두\s*틀리다")
_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"|‘[^’]*’|「[^」]*」|『[^』]*』")
_PARTICLE = re.compile(r"\s*(?:으로|로서|로써|에서|에게|이란|이라|란|은|는|이|가|을|를|와|과|로|의|도|만|에)")


def _texts(item: dict) -> list[tuple[str, str]]:
    out = [("발문", item.get("stem", "")), ("해설", item.get("explanation", ""))]
    out += [(f"보기{i + 1}", c.get("text", "")) for i, c in enumerate(item.get("choices") or [])]
    out += [(f"진술{s['label']}", s.get("text", "")) for s in item.get("statements") or []]
    return out


def autofix(item: dict):
    for c in item.get("choices") or []:
        c["text"] = _PREFIX.sub("", c.get("text", "")).strip()
    for s in item.get("statements") or []:
        s["text"] = _PREFIX.sub("", s.get("text", "")).strip()
    item["stem"] = (item.get("stem") or "").strip()


def check(item: dict) -> tuple[list[str], list[str]]:
    c = criteria()
    fails, warns = [], []
    t = item["type"]

    for where, text in _texts(item):
        text = _QUOTED.sub("", text or "")  # 따옴표 안 인용(대화·사례 문장)은 문어체 검사에서 제외 (Codex C16)
        for sent in re.split(r"(?<=[.?!])\s+", text):
            if _POLITE.search(sent):
                fails.append(f"G3: {where} 문어체 위반('~입니다/~합니다' 금지)")
                break

    absolute = c["absolute_words"]
    targets = [("보기", ch.get("text", "")) for ch in item.get("choices") or []]
    targets += [("진술", s.get("text", "")) for s in item.get("statements") or []]
    if t == "ox":
        targets.append(("지문", item.get("stem", "")))
    for where, text in targets:
        hit = [w for w in absolute if re.search(rf"(^|\s){w}(\s|$|[은는이가을를도])", text)]
        if hit:
            warns.append(f"G3: {where}에 절대어 {hit} — 정답 단서가 될 수 있음")
            break

    if any(_NOANSWER.search(ch.get("text", "")) for ch in item.get("choices") or []):
        fails.append("G3: '정답 없음/모두 정답' 보기 사용 금지")

    if t == "short":
        stem = item.get("stem", "")
        if not _BLANK.search(stem):
            fails.append("G3: 단답형 괄호 빈칸 '(     )' 형식이 없음")
        for m in _BLANK.finditer(stem):
            # 빈칸 뒤 공백을 건너뛰고 조사로 시작하면 힌트가 된다. '(은)는'처럼 괄호 형식이면 허용 (Codex C16)
            if _PARTICLE.match(stem, m.end()):
                fails.append("G3: 단답형 괄호 뒤 조사가 정답 힌트가 됨 — '(은)는' 형식으로 쓸 것")
                break

    if t == "mcq":
        stem = item.get("stem", "")
        if item.get("statements"):
            if "모두" not in stem:
                fails.append("G3: 조합형 발문에 '모두' 단서가 없음")
        else:
            neg = bool(_NEG.search(stem))
            if item.get("polarity") == "negative" and not neg:
                fails.append("G3: 옳지 않은 것 고르기인데 발문에 부정 표현이 없음")
            if item.get("polarity") == "positive" and neg:
                fails.append("G3: 옳은 것 고르기인데 발문에 부정 표현이 있음")
            lens = [len(ch.get("text", "")) for ch in item.get("choices") or []]
            if lens and min(lens) > 0 and max(lens) / min(lens) > c["thresholds"]["choice_length_ratio_warn"]:
                warns.append("G3: 보기 길이 차이가 큼")
        if not stem.rstrip().endswith("?"):
            warns.append("G3: 선다형 발문이 물음표로 끝나지 않음")

    if t == "ox" and item.get("answer") == "O":
        ns, ne = normalize(item.get("stem", "")), normalize(item.get("explanation", ""))
        if ns and (ne == ns or (ns in ne and len(ne) < len(ns) * 1.3)):
            fails.append("G3: 진위형 O 문항의 해설이 지문과 동일함")

    if len(item.get("explanation", "")) < 15:
        warns.append("G3: 해설이 너무 짧음")

    fails += _modality(item)
    return fails, warns


def _modality(item: dict) -> list[str]:
    """참인 진술이 근거 원문의 양태(의무·금지·가능·권고)를 바꿨는지."""
    groups = criteria()["modality_groups"]

    def kinds(text):
        n = re.sub(r"\s", "", text or "")
        return {g for g, words in groups.items() if any(re.sub(r"\s", "", w) in n for w in words)}

    from .evidence import statement_text
    out = []
    for ev in item.get("evidence") or []:
        if not ev.get("is_true") or not ev.get("match"):
            continue
        src, stmt = kinds(ev.get("quote")), kinds(statement_text(item, str(ev.get("target"))))
        if src and stmt and not (src & stmt):
            out.append(f"G3: [{ev.get('target')}] 원문의 {sorted(src)} 표현을 {sorted(stmt)}로 바꿈(표현 정확도)")
    return out
