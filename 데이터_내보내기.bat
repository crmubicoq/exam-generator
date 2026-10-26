@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 데이터 내보내기 - 원고와 출제 결과
set "PYEXE=python\python.exe"
if not exist "%PYEXE%" set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
"%PYEXE%" -m exam_gen.tools.transfer export
pause
