"""Fase 3 - Avaliacao: GraphRAG x RAG so vetorial x LLM sem contexto.

1. Gera perguntas com GABARITO tirado do grafo por consultas Cypher fixas
   (fato simples, contagem, multi-hop) + perguntas SEM resposta no corpus
   (entidades ficticias), que medem se o sistema inventa.
2. Roda as 3 versoes em cada pergunta (retomavel: guarda cada resposta).
3. Mede acerto e taxa de alucinacao por modo e por tipo de pergunta.

Uso: python scripts/f3_avaliacao.py            (gera perguntas se faltar e avalia)
     python scripts/f3_avaliacao.py --refazer   (gera as perguntas de novo)
"""
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import graphrag as gr

RAIZ = Path(__file__).resolve().parent.parent
PERGUNTAS = RAIZ / "data" / "interim" / "perguntas_avaliacao_v2.json"
RESPOSTAS = RAIZ / "data" / "interim" / "respostas_avaliacao_v2.json"
SAIDA = RAIZ / "data" / "interim" / "avaliacao.json"
MODOS = ["graphrag", "rag_vetorial", "llm_puro"]
PAUSA = 4.5

SEM_RESPOSTA = [
    "Qual foi a pena aplicada ao presidente do Banco Atlantida do Brasil?",
    "Quais orgaos investigaram o Banco Marajoara?",
    "Em quantos casos aparece a cooperativa de credito Credipampa?",
    "Qual foi o valor da fraude cometida pela corretora Solaris Investimentos em 2014?",
    "Quem foi o juiz do caso de fraude no Banco Tupiniquim?",
]
DESFECHO_TERMOS = {
    "condenado": ["condenad", "convicted", "found guilty", "culpad"],
    "sentenciado": ["sentenc"],
    "confessou": ["confess", "declarou-se culpad", "declarou culpad", "pleaded guilty",
                  "admitiu", "se declarou"],
    "denunciado": ["denunciad", "indiciad", "acusad", "indicted", "charged"],
    "acordo": ["acordo", "settle"],
}


def norm(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\.,'’]", "", t)
    return re.sub(r"\s+", " ", t)


def contem(texto_norm, forma):
    f = norm(forma).strip()
    return bool(f) and re.search(rf"(?<![a-z0-9]){re.escape(f)}(?![a-z0-9])", texto_norm)


def spans(texto_norm, formas):
    out = []
    for forma in formas:
        f = norm(forma).strip()
        if f:
            out += [(m.start(), m.end()) for m in
                    re.finditer(rf"(?<![a-z0-9]){re.escape(f)}(?![a-z0-9])", texto_norm)]
    return out


def entidades_citadas(texto_norm, universo, pergunta_norm):
    """Entidades do universo citadas na resposta.
    - ignora a entidade que a propria pergunta cita (ex.: 'junto com PNC');
    - ignora casamentos contidos num nome maior (ex.: 'DOJ' dentro de 'DOJ Tax Division')."""
    achadas = {k: spans(texto_norm, f) for k, f in universo.items()}
    achadas = {k: v for k, v in achadas.items() if v}
    na_pergunta = {k for k, f in universo.items() if any(contem(pergunta_norm, x) for x in f)}
    final = set()
    for k, sp in achadas.items():
        if k in na_pergunta:
            continue
        outros = [o for j, v in achadas.items() if j != k for o in v]
        if any(not any(a2 <= a and b <= b2 and (b2 - a2) > (b - a) for a2, b2 in outros)
               for a, b in sp):
            final.add(k)
    return final


# ---------------------------------------------------------------- perguntas
def formas_inst():
    """Formas de escrita (nome, exibicao, aliases) de cada instituicao."""
    linhas = gr.ler_cypher("MATCH (i:InstituicaoFinanceira) RETURN i.nome AS nome, "
                           "i.nome_exibicao AS exib, i.aliases AS aliases")
    out = {}
    for l in linhas:
        fs = {l["nome"], l["exib"], *(l["aliases"] or [])}
        out[l["nome"]] = sorted(f for f in fs if f and len(f) >= 3
                                and norm(f) not in ("bank", "the bank", "banks"))
    return out


def formas_orgao():
    linhas = gr.ler_cypher("MATCH (o:Orgao) RETURN o.nome AS nome, o.nome_exibicao AS exib, "
                           "o.sigla AS sigla")
    return {l["nome"]: sorted({f for f in (l["nome"], l["exib"], l["sigla"]) if f})
            for l in linhas}


def gerar_perguntas():
    p = []
    # T1 - fato simples: instituicoes de um caso
    for r in gr.ler_cypher("""
        MATCH (c:Caso)-[:ENVOLVE_INSTITUICAO]->(i)
        WITH c, collect(DISTINCT i.nome) AS g WHERE 2 <= size(g) <= 4
        RETURN c.caso_id AS caso, c.titulo AS titulo, g ORDER BY c.caso_id LIMIT 5"""):
        p.append({"tipo": "fato_simples", "classe": "inst",
                  "pergunta": f"Quais instituicoes financeiras aparecem no caso \"{r['titulo']}\"?",
                  "gabarito": r["g"]})
    # T2 - agregacao: em quantos casos a instituicao aparece
    for r in gr.ler_cypher("""
        MATCH (c:Caso)-[:ENVOLVE_INSTITUICAO]->(i)
        WITH i, count(DISTINCT c) AS n WHERE n >= 3
        RETURN i.nome_exibicao AS nome, n ORDER BY n DESC, nome LIMIT 4"""):
        p.append({"tipo": "agregacao", "classe": "numero",
                  "pergunta": f"Em quantos casos do grafo a instituicao {r['nome']} aparece?",
                  "gabarito": r["n"]})
    # T3 - multi-hop: orgaos que investigaram casos de uma instituicao
    for r in gr.ler_cypher("""
        MATCH (o:Orgao)-[:INVESTIGOU]->(c:Caso)-[:ENVOLVE_INSTITUICAO]->(i)
        WITH i, collect(DISTINCT o.nome) AS g, count(DISTINCT c) AS n
        WHERE 2 <= n <= 6 AND 2 <= size(g) <= 6
        RETURN i.nome_exibicao AS nome, g ORDER BY n DESC, nome LIMIT 5"""):
        p.append({"tipo": "multi_hop", "classe": "orgao",
                  "pergunta": f"Quais orgaos investigaram casos que envolvem a instituicao "
                              f"{r['nome']}?", "gabarito": r["g"]})
    # T4 - multi-hop: instituicoes que aparecem junto com outra
    for r in gr.ler_cypher("""
        MATCH (i)<-[:ENVOLVE_INSTITUICAO]-(c:Caso)-[:ENVOLVE_INSTITUICAO]->(j)
        WHERE i <> j
        WITH i, collect(DISTINCT j.nome) AS g, count(DISTINCT c) AS n
        WHERE 2 <= n <= 6 AND 2 <= size(g) <= 6
        RETURN i.nome_exibicao AS nome, g ORDER BY n DESC, nome LIMIT 4"""):
        p.append({"tipo": "multi_hop", "classe": "inst",
                  "pergunta": f"Quais outras instituicoes financeiras aparecem em casos junto "
                              f"com {r['nome']}?", "gabarito": r["g"]})
    # T5 - atributo de relacao: desfecho de um reu
    for r in gr.ler_cypher("""
        MATCH (r:Reu)-[x:REU_EM]->(c:Caso)
        WHERE x.desfecho IN ['condenado','sentenciado','confessou','denunciado']
        WITH r, x, c ORDER BY r.codigo
        WITH x.desfecho AS d, collect({r: r.codigo, t: c.titulo})[0] AS ex
        RETURN d, ex.r AS reu, ex.t AS titulo ORDER BY d"""):
        p.append({"tipo": "fato_simples", "classe": "desfecho",
                  "pergunta": f"Qual foi a situacao processual (desfecho) de {r['reu']} no caso "
                              f"\"{r['titulo']}\"?", "gabarito": r["d"]})
    # T6 - sem resposta no corpus
    for q in SEM_RESPOSTA:
        p.append({"tipo": "sem_resposta", "classe": "abster", "pergunta": q, "gabarito": None})
    for i, x in enumerate(p, 1):
        x["id"] = f"P{i:02d}"
    PERGUNTAS.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(p)} perguntas geradas em {PERGUNTAS.name}")
    return p


# ------------------------------------------------------------------ correcao
def corrigir(perg, resp, f_inst, f_org):
    # tira as citacoes ([DOJ-00025-T01], [Q1]) antes de procurar entidades: 'DOJ-00025' nao e o orgao DOJ
    texto = norm(re.sub(r"\[[^\]]*\]", " ", resp["resposta"]))
    absteve = not resp["encontrado"]
    if perg["classe"] == "abster":
        return {"acerto": 1.0 if absteve else 0.0, "alucinou": not absteve, "absteve": absteve}
    if absteve:
        return {"acerto": 0.0, "alucinou": False, "absteve": True}
    if perg["classe"] in ("inst", "orgao"):
        universo = f_inst if perg["classe"] == "inst" else f_org
        gab = set(perg["gabarito"])
        # so nas perguntas multi-hop a entidade da pergunta ("junto com X") nao conta como resposta;
        # nas de fato simples o titulo do caso pode conter o proprio gabarito
        alvo = norm(perg["pergunta"]) if perg["tipo"] == "multi_hop" else ""
        citadas = entidades_citadas(texto, universo, alvo)
        acertos = citadas & gab
        prec = len(acertos) / len(citadas) if citadas else 0.0
        rec = len(acertos) / len(gab)
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        falsas = citadas - gab
        return {"acerto": round(f1, 3), "precisao": round(prec, 3), "revocacao": round(rec, 3),
                "alucinou": bool(falsas) or not acertos, "absteve": False,
                "entidades_falsas": sorted(falsas)}
    if perg["classe"] == "numero":
        sem_ids = re.sub(r"doj-\d{5}(-t\d{2})?|\bq\d+\b|(reu|juiz|promotor|agente)_\d{3}", " ", texto)
        nums = {int(n) for n in re.findall(r"\b\d+\b", sem_ids)}
        ok = perg["gabarito"] in nums
        return {"acerto": 1.0 if ok else 0.0, "alucinou": not ok, "absteve": False}
    if perg["classe"] == "desfecho":
        ok = any(t in texto for t in DESFECHO_TERMOS[perg["gabarito"]])
        return {"acerto": 1.0 if ok else 0.0, "alucinou": not ok, "absteve": False}
    raise ValueError(perg["classe"])


# ------------------------------------------------------------------- execucao
def main():
    from registro import espelhar
    espelhar("avaliacao.log")
    print(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} - avaliacao =====")
    perguntas = (gerar_perguntas() if "--refazer" in sys.argv or not PERGUNTAS.exists()
                 else json.loads(PERGUNTAS.read_text(encoding="utf-8")))
    respostas = (json.loads(RESPOSTAS.read_text(encoding="utf-8"))
                 if RESPOSTAS.exists() and "--refazer" not in sys.argv else {})
    total = len(perguntas) * len(MODOS)
    for p in perguntas:
        for modo in MODOS:
            chave = f"{p['id']}|{modo}"
            if chave in respostas:
                continue
            for tentativa in (1, 2):
                try:
                    r = gr.responder(p["pergunta"], modo, tolerante=False)
                    respostas[chave] = {k: r.get(k) for k in (
                        "resposta", "encontrado", "confianca", "confianca_rotulo", "cypher",
                        "erro_cypher", "citacoes", "citacoes_invalidas", "segundos")}
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  {chave}: erro ({str(e)[:160]}); "
                          f"{'nova tentativa' if tentativa == 1 else 'pulando'}")
                    if "per day" in str(e).lower() or "PerDay" in str(e):
                        print("  Limite DIARIO do Gemini atingido. Rode de novo amanha: retoma daqui.")
                        sys.exit(0)
                    time.sleep(20)
            RESPOSTAS.write_text(json.dumps(respostas, ensure_ascii=False, indent=1,
                                            default=str), encoding="utf-8")
            print(f"  [{len(respostas)}/{total}] {chave} {'ok' if chave in respostas else 'PULADA'}")
            time.sleep(PAUSA)

    f_inst, f_org = formas_inst(), formas_orgao()
    detalhes, por_modo, por_tipo = [], defaultdict(list), defaultdict(list)
    for p in perguntas:
        for modo in MODOS:
            r = respostas.get(f"{p['id']}|{modo}")
            if not r:
                continue
            c = corrigir(p, r, f_inst, f_org)
            detalhes.append({"id": p["id"], "tipo": p["tipo"], "modo": modo,
                             "pergunta": p["pergunta"], "gabarito": str(p["gabarito"]),
                             "resposta": r["resposta"], "confianca": r["confianca"], **c,
                             "entidades_falsas": ", ".join(c.get("entidades_falsas", []))})
            por_modo[modo].append(c)
            por_tipo[(modo, p["tipo"])].append(c)

    def resumir(lista):
        n = len(lista)
        return {"n": n, "acerto": round(sum(c["acerto"] for c in lista) / n, 3),
                "taxa_alucinacao": round(sum(c["alucinou"] for c in lista) / n, 3),
                "taxa_abstencao": round(sum(c["absteve"] for c in lista) / n, 3)}

    resumo = {m: resumir(v) for m, v in por_modo.items()}
    resumo_tipo = {f"{m} | {t}": resumir(v) for (m, t), v in sorted(por_tipo.items())}
    SAIDA.write_text(json.dumps({"resumo": resumo, "por_tipo": resumo_tipo,
                                 "detalhes": detalhes}, ensure_ascii=False, indent=1,
                                default=str), encoding="utf-8")
    print("\nRESUMO (acerto = media; alucinacao = respondeu algo errado ou inventado)")
    print(f"{'modo':<14}{'n':>4}{'acerto':>9}{'alucin.':>9}{'absten.':>9}")
    for m, s in resumo.items():
        print(f"{m:<14}{s['n']:>4}{s['acerto']:>9.2f}{s['taxa_alucinacao']:>9.2f}"
              f"{s['taxa_abstencao']:>9.2f}")
    print("\nPOR TIPO DE PERGUNTA")
    for k, s in resumo_tipo.items():
        print(f"  {k:<32} n={s['n']:<3} acerto={s['acerto']:.2f} "
              f"alucinacao={s['taxa_alucinacao']:.2f}")
    print(f"\nDetalhes em {SAIDA}")


if __name__ == "__main__":
    main()
