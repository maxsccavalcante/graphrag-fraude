@echo off
REM Calcula precisao e revocacao da extracao a partir da planilha anotada. Uso: duplo clique.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -u scripts\f2_anotacao.py --medir
echo.
pause
