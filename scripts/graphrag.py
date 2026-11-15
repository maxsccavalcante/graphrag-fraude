"""Fase 3 - Motor GraphRAG/KAG: busca hibrida + raciocinio no grafo + resposta fundamentada.

Fluxo de uma pergunta (2 chamadas ao LLM):
 1. Ligacao de entidades: nomes da pergunta -> nos do grafo (indice fulltext) e
    codigos citados (DOJ-00025, Reu_001...).
 2. PLANO (LLM): forma logica da pergunta = consulta Cypher SOMENTE LEITURA
    (Text2Cypher, o lado "KAG") + termos de busca em ingles.
 3. Execucao do Cypher em transacao de leitura, com bloqueio de escrita e LIMIT.
 4. Busca hibrida nos trechos: vetorial (embeddings) + fulltext (BM25/Lucene),
    fundidas por Reciprocal Rank Fusion (RRF).
 5. Expansao no grafo (multi-hop): casos dos trechos -> reus, instituicoes,
    orgaos, esquemas -> outros casos que compartilham a mesma instituicao.
 6. RESPOSTA (LLM): so com o contexto numerado; cada afirmacao cita [ID].
 7. Verificacao: citacoes inexistentes sao removidas e reduzem a confianca;
    sem contexto ou sem citacao valida -> "nao encontrado".

Modos (para a avaliacao): "graphrag", "rag_vetorial" (so passo 4 vetorial) e
"llm_puro" (sem contexto nenhum).
"""
import json
import math
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import neo4j
from pydantic import BaseModel, Field

from conexao import BANCO, criar_driver
from llm import MODELO, criar_cliente, gerar_json, listar_modelos_texto, sugerir_modelo

K_TRECHOS = 6
K_RRF = 60
LIMITE_LINHAS = 50
NAO_ENCONTRADO = ("Nao encontrei essa informacao nos comunicados do grafo. "
                  "Prefiro nao responder a inventar.")

ESQUEMA_GRAFO = """
NOS (rotulo {propriedades}):
- Caso {caso_id: 'DOJ-00025', titulo, data: DATE, ano: INTEGER, valor_esquema_usd: FLOAT,
        doj_id, pagerank, comunidade}
- Trecho {trecho_id: 'DOJ-00025-T01', caso_id, ordem, texto}   (pedacos do comunicado)
- Pessoa {codigo, pagerank, comunidade} com um rotulo extra: Reu | Juiz | Promotor | Agente.
  Codigos: 'Reu_001', 'Juiz_001', 'Promotor_001', 'Agente_001' (nomes reais anonimizados).
- InstituicaoFinanceira {nome (chave normalizada em minusculas, ex.: 'ubs', 'bank of america',
        'wells fargo', 'jpmorgan chase'), nome_exibicao (ex.: 'UBS AG'), tipo, aliases, pagerank,
        comunidade}
- Empresa {nome (minusculas), nome_exibicao}
- Orgao {nome (sigla: 'FBI','IRS','SEC','HUD-OIG','FHFA-OIG','FDIC','USPIS','Secret Service',
        'SIGTARP','ICE-HSI','CFTC','FinCEN','OCC','DOJ Criminal Division','DOJ Tax Division'...),
        nome_exibicao, sigla}
- Distrito {nome, ex.: 'Southern District of New York'}
- Esquema {nome}: 'Fraude hipotecaria','Esquema Ponzi','Fraude de investimentos ou valores
        mobiliarios','Roubo de identidade','Fraude de cartao','Fraude de cheques','Fraude de
        emprestimos','Desvio por funcionario (insider)','Lavagem de dinheiro','Evasao fiscal ou
        contas offshore','Fraude eletronica (phishing ou invasao)','Outro'
- Crime {nome}: 'bank fraud','wire fraud','mail fraud','money laundering','conspiracy',
        'securities fraud','Bank Secrecy Act violation','tax evasion','identity theft',
        'false statements','bribery','embezzlement','other'

RELACOES:
- (:Reu)-[:REU_EM {desfecho: 'denunciado'|'confessou'|'condenado'|'sentenciado'|'acordo'|'outro',
        pena_meses, restituicao_usd, confisco_usd, evidencia}]->(:Caso)
- (:Juiz)-[:JULGOU]->(:Caso)   (:Promotor)-[:CONDUZIU]->(:Caso)   (:Agente)-[:ATUOU_EM]->(:Caso)
- (:Caso)-[:ENVOLVE_INSTITUICAO {papel: 'vitima'|'acusada'|'intermediaria', evidencia}]
        ->(:InstituicaoFinanceira)
- (:Caso)-[:ENVOLVE_EMPRESA {papel}]->(:Empresa)     (:Pessoa)-[:VINCULADO_A]->(:Empresa)
- (:Orgao)-[:INVESTIGOU {evidencia}]->(:Caso)
- (:Caso)-[:JULGADO_EM]->(:Distrito)   (:Caso)-[:USA_ESQUEMA]->(:Esquema)
- (:Caso)-[:ENVOLVE_CRIME]->(:Crime)
- (:Caso)-[:TEM_TRECHO]->(:Trecho)-[:PROXIMO]->(:Trecho)   (:Trecho)-[:MENCIONA]->(entidade)
"""


# ------------------------------------------------------------------ recursos
@lru_cache(maxsize=1)
def driver():
    return criar_driver()


@lru_cache(maxsize=1)
def cliente_llm():
    return criar_cliente()


@lru_cache(maxsize=1)
def modelo_llm():
    return MODELO or sugerir_modelo(listar_modelos_texto(cliente_llm()))


@lru_cache(maxsize=1)
def modelo_emb():
    from fase2 import modelo_embedding
    return modelo_embedding(cliente_llm())


def ler_cypher(query, **params):
    """Executa Cypher numa transacao de LEITURA (o banco recusa qualquer escrita)."""
    with driver().session(database=BANCO, default_access_mode=neo4j.READ_ACCESS,
                          notifications_disabled_classifications=["DEPRECATION"]) as s:
        @neo4j.unit_of_work(timeout=20)
        def _ler(tx):
            return tx.run(query, **params).data()
        return s.execute_read(_ler)


CACHE_EMB = Path(__file__).resolve().parent.parent / "data" / "interim" / "cache_consultas.json"
_cache_emb = None


def embed_pergunta(texto):
    """Vetor da pergunta, com cache em disco (economiza a cota diaria de embeddings)."""
    global _cache_emb
    from google.genai import types
    if _cache_emb is None:
        try:
            _cache_emb = json.loads(CACHE_EMB.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache_emb = {}
    if texto in _cache_emb:
        return _cache_emb[texto]
    resp = cliente_llm().models.embed_content(
        model=modelo_emb(), contents=[texto],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=768))
    v = resp.embeddings[0].values
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    _cache_emb[texto] = [x / n for x in v]
    try:
        CACHE_EMB.write_text(json.dumps(_cache_emb), encoding="utf-8")
    except OSError:
        pass
    return _cache_emb[texto]


# ---------------------------------------------------------- ligar entidades
RE_CODIGO = re.compile(r"\b(?:Reu|Juiz|Promotor|Agente)_\d{3}\b")
RE_CASO = re.compile(r"\bDOJ-\d{5}\b")


def lucene(texto):
    return re.sub(r'[+\-&|!(){}\[\]^"~*?:\\/]', " ", texto).strip()


def ligar_entidades(pergunta):
    achadas = []
    termos = re.findall(r"\b[A-Z][\w&\.\-]*(?:\s+(?:of\s+|&\s+)?[A-Z][\w&\.\-]*)*", pergunta)
    termos = [t for t in termos if len(t) > 2 and t not in ("Quais", "Qual", "Quantos",
                                                           "Quantas", "Quem", "Como", "Onde")]
    for t in termos[:5]:
        try:
            for r in ler_cypher("""
                CALL db.index.fulltext.queryNodes('organizacao_nome_ft', $q) YIELD node, score
                WHERE score > 0.5
                RETURN labels(node)[0] AS rotulo, node.nome AS nome,
                       node.nome_exibicao AS exibicao, score LIMIT 3""", q=lucene(t)):
                achadas.append(r)
        except neo4j.exceptions.Neo4jError:
            pass
    codigos = RE_CODIGO.findall(pergunta)
    casos = RE_CASO.findall(pergunta)
    return achadas, codigos, casos


# --------------------------------------------------------------- plano (KAG)
class Plano(BaseModel):
    cypher: Optional[str] = Field(
        None, description="Consulta Cypher SOMENTE LEITURA (MATCH/OPTIONAL MATCH/WITH/WHERE/"
                          "RETURN/ORDER BY/LIMIT) que responde a pergunta, ou null se nao der.")
    termos_busca_ingles: str = Field(
        description="Palavras-chave em INGLES para busca textual nos comunicados.")
    tipo: Literal["fato_simples", "multi_hop", "agregacao", "fora_do_escopo"]


PROMPT_PLANO = """Voce traduz perguntas em portugues para consultas Cypher sobre um grafo de
fraudes financeiras construido a partir de comunicados do Departamento de Justica dos EUA.

ESQUEMA DO GRAFO:
{esquema}
REGRAS DO CYPHER:
- Somente leitura. Proibido CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV, CALL.
- Use os nomes de rotulos, relacoes e propriedades EXATAMENTE como no esquema.
- Compare nomes de instituicoes por i.nome (minusculas) ou com toLower(i.nome_exibicao) CONTAINS.
- Para listar, devolva os itens com DISTINCT e os casos que os comprovam, ex.:
  RETURN DISTINCT o.nome AS orgao, collect(DISTINCT c.caso_id) AS casos
- Para CONTAR, nunca devolva c.caso_id solto junto com count (isso agrupa por caso e da 1).
  Use: RETURN count(DISTINCT c) AS total, collect(DISTINCT c.caso_id)[0..20] AS casos
- Para achar um caso pelo titulo use toLower(c.titulo) CONTAINS 'trecho do titulo em minusculas'.
- Termine com LIMIT {limite}.
{entidades}
PERGUNTA: {pergunta}
"""


ESCRITA = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|CALL|"
                     r"FOREACH|GRANT|DENY|REVOKE|ALTER|RENAME)\b", re.I)


def validar_cypher(q):
    if not q:
        return None, "sem consulta"
    q = q.strip().rstrip(";")
    if ";" in q:
        return None, "mais de um comando"
    if ESCRITA.search(re.sub(r"'[^']*'|\"[^\"]*\"", "''", q)):
        return None, "consulta de escrita bloqueada"
    if not re.search(r"\bLIMIT\s+\d+\s*$", q, re.I):
        q += f" LIMIT {LIMITE_LINHAS}"
    return q, None


# --------------------------------------------------------- busca nos trechos
def busca_vetorial(vetor, k=K_TRECHOS * 2):
    return ler_cypher("""
        CALL db.index.vector.queryNodes('trecho_embedding_vec', $k, $v) YIELD node, score
        RETURN node.trecho_id AS id, node.caso_id AS caso_id, node.texto AS texto, score""",
                      k=k, v=vetor)


def busca_textual(termos, k=K_TRECHOS * 2):
    q = lucene(termos)
    if not q:
        return []
    return ler_cypher("""
        CALL db.index.fulltext.queryNodes('trecho_texto_ft', $q) YIELD node, score
        RETURN node.trecho_id AS id, node.caso_id AS caso_id, node.texto AS texto, score
        LIMIT $k""", q=q, k=k)


def rrf(*listas, k=K_RRF):
    """Reciprocal Rank Fusion: soma 1/(k + posicao) de cada lista."""
    pont, itens = {}, {}
    for lista in listas:
        for pos, it in enumerate(lista):
            pont[it["id"]] = pont.get(it["id"], 0.0) + 1.0 / (k + pos + 1)
            itens.setdefault(it["id"], it)
    ordem = sorted(pont, key=pont.get, reverse=True)
    return [{**itens[i], "rrf": pont[i]} for i in ordem]


# ------------------------------------------------------- expansao no grafo
def expandir(caso_ids):
    """Fatos dos casos + ligacoes multi-hop (outros casos com a mesma instituicao)."""
    if not caso_ids:
        return [], []
    fatos = ler_cypher("""
        MATCH (c:Caso) WHERE c.caso_id IN $ids
        OPTIONAL MATCH (c)-[:JULGADO_EM]->(d:Distrito)
        RETURN c.caso_id AS caso_id, c.titulo AS titulo, toString(c.data) AS data,
               c.valor_esquema_usd AS valor, d.nome AS distrito,
               [(r:Reu)-[x:REU_EM]->(c) | r.codigo + ' (' + coalesce(x.desfecho, '?') +
                  CASE WHEN x.pena_meses IS NULL THEN '' ELSE ', ' + toString(x.pena_meses) +
                  ' meses' END + ')'] AS reus,
               [(c)-[x:ENVOLVE_INSTITUICAO]->(i) | i.nome_exibicao + ' (' + coalesce(x.papel, '?') + ')'] AS insts,
               [(o:Orgao)-[:INVESTIGOU]->(c) | o.nome] AS orgaos,
               [(c)-[:USA_ESQUEMA]->(e) | e.nome] AS esquemas,
               [(c)-[:ENVOLVE_CRIME]->(k) | k.nome] AS crimes""", ids=list(caso_ids))
    ligacoes = ler_cypher("""
        MATCH (c:Caso)-[:ENVOLVE_INSTITUICAO]->(i:InstituicaoFinanceira)<-[:ENVOLVE_INSTITUICAO]-(o:Caso)
        WHERE c.caso_id IN $ids AND NOT o.caso_id IN $ids
        WITH i, collect(DISTINCT o.caso_id)[0..5] AS outros, count(DISTINCT o) AS n
        RETURN i.nome_exibicao AS instituicao, n, outros ORDER BY n DESC LIMIT 5""",
                          ids=list(caso_ids))
    return fatos, ligacoes


def subgrafo(caso_ids):
    """Nos e arestas usados na resposta (para desenhar na interface)."""
    if not caso_ids:
        return []
    return ler_cypher("""
        MATCH (c:Caso) WHERE c.caso_id IN $ids
        MATCH (c)-[r:REU_EM|JULGOU|CONDUZIU|ATUOU_EM|ENVOLVE_INSTITUICAO|ENVOLVE_EMPRESA|
                    INVESTIGOU|JULGADO_EM|USA_ESQUEMA]-(n)
        RETURN elementId(c) AS a, c.caso_id AS a_nome, 'Caso' AS a_tipo,
               elementId(n) AS b, coalesce(n.nome_exibicao, n.codigo, n.nome) AS b_nome,
               [l IN labels(n) WHERE l <> 'Pessoa'][0] AS b_tipo, type(r) AS rel,
               r.evidencia AS evidencia LIMIT 300""", ids=list(caso_ids))


# ------------------------------------------------------------------ resposta
class Resposta(BaseModel):
    encontrado: bool = Field(description="False se o CONTEXTO nao responde a pergunta.")
    resposta: str = Field(description="Resposta em portugues; cada frase termina com [ID] "
                                      "das fontes do contexto que a sustentam.")
    citacoes: list[str] = Field(description="IDs do contexto usados (ex.: DOJ-00025, "
                                            "DOJ-00025-T01, Q1).")


PROMPT_RESPOSTA = """Voce responde perguntas sobre fraudes financeiras usando APENAS o CONTEXTO.
Regras:
1. Nao use conhecimento externo. Se o contexto nao responde, encontrado = false.
2. Cada frase da resposta termina com o(s) ID(s) entre colchetes, ex.: [DOJ-00025] ou [Q1].
3. Pessoas aparecem como codigos (Reu_001...). Nunca tente adivinhar nomes reais.
4. Seja direto: liste os itens pedidos; em perguntas de contagem, de o numero.

CONTEXTO:
{contexto}

PERGUNTA: {pergunta}
"""

PROMPT_LLM_PURO = """Responda a pergunta sobre fraudes financeiras investigadas pelo Departamento
de Justica dos EUA com o seu conhecimento. Se nao souber, encontrado = false.
Deixe citacoes vazia.

PERGUNTA: {pergunta}
"""


def montar_contexto(linhas_cypher, trechos, fatos, ligacoes):
    partes, ids = [], set()
    for i, l in enumerate(linhas_cypher, 1):
        partes.append(f"[Q{i}] (resultado da consulta ao grafo) {l}")
        ids.add(f"Q{i}")
    for f in fatos:
        partes.append(
            f"[{f['caso_id']}] Caso '{f['titulo']}' ({f['data']}); distrito: {f['distrito']}; "
            f"valor: {f['valor']}; reus: {', '.join(f['reus']) or '-'}; "
            f"instituicoes: {', '.join(f['insts']) or '-'}; orgaos: {', '.join(f['orgaos']) or '-'}; "
            f"esquemas: {', '.join(f['esquemas']) or '-'}; crimes: {', '.join(f['crimes']) or '-'}")
        ids.add(f["caso_id"])
    for lg in ligacoes:
        partes.append(f"[REL] {lg['instituicao']} tambem aparece em {lg['n']} outro(s) caso(s): "
                      f"{', '.join(lg['outros'])}")
    for t in trechos:
        partes.append(f"[{t['id']}] {t['texto']}")
        ids.add(t["id"])
    return "\n".join(partes), ids


def verificar(resp, ids_validos):
    """Remove citacoes inventadas e mede quanto da resposta esta fundamentado."""
    citadas = set(re.findall(r"\[([A-Za-z0-9\-_, ]+)\]", resp.resposta))
    citadas = {c.strip() for grupo in citadas for c in grupo.split(",")} | set(resp.citacoes)
    validas = {c for c in citadas if c in ids_validos
               or any(i.startswith(c + "-T") for i in ids_validos)}   # caso cujo trecho esta no contexto
    invalidas = citadas - validas - {"REL"}
    frases = [f for f in re.split(r"(?<=[\.\!\?])\s+", resp.resposta.strip()) if len(f) > 3]
    com_cit = [f for f in frases if any(v in f for v in validas)]
    return validas, invalidas, (len(com_cit) / len(frases) if frases else 0.0)


def confianca(resp, validas, invalidas, frac_frases, n_linhas_cypher, melhor_sim):
    if not resp.encontrado or not validas:
        return 0.0
    precisao_cit = len(validas) / (len(validas) + len(invalidas))
    score = (0.45 * frac_frases * precisao_cit + 0.30 * (1.0 if n_linhas_cypher else 0.0)
             + 0.25 * max(0.0, min(1.0, (melhor_sim - 0.5) / 0.4)))
    return round(score, 2)


def rotulo_conf(c):
    return "alta" if c >= 0.7 else "media" if c >= 0.4 else "baixa"


# ------------------------------------------------------------------- principal
def responder(pergunta, modo="graphrag", tolerante=True):
    """tolerante=True (interface): se a cota de embeddings acabar, segue so com fulltext + grafo.
    tolerante=False (avaliacao): propaga o erro, para a pergunta ser refeita depois."""
    t0 = time.time()
    cliente, modelo = cliente_llm(), modelo_llm()
    saida = {"pergunta": pergunta, "modo": modo, "cypher": None, "erro_cypher": None,
             "linhas_cypher": [], "trechos": [], "fatos": [], "ligacoes": [],
             "entidades": [], "casos_usados": []}

    if modo == "llm_puro":
        resp = gerar_json(cliente, modelo, PROMPT_LLM_PURO.format(pergunta=pergunta), Resposta)
        saida.update(resposta=resp.resposta if resp.encontrado else NAO_ENCONTRADO,
                     encontrado=resp.encontrado, citacoes=[], citacoes_invalidas=[],
                     confianca=None, confianca_rotulo="sem fontes",
                     segundos=round(time.time() - t0, 1))
        return saida

    try:
        vet = busca_vetorial(embed_pergunta(pergunta))
    except Exception as e:  # noqa: BLE001 - cota/rede do servico de embeddings
        if not tolerante:
            raise
        vet = []
        saida["aviso"] = f"Busca vetorial indisponivel agora ({str(e)[:80]}); usando fulltext + grafo."
    melhor_sim = vet[0]["score"] if vet else 0.0

    if modo == "rag_vetorial":
        trechos = vet[:K_TRECHOS]
        linhas, fatos, ligacoes = [], [], []
    else:
        achadas, codigos, casos = ligar_entidades(pergunta)
        saida["entidades"] = achadas + [{"codigo": c} for c in codigos + casos]
        dicas = ""
        if achadas or codigos or casos:
            dicas = "ENTIDADES DA PERGUNTA ENCONTRADAS NO GRAFO:\n" + "\n".join(
                [f"- {a['rotulo']} {{nome: '{a['nome']}', nome_exibicao: '{a['exibicao']}'}}"
                 for a in achadas] + [f"- codigo '{c}'" for c in codigos + casos]) + "\n"
        plano = gerar_json(cliente, modelo, PROMPT_PLANO.format(
            esquema=ESQUEMA_GRAFO, limite=LIMITE_LINHAS, entidades=dicas, pergunta=pergunta), Plano)
        linhas = []
        if plano.tipo != "fora_do_escopo":
            q, erro = validar_cypher(plano.cypher)
            saida["cypher"], saida["erro_cypher"] = q or plano.cypher, erro
            if q:
                try:
                    linhas = ler_cypher(q)[:LIMITE_LINHAS]
                except neo4j.exceptions.Neo4jError as e:
                    saida["erro_cypher"] = f"{e.code}: {str(e.message)[:200]}"
        saida["tipo"] = plano.tipo
        txt = busca_textual(plano.termos_busca_ingles)
        trechos = rrf(vet, txt)[:K_TRECHOS]
        casos_cypher = {v for l in linhas for v in l.values()
                        if isinstance(v, str) and RE_CASO.fullmatch(v)}
        casos_cypher |= {v for l in linhas for vs in l.values() if isinstance(vs, list)
                         for v in vs if isinstance(v, str) and RE_CASO.fullmatch(v)}
        caso_ids = list(dict.fromkeys(list(casos_cypher)[:8] +
                                      [t["caso_id"] for t in trechos]))[:10]
        fatos, ligacoes = expandir(caso_ids)

    contexto, ids_validos = montar_contexto(linhas, trechos, fatos, ligacoes)
    if not contexto.strip():
        resp = Resposta(encontrado=False, resposta=NAO_ENCONTRADO, citacoes=[])
    else:
        resp = gerar_json(cliente, modelo,
                          PROMPT_RESPOSTA.format(contexto=contexto, pergunta=pergunta), Resposta)
    validas, invalidas, frac = verificar(resp, ids_validos)
    conf = confianca(resp, validas, invalidas, frac, len(linhas), melhor_sim)
    encontrado = resp.encontrado and bool(validas)
    usados = sorted({v.split("-T")[0] for v in validas if v.startswith("DOJ-")} |
                    ({c for l in linhas for c in l.values() if isinstance(c, str)
                      and RE_CASO.fullmatch(c)} if validas else set()))
    saida.update(
        resposta=resp.resposta if encontrado else NAO_ENCONTRADO, encontrado=encontrado,
        citacoes=sorted(validas), citacoes_invalidas=sorted(invalidas),
        confianca=conf if encontrado else 0.0,
        confianca_rotulo=rotulo_conf(conf) if encontrado else "nao encontrado",
        linhas_cypher=linhas, trechos=trechos, fatos=fatos, ligacoes=ligacoes,
        casos_usados=usados, contexto=contexto, segundos=round(time.time() - t0, 1))
    return saida


if __name__ == "__main__":
    import json
    import sys
    pergunta = " ".join(sys.argv[1:]) or "Quais instituicoes financeiras aparecem em mais casos?"
    r = responder(pergunta)
    print(json.dumps({k: r[k] for k in ("resposta", "confianca", "confianca_rotulo", "cypher",
                                        "erro_cypher", "citacoes", "citacoes_invalidas",
                                        "casos_usados", "segundos")},
                     ensure_ascii=False, indent=2, default=str))
