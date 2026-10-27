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
# 학습자는 원고를 볼 수 없다 — 문항·보기·해설에 원고·쪽수·절 번호를 노출하지 않는다
_LEAK = re.compile(r"원고|교안|\bp\.\s*\d|\d+\s*(?:쪽|페이지)|['‘]\s*\d+(?:\.\d+)+\.?\s")
# 문항 안에 가리키는 대상이 없는 지시어 (유의사항: 주체·대상·상황 누락 금지)
_DEICTIC = re.compile(r"(?:^|[\s\[(])(?:이|그|해당|위|앞)\s?(?:시기|단계|고객|과정|경우|방법|제도|항목|사례)(?=[의은는이가을를에도와과\s,.]|$)")
# '…것을 고른다.' 같은 서술형 발문 → '…것은?'으로 고칠 수 있는 형태
_STEM_SOURCE_PREFIX = re.compile(r"^(?:원고|교재)(?:에\s*따르면,?|에\s*따른|에\s*제시된|의|에서)\s*")
_STATEMENT_STEM = re.compile(r"(\S+?)(?:을|를)\s*(?:모두\s*)?(?:고른다|선택한다|고르시오|선택하시오|고르는\s*문항이다|찾는다|찾으시오)\.?$")


def _ends_with_question(stem: str) -> bool:
    """선다형 발문이 질문(…?)으로 끝나는가. 자격시험형 사례 문항의 두 가지 표준 형식도 인정한다:
    '…것은?' 뒤에 [사례]·<사례> 블록이 오는 경우, '…것은? (단, … 가정한다)'처럼 단서 괄호가 붙는 경우
    (최상 슬롯이 이 형식 때문에 4번 모두 탈락 — 확인 시험 2026-10-27)."""
    s = re.sub(r"\s*\(\s*단[,\s][^()]*\)\s*$", "", stem.rstrip())
    if s.endswith("?"):
        return True
    return bool(re.search(r"\?\s*\n\s*[\[<〈【]\s*(사례|상황|자료)", stem))


def _topic_particle(word: str) -> str:
    """받침 유무에 따라 '은/는'."""
    ch = word[-1] if word else ""
    if "가" <= ch <= "힣":
        return "은" if (ord(ch) - 0xAC00) % 28 else "는"
    return "은"


def _texts(item: dict) -> list[tuple[str, str]]:
    out = [("발문", item.get("stem", "")), ("해설", item.get("explanation", ""))]
    out += [(f"보기{i + 1}", c.get("text", "")) for i, c in enumerate(item.get("choices") or [])]
    out += [(f"진술{s['label']}", s.get("text", "")) for s in item.get("statements") or []]
    return out


_TRAIL_JOSA = re.compile(r"(?:으로|로|을|를|이|가|은|는|의|와|과|에)$")


def _variant_key(v: str) -> str:
    """허용 답안 비교용: 공백·끝 조사를 뺀 형태. 채점은 띄어쓰기를 무시하므로 이것만 다른 표현은 중복이다."""
    k = re.sub(r"[\s\-_]", "", v or "").lower()
    return _TRAIL_JOSA.sub("", k) if len(k) > 2 else k


def clean_variants(item: dict) -> None:
    """단답형 허용 답안에서 정답과 띄어쓰기·끝 조사만 다른 표현(예: '사후리뷰를')과 중복을 뺀다."""
    if item.get("type") != "short":
        return
    seen = {_variant_key(item.get("answer", ""))}
    out = []
    for v in item.get("short_variants") or []:
        k = _variant_key(v)
        if v and v.strip() and k not in seen:
            seen.add(k)
            out.append(v.strip())
    item["short_variants"] = out


def autofix(item: dict):
    clean_variants(item)
    for c in item.get("choices") or []:
        c["text"] = _PREFIX.sub("", c.get("text", "")).strip()
    for s in item.get("statements") or []:
        s["text"] = _PREFIX.sub("", s.get("text", "")).strip()
    item["stem"] = _STEM_SOURCE_PREFIX.sub("", (item.get("stem") or "").strip())
    if item.get("type") == "mcq" and not item["stem"].endswith("?"):
        # '…옳지 않은 것을 고른다.' / '…설명을 고르는 문항이다.' → '…옳지 않은 것은?' (지침·양식의 발문 형태)
        m = _STATEMENT_STEM.search(item["stem"])
        if m:
            word = m.group(1)
            item["stem"] = item["stem"][:m.start()] + word + _topic_particle(word) + "?"
    if item.get("type") == "short" and len(_BLANK.findall(item["stem"])) >= 2 and "공통" not in item["stem"]:
        # 같은 답이 들어가는 괄호가 둘 이상이면 '공통으로'를 밝힌다(실제 원고 시험 K00022)
        item["stem"] = re.sub(r"괄호\s*안에\s*들어갈", "괄호 안에 공통으로 들어갈", item["stem"], count=1)


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
        if len(_BLANK.findall(stem)) >= 2 and "공통" not in stem:
            fails.append("G3: 단답형 괄호가 둘 이상인데 '공통으로' 표시가 없음")
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
        if not _ends_with_question(stem):
            fails.append("G3: 선다형 발문은 '…것은?'처럼 물음표로 끝나는 질문이어야 함")

    for where, text in _texts(item):
        if _LEAK.search(_QUOTED.sub("", text or "")):
            fails.append(f"G3: {where}에 '원고'·쪽수·절 번호 노출 — 학습자는 원고를 볼 수 없음")
            break

    for where, text in [("발문", item.get("stem", ""))] + [(f"진술{s['label']}", s.get("text", ""))
                                                           for s in item.get("statements") or []]:
        m = _DEICTIC.search(text or "")
        if m:
            fails.append(f"G3: {where}의 '{m.group().strip(' [(')}'가 가리키는 대상이 문항 안에 없음 — 대상을 명시할 것")
            break

    if t == "ox" and item.get("answer") == "O":
        ns, ne = normalize(item.get("stem", "")), normalize(item.get("explanation", ""))
        if ns and (ne == ns or (ns in ne and len(ne) < len(ns) * 1.3)):
            fails.append("G3: 진위형 O 문항의 해설이 지문과 동일함")
        elif _word_overlap(item.get("stem", ""), item.get("explanation", "")) >= 0.75:
            fails.append("G3: 진위형 O 문항의 해설이 지문을 거의 반복함 — 판정과 그 이유를 쓸 것")

    if item.get("difficulty") in ("상", "최상") and t in ("ox", "short"):
        # 상·최상은 여러 구절을 연결해야 한다 — 서로 겹치지 않는 근거 2곳 이상 (자체 평가: 대부분 한 문장 대조형)
        spots = []
        for ev in item.get("evidence") or []:
            m = ev.get("match")
            if m and not any(m["g"] == g and set(m["lines"]) & ls for g, ls in spots):
                spots.append((m["g"], set(m["lines"])))
        if len(spots) < 2:
            fails.append("G3: 상·최상 문항은 서로 다른 구절의 근거가 2곳 이상 필요함(한 문장 대조로 풀림)")

    if t == "short":
        ans = item.get("answer", "")
        if len(re.sub(r"\s", "", ans)) > 10 or len(ans.split()) > 3:
            warns.append("G3: 단답형 정답이 단어 수준보다 김")
        # 정답어가 빈칸 밖 지문에 그대로 나오면 옮겨 쓰기만 해도 맞힌다 (Codex 실제 원고 검수 K00017 '출처')
        visible = re.sub(r"\s", "", _BLANK.sub("", item.get("stem", "")))
        for a in [ans, *(item.get("short_variants") or [])]:
            a = re.sub(r"\s", "", a or "")
            if len(a) >= 2 and a in visible:
                fails.append(f"G3: 단답형 정답어 '{a}'가 빈칸 밖 지문에 그대로 나옴 — 옮겨 쓰기만 해도 정답")
                break

    if len(item.get("explanation", "")) < 15:
        warns.append("G3: 해설이 너무 짧음")

    fails += _modality(item)
    from . import freshness
    warns += freshness.warnings(item)
    return fails, warns


def _word_overlap(a: str, b: str) -> float:
    """지문 어절 중 해설에 그대로 나오는 비율(어미·조사 차이를 줄이려고 앞 2글자로 비교)."""
    wa = {w[:2] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", a or "")}
    wb = {w[:2] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", b or "")}
    return len(wa & wb) / len(wa) if wa else 0.0


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
