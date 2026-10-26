@echo off
chcp 65001 > nul
cd /d "%~dp0"
title Claude 구독 로그인

rem 휴대용 패키지는 python 폴더의 내장 Python을, 개발용 설치는 .venv를 쓴다
set "PYEXE=python\python.exe"
if not exist "%PYEXE%" set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
  echo  아직 설치되지 않았습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
for /f "delims=" %%i in ('"%PYEXE%" -c "from exam_gen.tools.check_env import bundled_claude; print(bundled_claude() or '')"') do set "CLAUDE=%%i"
if "%CLAUDE%"=="" (
  echo  Claude 프로그램을 찾지 못했습니다. setup.bat 을 다시 실행하세요.
  pause
  exit /b 1
)

echo.
echo  잠시 뒤 Claude 화면이 열립니다.
echo   1. 화면에서  /login  을 입력하고 Enter
echo   2. 브라우저가 열리면 회사 Claude 계정으로 로그인하고 승인
echo   3. 로그인되었다는 메시지가 나오면  /exit  를 입력해 닫기
echo.
pause
"%CLAUDE%"
echo.
echo  로그인 확인 중 (Claude를 한 번 호출합니다)...
"%PYEXE%" -m exam_gen.tools.check_env --login
pause
