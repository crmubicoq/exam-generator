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

## 현재 상황 (2026-10-22 기준)
- 실제 원고 `[1권] 지키는 AI, 돕는 AI, 농업인을 위한 AI실무 가이드`(286쪽)로 시험 출제 4회 + Codex 검수 3회를 거쳐 규칙을 고쳤다. 상세: `docs/worklog/2026-10-22.md`.
- 최종 점검(처음부터 새로, 32문항): Claude 출제가 Codex보다 낫다(12분 vs 24분, 정교한 함정). 난이도는 '하' 쏠림이 남음.
- 노트북 시연용 꾸러미(setup.bat·run.bat·데이터 이전)와 사용 안내(`docs/평가문항_자동출제_사용안내.html`) 완료.
- 대시보드 기본 LLM: Claude 구독 로그인(`claude_agent_sdk`).

## 다음 할 일 — `docs/TODO.md` 참고
1. **[알림] 섞어 쓰기 교차 검증 테스트**: 출제는 Claude, G4 독립 풀이·G5 검수는 Codex. 사용자가 2026-10-23에 하기로 함(Codex 사용 한도 확인 후). 작업 시작 시 먼저 알린다.
2. 문항 밖 지시 표현('사례 A', '위 사례') 검사 보강.
3. 담당자 협의 후: 300문항 최종 시험(약 2~2.5시간, 호출 약 1,500회), 난이도 목표 조정, 사내 LiteLLM·딥시크 시험(보안 정책 확인 후).

## 개발 메모
- 실행: `run.bat` 또는 `python -m streamlit run app.py --server.port 8620`. 테스트: `python -m pytest -q`(현재 66개).
- CLI: `python -m exam_gen.cli new|run|export|report|pool <프로젝트>` (`run --provider claude_agent_sdk|codex_cli`).
- 출력 콘솔이 cp949라 한글이 깨지면 `PYTHONIOENCODING=utf-8`.
- 프롬프트를 바꾸면 `exam_gen/config.py`의 `PROMPT_VERSION`을 올린다.
- 다른 PC로 데이터 이전: `데이터_내보내기.bat` → ZIP → `데이터_가져오기.bat`. 두 PC에서 동시에 출제 데이터를 고치지 않는다(문항 DB는 합쳐지지 않음).
