# 평가문항 자동 출제 도구 — 작업 안내 (Claude Code용)

다른 팀 요청으로 만드는 프로토타입이다. 원고(PDF·PPTX·DOCX·HWP) → 출제 옵션 → 출제(자동 검증 G1~G6) → 사람 검수(G7) → 양식 엑셀 + 근거PDF.

## 반드시 지킬 것
- **사용자에게는 항상 한국어로 답한다.** 중간 진행 안내도 한국어. (여러 번 지적받음)
- **원고를 git에 올리지 않는다.** 원고는 `원고/`(gitignore), 출제 결과는 `projects/`(gitignore). 커밋 전 `git status --short` 확인, `git add -A` 금지. 원고 문장을 인용한 검수 보고서(`docs/claude_real_review_handoff.md`, `docs/codex_real_review_3_result.md`)도 올리지 않는다.
- **최우선 요구**: ★1 원고 전반 출제 ★2 원고 위 출처 표시 ★3 신뢰도. 문항 오류는 서비스 사고다.
- 비용이 드는 출제(실제 LLM 호출)는 규모·예상 시간을 말하고 사용자 동의를 받은 뒤 실행한다. 비용·시간 예상은 실측 근거로 말한다(두 번 틀렸음).
- 작업을 마무리하는 날 `docs/worklog/YYYY-MM-DD.md` 작성, `docs/TODO.md` 갱신. 작업 시작 시 TODO를 먼저 본다.
- **GitHub에 push할 때는 `README.md`도 바뀐 기능에 맞게 함께 갱신한다**(사용자 요청 2026-10-22).
- 화면은 처음 쓰는 다른 팀 실무자 기준: 용어·옵션 설명을 화면에 바로 보이게, 안내 문구는 '~습니다'체(문항·해설은 지침대로 '~이다'체).
- 문항 품질 판단은 Codex 교차 검수를 함께 받는다(민감한 영역이라 크로스 체크). 같은 AI가 만든 문항을 같은 AI가 검수한 등급은 그대로 비교하지 않는다.

## 현재 상황 (2026-10-26 기준)
- 2026-10-23 담당자 회의 피드백 10가지 + 추가 기능을 모두 반영했다(상세: `docs/worklog/2026-10-26.md`, TODO 2-7절).
  - 검수 없이 출력, 원고 새 판 교체(표시는 토큰 0·확정 후 일괄 처리), 프로젝트 관리, 기출 등록, 유사답변, 함정 색 파랑, 8:2, 최상=자격시험형, 기본 AI Claude 구독
  - 빠른 출제, 최종 문항 자동 선정, 시험지 PDF, 완료 알림, 자동 백업, 출력 이력, 이의신청 조회
  - 휴대용 패키지: `python tools/build_portable.py` → `dist/`(git 제외). 패키지에는 마지막 커밋만 들어간다.
- 운영 조건: 1~2개월은 Claude 구독, 당분간 로컬 설치형(서버형·문항 은행은 2차 범위).
- 대시보드 기본 LLM: Claude 구독 로그인(`claude_agent_sdk`).

## 다음 할 일 — `docs/TODO.md` 참고
1. 노트북에 휴대용 패키지 설치 확인(사용자).
2. 최상 5문항 시험 출제 → **300문항 출제**(약 2~2.5시간, 호출 약 1,500회) — 사용자 승인 후. 결과를 담당자 재협의용으로 정리.
3. **[알림] 섞어 쓰기 교차 검증 테스트**: 출제는 Claude, G4 독립 풀이·G5 검수는 Codex. Codex 사용 한도 확인 후. 작업 시작 시 먼저 알린다.
4. 문항 밖 지시 표현('사례 A', '위 사례') 검사 보강. 사내 LiteLLM·딥시크 시험(보안 정책 확인 후).

## 개발 메모
- 실행: `run.bat` 또는 `python -m streamlit run app.py --server.port 8620`. 테스트: `python -m pytest -q`(현재 77개, `pytest.ini`로 tests/만 탐색).
- CLI: `python -m exam_gen.cli new|run|export|report|pool <프로젝트>` (`run --provider claude_agent_sdk|codex_cli`).
- 출력 콘솔이 cp949라 한글이 깨지면 `PYTHONIOENCODING=utf-8`.
- 프롬프트를 바꾸면 `exam_gen/config.py`의 `PROMPT_VERSION`을 올린다.
- 다른 PC로 데이터 이전: `데이터_내보내기.bat` → ZIP → `데이터_가져오기.bat`. 두 PC에서 동시에 출제 데이터를 고치지 않는다(문항 DB는 합쳐지지 않음).
