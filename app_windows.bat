@echo off
REM Abre a interface do GraphRAG no navegador. Uso: duplo clique. Para fechar: feche esta janela.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
REM Pula a pergunta de e-mail que o Streamlit faz na primeira vez
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
  echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo.
echo Abrindo a interface em http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
