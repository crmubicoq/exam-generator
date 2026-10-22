"""시의성 표시 — 시간이 지나면 바뀔 수 있는 정보(기관명·법령·금액 기준·전화번호·날짜)를 문항에서 찾아 표시한다.

외부 확인(웹 검색)은 하지 않는다. 원고가 낡았을 수 있다는 사실을 검수자에게 알리는 경고만 만든다.
정답이 그 정보에 달려 있으면 '정답 관련'으로 따로 표시한다. 탈락시키지는 않는다(원고 기준으로는 맞는 문항이므로).
"""
from __future__ import annotations

import re

PREFIX = "시의성 확인:"

# 자주 개편되는 중앙행정기관·공공기관(약칭 포함). 목록에 없는 기관은 아래 접미사 규칙으로 찾는다
_AGENCIES = ["과학기술정보통신부", "과기정통부", "금융위원회", "금융위", "금융감독원", "금감원", "경찰청", "국가정보원",
             "한국인터넷진흥원", "KISA", "개인정보보호위원회", "방송통신위원회", "방통위", "행정안전부", "행안부",
             "국립과학수사연구원", "국과수", "법제처", "농림축산식품부", "농식품부", "보건복지부", "고용노동부", "기획재정부",
             "검찰청", "대검찰청", "법무부", "통계청", "중소벤처기업부", "국민권익위원회", "국토교통부", "교육부"]
_AGENCY = re.compile("|".join(map(re.escape, sorted(_AGENCIES, key=len, reverse=True)))
                     + r"|[가-힣]{2,}(?:위원회|진흥원|연구원|공단)")
# '○○법상/에 따라' 형태의 법령 약칭. 방법·수법·불법처럼 '법'으로 끝나는 일반어는 제외
_NOT_LAW = ("방법", "수법", "기법", "불법", "위법", "적법", "합법", "편법", "문법", "해법", "화법", "기술법", "사용법", "활용법", "대응법", "작성법")
_LAW = re.compile(r"「[^」]{2,60}」|[가-힣]{2,}(?:특별법|기본법|시행령|시행규칙)|제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?"
                  r"|[가-힣]{3,}법(?=상|에\s*따|령|\s*제\s*\d)")
_MONEY = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:천|만|억)?\s*원(?:\s*(?:이하|이상|미만|초과))?")
_PHONE = re.compile(r"(?<![\d-])(?:112|118|1332|1301|1394|182)(?![\d-])|\d{2,4}-\d{3,4}-\d{4}|\d{4}-\d{4}")
_DATE = re.compile(r"20\d\d\s*[.년]\s*\d{1,2}\s*[.월]?(?:\s*\d{1,2}\s*[.일]?)?")
KINDS = [("기관", _AGENCY), ("법령", _LAW), ("금액", _MONEY), ("전화번호", _PHONE), ("날짜", _DATE)]


def find(text: str) -> list[tuple[str, str]]:
    out = []
    for kind, rx in KINDS:
        for m in rx.finditer(text or ""):
            v = m.group(0).strip()
            if kind == "법령" and v.endswith(_NOT_LAW):
                continue
            if (kind, v) not in out:
                out.append((kind, v))
    return out


def _answer_texts(item: dict) -> list[str]:
    """정답 판정이 달린 부분: 진위형 지문, 단답형 정답, 선다형 정답 보기, 조합형 거짓 진술."""
    t = item.get("type")
    if t == "ox":
        return [item.get("stem", "")]
    if t == "short":
        return [item.get("answer", "")]
    if item.get("statements"):
        return [s.get("text", "") for s in item["statements"]]
    try:
        return [item["choices"][int(item.get("answer")) - 1]["text"]]
    except (ValueError, IndexError, KeyError, TypeError):
        return []


def warnings(item: dict) -> list[str]:
    texts = [item.get("stem", ""), item.get("answer", "") if item.get("type") == "short" else "",
             *[c.get("text", "") for c in item.get("choices") or []],
             *[s.get("text", "") for s in item.get("statements") or []]]
    found = find("\n".join(texts))
    if not found:
        return []
    key = {v for k, v in find("\n".join(_answer_texts(item)))}
    on_answer = [f"{k} {v}" for k, v in found if v in key]
    other = [f"{k} {v}" for k, v in found if v not in key]
    msg = PREFIX + " 개편·개정으로 바뀔 수 있는 정보 — "
    if on_answer:
        msg += "정답 관련: " + ", ".join(on_answer[:6])
    if other:
        msg += ("; " if on_answer else "") + "기타: " + ", ".join(other[:6])
    return [msg + " (원고 발행 이후 바뀌었는지 확인)"]
