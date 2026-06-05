@echo off
title 한영 맞춤법 검사기 실행기
echo 한영 맞춤법 검사기를 실행하는 중입니다...
cd /d "%~dp0"
call venv\Scripts\activate.bat
chainlit run app.py
pause
