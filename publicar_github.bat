@echo off
REM Passo 1.6 - Publica o projeto no GitHub. Uso: duplo clique.
chcp 65001 >nul
cd /d "%~dp0"

git --version >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Git nao encontrado. Instale em https://git-scm.com e rode de novo.
    goto fim
)

if not exist ".git" git init -b main

git config user.name >nul 2>nul
if errorlevel 1 git config user.name "Max Cavalcante"
git config user.email >nul 2>nul
if errorlevel 1 git config user.email "maxsccavalcante@users.noreply.github.com"

git add -A

echo.
echo Conferindo se nenhum segredo vai subir...
git ls-files --cached --error-unmatch .env >nul 2>nul
if not errorlevel 1 (
    echo [ERRO] O .env apareceu na lista de envio. Nada foi publicado.
    git reset -q
    goto fim
)
echo [OK] .env fora do envio.
echo.
echo Arquivos que vao para o GitHub:
git status --short
echo.

(
echo Fase 1: proposta, modelo LPG e schema do Neo4j
echo.
echo - Estudo de caso: Dados Grafos ^(Dados Financeiros ^(Fraudes^)^), dataset DOJ 2009-2018
echo - Conexao com Neo4j Aura e teste ^(passo 1.4^)
echo - Schema com constraints e indices B-Tree/RANGE, Fulltext e Vector, validado ^(passo 1.5^)
echo - README da Fase 1
echo.
echo Co-Authored-By: Claude Opus 5.5 ^<noreply@anthropic.com^>
echo Claude-Session: https://claude.ai/code/session_012kW3edxJezkZagBzqQ6coT
) > "%TEMP%\msg_fase1.txt"
git commit -q -F "%TEMP%\msg_fase1.txt"
del "%TEMP%\msg_fase1.txt" >nul 2>nul

git remote get-url origin >nul 2>nul
if errorlevel 1 git remote add origin https://github.com/maxsccavalcante/graphrag-fraude.git

echo Enviando para o GitHub (pode abrir uma janela de login)...
git push -u origin main
if errorlevel 1 (
    echo [ERRO] O envio falhou. Copie a mensagem acima e me mande.
) else (
    echo.
    echo [OK] Publicado em https://github.com/maxsccavalcante/graphrag-fraude
)

:fim
echo.
pause
