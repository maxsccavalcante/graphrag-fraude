@echo off
REM Diagnostico do certificado da conexao com o Neo4j. Uso: duplo clique.
chcp 65001 >nul
cd /d "%~dp0"
echo Atualizando dependencias...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo Rodando diagnostico (pode levar ate 1 minuto)...
".venv\Scripts\python.exe" scripts\diagnostico_certificado.py > diagnostico.log 2>&1
type diagnostico.log
echo.
echo (Resultado salvo em diagnostico.log)
pause
