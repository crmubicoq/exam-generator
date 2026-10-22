@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 평가문항 자동 출제 - 설치

echo.
echo  [1/4] Python 확인
set "PY="
call :trypy "py -3.12"
if not defined PY call :trypy "py -3"
if not defined PY call :trypy "python"
if not defined PY (
  echo.
  echo  Python 3.10 이상이 설치되어 있지 않습니다.
  echo  https://www.python.org/downloads/ 에서 Python 3.12 를 설치하세요.
  echo  설치 첫 화면에서 "Add python.exe to PATH" 에 반드시 체크하세요.
  echo  설치 후 이 파일을 다시 실행하세요.
  echo.
  pause
  exit /b 1
)
%PY% --version

rem 폴더 경로가 길면 Windows 경로 길이 제한(260자)에 걸려 패키지 설치가 실패한다
%PY% -c "import os, sys; sys.exit(1 if len(os.getcwd()) > 60 else 0)"
if errorlevel 1 (
  echo.
  echo  [주의] 프로그램 폴더 경로가 깁니다: %CD%
  echo  경로가 길면 패키지 설치가 실패할 수 있습니다.
  echo  D:\ExamGenerator 처럼 짧은 경로로 폴더를 옮긴 뒤 다시 실행하는 것을 권장합니다.
  echo  그대로 진행하려면 아무 키나 누르세요.
  pause
)

echo.
echo  [2/4] 가상환경 만들기 (.venv)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo  가상환경을 만들지 못했습니다. 위 오류 메시지를 확인하세요.
    pause
    exit /b 1
  )
)

echo.
echo  [3/4] 패키지 설치 (처음에는 몇 분 걸립니다)
".venv\Scripts\python.exe" -m pip install --upgrade pip > nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo  패키지 설치에 실패했습니다.
  echo   - 위에 "Long Path" 또는 "No such file or directory" 가 보이면: 폴더를 D:\ExamGenerator 처럼 짧은 경로로 옮겨 다시 실행
  echo   - 그 밖의 경우: 인터넷·사내 프록시를 확인하고 다시 실행
  pause
  exit /b 1
)

echo.
echo  [4/4] 설치 점검
".venv\Scripts\python.exe" -m exam_gen.tools.check_env
pause
exit /b 0

:trypy
%~1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" > nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
