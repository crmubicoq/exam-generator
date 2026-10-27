@echo off
chcp 65001 > nul
cd /d "%~dp0"
title Codex 구독 로그인 (선택)

rem 휴대용 패키지는 python 폴더의 내장 Python을, 개발용 설치는 .venv를 쓴다
set "PYEXE=python\python.exe"
if not exist "%PYEXE%" set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
  echo  아직 설치되지 않았습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)

echo.
echo  Codex(ChatGPT 계정)는 선택 사항입니다. 출제는 Claude만으로 할 수 있고,
echo  Codex는 '출제는 Claude, 검증은 Codex'처럼 다른 AI로 교차 검증할 때 씁니다.
echo.

rem Codex를 npm으로 설치했으면 사용자 npm 폴더에 있다
set "PATH=%APPDATA%\npm;%PATH%"
where codex > nul 2>&1
if not errorlevel 1 goto login

echo  [1/3] Codex 프로그램이 설치되어 있지 않습니다.
where npm > nul 2>&1
if errorlevel 1 (
  echo.
  echo  Codex를 설치하려면 먼저 Node.js 가 필요합니다.
  echo   1. 곧 열리는 https://nodejs.org 에서 LTS 버전을 내려받아 설치하세요.
  echo      회사 PC는 설치에 관리자 권한이 필요할 수 있습니다.
  echo   2. 설치가 끝나면 이 파일을 다시 실행하세요.
  echo.
  pause
  start "" https://nodejs.org/
  exit /b 1
)
echo  Node.js 가 있습니다. Codex 를 설치합니다: npm install -g @openai/codex
echo  계속하려면 아무 키나 누르세요(취소: 창 닫기).
pause > nul
call npm install -g @openai/codex
if errorlevel 1 (
  echo.
  echo  Codex 설치에 실패했습니다. 인터넷·사내 프록시를 확인하고 다시 실행하세요.
  pause
  exit /b 1
)

:login
echo.
echo  [2/3] 로그인 — 잠시 뒤 브라우저가 열립니다.
echo   1. ChatGPT 계정(Codex가 포함된 요금제)으로 로그인하고 승인하세요.
echo   2. 로그인이 끝나면 이 창으로 돌아옵니다.
echo   이미 로그인되어 있어도 다른 계정으로 바꾸려면 다시 로그인하면 됩니다.
echo.
pause
call codex login

echo.
echo  [3/3] 로그인 확인 (AI를 호출하지 않습니다)
"%PYEXE%" -m exam_gen.tools.check_env --codex
echo.
echo  대시보드가 켜져 있었다면 run.bat 창을 닫고 다시 실행한 뒤,
echo  왼쪽 'LLM 설정'에서 Codex 공급자와 모델을 고르고 '연결·로그인 확인'을 누르세요.
pause
