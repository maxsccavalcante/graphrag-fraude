@echo off
REM Passo 1.5 - Aplica constraints e indices no Neo4j e valida. Uso: duplo clique.
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" scripts\aplicar_schema.py > aplicar_schema.log 2>&1
type aplicar_schema.log
echo.
echo (Resultado salvo em aplicar_schema.log)
pause
