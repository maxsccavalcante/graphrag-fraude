@echo off
REM Publica (ou atualiza) o projeto no GitHub. Uso: duplo clique.
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
echo Fases 2 a 4: Text-to-Graph, GraphRAG/KAG, avaliacao e interface
echo.
echo - Pipeline Text-to-Graph com Gemini: esquema fechado, evidencia verificada,
echo   resolucao de entidades, anonimizacao, trechos e embeddings ^(fase2.py^)
echo - Motor GraphRAG/KAG: Text2Cypher somente leitura, busca hibrida com RRF,
echo   expansao multi-hop, citacoes, confianca e "nao encontrado" ^(graphrag.py^)
echo - PageRank, Louvain e menor caminho ^(f3_algoritmos.py^)
echo - Avaliacao GraphRAG x RAG vetorial x LLM sem contexto ^(f3_avaliacao.py^)
echo - Interface Streamlit ^(app.py^) e README atualizado
echo.
echo Co-Authored-By: Claude Opus 5.5 ^<noreply@anthropic.com^>
echo Claude-Session: https://claude.ai/code/session_012kW3edxJezkZagBzqQ6coT
) > "%TEMP%\msg_commit.txt"
git commit -q -F "%TEMP%\msg_commit.txt"
del "%TEMP%\msg_commit.txt" >nul 2>nul

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
