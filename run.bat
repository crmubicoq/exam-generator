@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 평가문항 자동 출제 - 실행 중 (이 창을 닫으면 프로그램이 종료됩니다)

if not exist ".venv\Scripts\python.exe" (
  echo  아직 설치되지 않았습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)

rem 이미 실행 중이면 브라우저만 연다
netstat -ano | findstr /r /c:":8620 .*LISTENING" > nul
if not errorlevel 1 (
  echo  이미 실행 중입니다. 브라우저를 엽니다.
  start "" http://localhost:8620
  exit /b 0
)

echo.
echo  대시보드를 시작합니다. 잠시 뒤 브라우저가 열립니다.
echo  주소: http://localhost:8620
echo  사용을 마치면 이 창을 닫으세요.
echo.
rem 처음 실행할 때 Streamlit이 이메일을 묻지 않도록 빈 등록 정보를 만들어 둔다
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
  > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8620 --browser.gatherUsageStats false
pause
