@echo off
REM Fase 2 - Text-to-Graph completo. Uso: duplo clique.
REM Pode fechar e rodar de novo a qualquer momento: o script retoma de onde parou.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if not exist "data\interim\recorte_focado.json" ".venv\Scripts\python.exe" scripts\analisar_dataset.py
echo.
echo Rodando a Fase 2 (cerca de 30 a 40 minutos). O progresso tambem fica salvo em fase2.log
echo.
".venv\Scripts\python.exe" -u scripts\fase2.py tudo
echo.
pause
