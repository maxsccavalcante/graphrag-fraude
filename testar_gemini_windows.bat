@echo off
REM Passo 2.1 - Prepara o recorte do dataset e testa o Gemini. Uso: duplo clique.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if not exist "data\interim\recorte_focado.json" ".venv\Scripts\python.exe" scripts\analisar_dataset.py
echo.
if exist testar_gemini.log del testar_gemini.log
".venv\Scripts\python.exe" -u scripts\testar_gemini.py
echo.
echo (Resultado salvo em testar_gemini.log)
pause
