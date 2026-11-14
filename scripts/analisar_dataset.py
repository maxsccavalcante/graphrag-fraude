"""Passo 1.1/1.2 - Reproduz a analise do dataset DOJ citada no README.

Uso (na raiz do projeto, com o zip do Kaggle em data/raw/dataset1.zip):
    python scripts/analisar_dataset.py

Saidas:
  - numeros do dataset completo e do recorte de fraude financeira
  - data/interim/recorte_focado.json (comunicados candidatos para a Fase 2)
As contagens sao por padrao de texto (regex), portanto aproximadas.
"""
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ZIP = RAIZ / "data" / "raw" / "dataset1.zip"
SAIDA = RAIZ / "data" / "interim" / "recorte_focado.json"

TOPICOS_FINANCEIROS = {
    "Financial Fraud",
    "Mortgage Fraud",
    "Securities, Commodities, & Investment Fraud",
}
PADRAO_TEXTO = r"\bbank fraud\b|Bank Secrecy Act|mortgage fraud"
PADRAO_TITULO = (r"\bbank|launder|ponzi|investment fraud|securities fraud|wire fraud|"
                 r"mortgage|loan fraud|financial institution|check fraud|credit card")

CONTAGENS = {
    "Valores em US$ milhoes/bilhoes": r"\$[\d\.,]+ (?:million|billion)",
    "Confissao de culpa (pleaded guilty)": r"pleaded guilty|pled guilty",
    "Condenacao (convicted)": r"convicted",
    "Denuncia (indicted/charged)": r"indicted|charged",
    "Coreus/cumplices": r"co-defendant|co-conspirator|and others|along with",
    "FBI": r"\bFBI\b|Federal Bureau of Investigation",
    "IRS": r"IRS|Internal Revenue Service",
    "SEC": r"\bSEC\b|Securities and Exchange Commission",
    "HUD/FHFA": r"HUD|FHFA",
    "Restituicao": r"restitution",
    "Confisco (forfeit)": r"forfeit",
    "Esquema hipotecario": r"mortgage",
    "Numero de lei (U.S.C.)": r"U\.S\.C\.",
    "Juiz citado": r"Judge [A-Z]",
    "Promotor citado": r"(Trial Attorneys?|Assistant U\.?S\.? Attorneys?) [A-Z]",
}


def carregar():
    if not ZIP.exists():
        sys.exit(f"[ERRO] Coloque o zip do Kaggle em {ZIP.relative_to(RAIZ)}")
    with zipfile.ZipFile(ZIP) as z, z.open("combined.json") as f:
        return [json.loads(linha) for linha in f]


def eh_fraude_financeira(r):
    texto = r["title"] + " " + r["contents"]
    return bool(
        TOPICOS_FINANCEIROS & set(r.get("topics") or [])
        or re.search(PADRAO_TEXTO, texto, re.I)
        or re.search(PADRAO_TITULO, r["title"], re.I)
    )


def main():
    todos = carregar()
    datas = sorted(r["date"][:10] for r in todos if r.get("date"))
    sem_topico = sum(1 for r in todos if not r.get("topics"))
    print(f"Comunicados no dataset : {len(todos)}")
    print(f"Periodo                : {datas[0]} a {datas[-1]}")
    print(f"Sem topico             : {sem_topico} ({sem_topico / len(todos):.0%})")

    recorte = [r for r in todos if eh_fraude_financeira(r)]
    print(f"\nRecorte de fraude financeira: {len(recorte)} comunicados")
    anos = Counter(r["date"][:4] for r in recorte)
    print("Por ano:", ", ".join(f"{a}: {n}" for a, n in sorted(anos.items())))

    print("\nComunicados do recorte que citam:")
    for rotulo, padrao in CONTAGENS.items():
        n = sum(1 for r in recorte if re.search(padrao, r["contents"]))
        print(f"  {rotulo:<38} {n}")

    sem_id = sum(1 for r in recorte if not r.get("id"))
    ids = [r["id"] for r in recorte if r.get("id")]
    print(f"\nSem id original: {sem_id} | ids repetidos: {len(ids) - len(set(ids))}")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(recorte, ensure_ascii=False), encoding="utf-8")
    print(f"\nRecorte salvo em {SAIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
