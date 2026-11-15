@echo off
REM Calcula PageRank, Louvain e menor caminho e grava no Neo4j. Uso: duplo clique.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -u scripts\f3_algoritmos.py
echo.
pause
