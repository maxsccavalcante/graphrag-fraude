"""Gera a planilha de anotacao manual (medicao da qualidade da extracao).

Sorteia 10 comunicados extraidos (semente fixa) e lista cada item que o LLM
extraiu, com a evidencia. O grupo marca 'correto' (S/N) e acrescenta, na aba
'faltou', o que o LLM deixou passar. Depois, f2_anotacao.py --medir calcula
precisao (itens corretos / extraidos) e a revocacao aproximada.

A planilha tem nomes reais de pessoas: fica em data/private/ (fora do Git).
Uso: python scripts/f2_anotacao.py          (gera)
     python scripts/f2_anotacao.py --medir  (calcula a partir da planilha preenchida)
"""
import json
import random
import sys
from collections import Counter
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

RAIZ = Path(__file__).resolve().parent.parent
SELECAO = RAIZ / "data" / "interim" / "selecao.json"
DIR_EXTR = RAIZ / "data" / "interim" / "extracoes"
PLANILHA = RAIZ / "data" / "private" / "anotacao_manual.xlsx"
N, SEMENTE = 10, 7
TIPOS = [("reus", "reu"), ("instituicoes", "instituicao"), ("empresas", "empresa"),
         ("orgaos", "orgao"), ("juizes", "juiz"), ("promotores", "promotor"), ("agentes", "agente")]


def normalizar(t):
    return " ".join(str(t or "").lower().split())


def gerar():
    selecao = {c["caso_id"]: c for c in json.loads(SELECAO.read_text(encoding="utf-8"))}
    extraidos = sorted(p.stem for p in DIR_EXTR.glob("*.json"))
    amostra = sorted(random.Random(SEMENTE).sample(extraidos, N))
    wb = Workbook()
    ws = wb.active
    ws.title = "itens"
    cab = ["caso_id", "tipo", "nome", "detalhe", "evidencia", "evidencia_no_texto",
           "correto (S/N)", "observacao"]
    ws.append(cab)
    textos = wb.create_sheet("textos")
    textos.append(["caso_id", "titulo", "texto"])
    falta = wb.create_sheet("faltou")
    falta.append(["caso_id", "tipo", "nome que o LLM deixou passar", "evidencia"])
    for cid in amostra:
        caso = selecao[cid]
        base = normalizar(caso["titulo"] + " " + caso["texto"])
        ext = json.loads((DIR_EXTR / f"{cid}.json").read_text(encoding="utf-8"))["extracao"]
        textos.append([cid, caso["titulo"], caso["texto"]])
        for chave, tipo in TIPOS:
            for it in ext.get(chave, []):
                detalhe = it.get("desfecho") or it.get("papel") or it.get("sigla") or ""
                ok = normalizar(it.get("evidencia")).strip(" .\"'") in base
                ws.append([cid, tipo, it.get("nome"), detalhe, it.get("evidencia"),
                           "sim" if ok else "NAO", "", ""])
    for planilha, larguras in ((ws, [11, 12, 30, 14, 70, 12, 12, 30]), (textos, [11, 50, 120]),
                               (falta, [11, 12, 35, 70])):
        for i, w in enumerate(larguras):
            planilha.column_dimensions[chr(65 + i)].width = w
        for c in planilha[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="4C78A8")
        planilha.freeze_panes = "A2"
    for linha in textos.iter_rows(min_row=2):
        linha[2].alignment = Alignment(wrap_text=True, vertical="top")
    PLANILHA.parent.mkdir(parents=True, exist_ok=True)
    wb.save(PLANILHA)
    print(f"Planilha: {PLANILHA} ({ws.max_row - 1} itens de {N} comunicados: {', '.join(amostra)})")


PREENCHIDA = RAIZ / "data" / "private" / "anotacao_preenchida.xlsx"


def medir():
    arquivo = PREENCHIDA if PREENCHIDA.exists() else PLANILHA
    print(f"Lendo {arquivo.name}")
    wb = load_workbook(arquivo)
    itens = [r for r in wb["itens"].iter_rows(min_row=2, values_only=True) if r[0]]
    marcados = [r for r in itens if str(r[6] or "").strip().upper() in ("S", "N")]
    faltou = [r for r in wb["faltou"].iter_rows(min_row=2, values_only=True) if r[0]]
    if not marcados:
        sys.exit("Nenhum item marcado com S/N na coluna 'correto'.")
    por_tipo = Counter()
    certos_tipo = Counter()
    for r in marcados:
        por_tipo[r[1]] += 1
        certos_tipo[r[1]] += str(r[6]).strip().upper() == "S"
    certos = sum(certos_tipo.values())
    prec = certos / len(marcados)
    rec = certos / (certos + len(faltou)) if certos + len(faltou) else 0.0
    print(f"Itens marcados: {len(marcados)}/{len(itens)} | corretos: {certos} | faltaram: {len(faltou)}")
    print(f"Precisao = {prec:.1%} | Revocacao aproximada = {rec:.1%} | "
          f"F1 = {2 * prec * rec / (prec + rec):.1%}" if prec + rec else "")
    for t in por_tipo:
        print(f"  {t:<12} {certos_tipo[t]}/{por_tipo[t]} corretos ({certos_tipo[t] / por_tipo[t]:.0%})")
    saida = RAIZ / "data" / "interim" / "anotacao_resultado.json"
    saida.write_text(json.dumps({"marcados": len(marcados), "corretos": certos,
                                 "faltaram": len(faltou), "precisao": prec, "revocacao": rec,
                                 "por_tipo": {t: [certos_tipo[t], por_tipo[t]] for t in por_tipo}},
                                indent=1), encoding="utf-8")


if __name__ == "__main__":
    medir() if "--medir" in sys.argv else gerar()
