@echo off
cd /d "%~dp0app\backend"

call .venv\Scripts\activate.bat

uvicorn app.main:app --port 8000

pause