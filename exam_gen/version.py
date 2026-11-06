"""프로그램 버전 — 담당부서 실사용(2026-11-09 주) 시작 버전을 1.0.0으로 정한다.

번호 규칙(주.부.수):
- 수(1.0.x): 버그·문구·작은 화면 수정
- 부(1.x.0): 기능 추가·화면 구성 변경
- 주(x.0.0): 데이터 구조가 바뀌어 옮기기가 필요하거나 서버형 전환처럼 큰 변화
프롬프트 버전(config.PROMPT_VERSION)과 난이도 기준 버전은 따로 관리한다(바뀌는 이유가 다르다).
배포할 때: 이 파일의 버전·날짜를 올리고 → docs/변경내역.md에 적고 → 커밋에 git 태그(v1.0.0)를 붙이고 → 패키지를 만든다.
"""
from __future__ import annotations

APP_VERSION = "1.0.0"
RELEASE_DATE = "2026-11-06"


def label() -> str:
    return f"v{APP_VERSION} ({RELEASE_DATE})"
