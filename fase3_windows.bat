@echo off
REM Fase 3 - atualiza o grafo, calcula PageRank/Louvain/menor caminho e avalia GraphRAG x RAG x LLM.
REM Uso: duplo clique. A avaliacao retoma de onde parou se for interrompida.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo.
echo Atualizando o grafo (usa o que ja foi extraido; nao chama o LLM de texto)...
".venv\Scripts\python.exe" -u scripts\fase2.py processar
".venv\Scripts\python.exe" -u scripts\fase2.py embeddings
".venv\Scripts\python.exe" -u scripts\fase2.py ingerir
".venv\Scripts\python.exe" -u scripts\fase2.py relatorio
echo.
".venv\Scripts\python.exe" -u scripts\f3_algoritmos.py
echo.
if not exist "data\private\anotacao_manual.xlsx" ".venv\Scripts\python.exe" scripts\f2_anotacao.py
echo.
echo Avaliacao (cerca de 20 a 30 minutos)...
".venv\Scripts\python.exe" -u scripts\f3_avaliacao.py
echo.
pause
