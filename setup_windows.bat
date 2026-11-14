@echo off
REM Passo 1.4 - Prepara o ambiente e testa a conexao com o Neo4j.
REM Uso: duplo clique neste arquivo (dentro da pasta do projeto).
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo  Projeto GraphRAG - Fraudes Financeiras (DOJ)
echo  Passo 1.4: ambiente + teste de conexao
echo ==================================================
echo.

echo [1/4] Criando .env a partir das credenciais do Aura...
set "CRED=%USERPROFILE%\Downloads\Neo4j-060ef994-Created-2026-11-13.txt"
if exist ".env" (
    echo       .env ja existe - mantido.
) else (
    if exist "%CRED%" (
        copy "%CRED%" ".env" >nul
        echo       .env criado.
    ) else (
        echo       [ERRO] Arquivo de credenciais nao encontrado:
        echo       %CRED%
        goto fim
    )
)
echo.

echo [2/4] Localizando o Python...
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo       [ERRO] Python nao encontrado no PATH.
    goto fim
)
%PY% --version
if not exist ".venv\Scripts\python.exe" (
    echo       Criando ambiente virtual .venv ...
    %PY% -m venv .venv
)
echo.

echo [3/4] Instalando dependencias (requirements.txt)...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo.

echo [4/4] Testando conexao com o Neo4j...
".venv\Scripts\python.exe" scripts\testar_conexao.py > teste_conexao.log 2>&1
type teste_conexao.log
echo.
echo (O resultado tambem foi salvo em teste_conexao.log)

:fim
echo.
pause
