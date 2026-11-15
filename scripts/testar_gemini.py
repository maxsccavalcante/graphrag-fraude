"""Passo 2.1 - Testa a chave do Gemini e a saida em JSON com esquema fechado.

Uso (na raiz do projeto, com GEMINI_API_KEY no .env):
    python scripts/testar_gemini.py

Nao imprime a chave. Faz no maximo 2 chamadas ao modelo.
"""
import json
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from llm import MODELO, criar_cliente, gerar_json, listar_modelos_texto, sugerir_modelo

RAIZ = Path(__file__).resolve().parent.parent
RECORTE = RAIZ / "data" / "interim" / "recorte_focado.json"


class Instituicao(BaseModel):
    nome: str
    papel: str = Field(description="vitima, acusada ou intermediaria")
    evidencia: str = Field(description="frase exata do texto que comprova")


class TesteExtracao(BaseModel):
    eh_fraude_financeira: bool
    instituicoes_financeiras: list[Instituicao]


def main():
    try:
        cliente = criar_cliente()
    except RuntimeError as erro:
        print(f"[ERRO] {erro}")
        return 1

    print("[1] Modelos de texto disponiveis na sua conta:")
    try:
        nomes = listar_modelos_texto(cliente)
    except Exception as erro:  # noqa: BLE001
        print(f"[ERRO] Nao consegui listar os modelos: {erro}")
        return 1
    for n in nomes:
        print(f"    - {n}")

    modelo = MODELO or sugerir_modelo(nomes)
    origem = "definido no .env" if MODELO else "sugestao automatica"
    print(f"\n[2] Modelo usado no teste: {modelo} ({origem})")
    if not modelo:
        print("[ERRO] Nenhum modelo Flash disponivel.")
        return 1

    if RECORTE.exists():
        caso = json.loads(RECORTE.read_text(encoding="utf-8"))[0]
        texto = caso["title"] + "\n\n" + caso["contents"][:2500]
        print(f"    Texto de teste: comunicado real -> {caso['title'][:80]}")
    else:
        print("[ERRO] Rode antes: python scripts/analisar_dataset.py")
        return 1

    prompt = (
        "Voce extrai informacoes de comunicados do Departamento de Justica dos EUA.\n"
        "Responda se o texto trata de fraude financeira e liste as instituicoes "
        "financeiras citadas (bancos, cooperativas, corretoras), com o papel de cada "
        "uma e a frase EXATA do texto que comprova. Nao invente: use apenas o texto.\n\n"
        f"TEXTO:\n{texto}"
    )
    print("\n[3] Testando saida em JSON com esquema fechado...")
    try:
        resultado = gerar_json(cliente, modelo, prompt, TesteExtracao)
    except Exception as erro:  # noqa: BLE001
        print(f"[ERRO] {erro.__class__.__name__}: {erro}")
        return 1

    print(f"    Fraude financeira? {resultado.eh_fraude_financeira}")
    print(f"    Instituicoes encontradas: {len(resultado.instituicoes_financeiras)}")
    ok_evid = 0
    for inst in resultado.instituicoes_financeiras:
        existe = inst.evidencia.strip() and inst.evidencia.strip() in texto
        ok_evid += bool(existe)
        marca = "OK " if existe else "NAO ENCONTRADA"
        print(f"     - {inst.nome} ({inst.papel}) | evidencia no texto: {marca}")

    print(f"\n[OK] Gemini funcionando. Evidencias conferidas: "
          f"{ok_evid}/{len(resultado.instituicoes_financeiras)}")
    print("Anote o modelo acima; para fixa-lo, adicione ao .env: "
          f"GEMINI_MODEL={modelo}")
    return 0


if __name__ == "__main__":
    from registro import espelhar
    espelhar("testar_gemini.log")
    sys.exit(main())
