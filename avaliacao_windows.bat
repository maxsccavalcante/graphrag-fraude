@echo off
REM Avaliacao GraphRAG x RAG vetorial x LLM sem contexto. Uso: duplo clique.
REM Se for interrompida (ou atingir o limite diario do Gemini), rode de novo: retoma de onde parou.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -u scripts\f3_avaliacao.py
echo.
pause
