"""Fase 2 - Text-to-Graph: do comunicado do DOJ ao grafo no Neo4j.

Uso (na raiz do projeto):
    python scripts/fase2.py selecionar   # escolhe os comunicados
    python scripts/fase2.py extrair      # LLM: NER + RE com evidencia (retomavel)
    python scripts/fase2.py processar    # evidencia, resolucao, anonimizacao, trechos
    python scripts/fase2.py embeddings   # vetores dos trechos e casos (retomavel)
    python scripts/fase2.py ingerir      # MERGE no Neo4j
    python scripts/fase2.py relatorio    # numeros finais
    python scripts/fase2.py tudo         # todas as etapas em ordem
"""
import json
import math
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
INTERIM = RAIZ / "data" / "interim"
PRIVADO = RAIZ / "data" / "private"
RECORTE = INTERIM / "recorte_focado.json"
SELECAO = INTERIM / "selecao.json"
DIR_EXTR = INTERIM / "extracoes"
PROCESSADO = INTERIM / "processado.json"
EMBEDDINGS = INTERIM / "embeddings.json"
RELATORIO = INTERIM / "relatorio_fase2.json"
TABELA_PESSOAS = PRIVADO / "pessoas.json"   # nome real -> codigo (fica fora do Git)

N_CASOS = 200
TAM_TRECHO, SOBREPOSICAO = 1500, 150
DIM = 768
PAUSA_LLM = 4.5          # 15 pedidos/min no Flash-Lite gratuito
LOTE_EMB = 40          # Gemini Embedding gratuito: 30 mil tokens/min


def ler(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def gravar(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- selecionar
# Instituicoes conhecidas (regex) + bancos com nome proprio achados no texto.
CONHECIDAS = {
    "Bank of America": r"Bank of America|Countrywide", "JPMorgan Chase": r"JP ?Morgan|Chase Bank",
    "Wells Fargo": r"Wells Fargo|Wachovia", "Citigroup": r"Citi(?:group|bank|corp)",
    "HSBC": r"\bHSBC\b", "UBS": r"\bUBS\b", "Credit Suisse": r"Credit Suisse",
    "Deutsche Bank": r"Deutsche Bank", "Barclays": r"Barclays", "Goldman Sachs": r"Goldman Sachs",
    "Morgan Stanley": r"Morgan Stanley", "Washington Mutual": r"Washington Mutual|WaMu",
    "SunTrust": r"SunTrust", "PNC": r"\bPNC\b", "Regions Bank": r"Regions Bank",
    "TD Bank": r"\bTD Bank", "Capital One": r"Capital One", "U.S. Bank": r"U\.S\. Bank\b",
    "Fannie Mae": r"Fannie Mae", "Freddie Mac": r"Freddie Mac", "BNP Paribas": r"BNP Paribas",
    "Royal Bank of Scotland": r"Royal Bank of Scotland|\bRBS\b", "Standard Chartered": r"Standard Chartered",
    "Western Union": r"Western Union", "MoneyGram": r"MoneyGram", "Colonial Bank": r"Colonial Bank",
    "Taylor, Bean & Whitaker": r"Taylor,? Bean", "Stanford International Bank": r"Stanford International Bank",
    "Ocwen": r"\bOcwen\b", "Ally/GMAC": r"\bAlly Financial|\bGMAC\b", "SunTrust Mortgage": r"SunTrust Mortgage",
}
RE_CONHECIDAS = {k: re.compile(v) for k, v in CONHECIDAS.items()}
RE_BANCO = re.compile(r"\b((?:[A-Z][A-Za-z&\-]+ ){1,3}(?:Bank|Bancorp|Credit Union|Savings Bank)"
                      r"(?: N\.A\.)?)")
GENERICOS = {"the", "swiss", "foreign", "british", "israeli", "dutch", "federal", "export-import",
             "ex-im", "european", "state", "community", "branch", "three", "program", "national",
             "a", "first", "local", "victim", "one", "two", "central", "world", "reserve", "s"}
MAX_CHARS = 12000   # comunicados muito longos (acordos bilionarios) estouram o orcamento de tokens


def instituicoes_no_texto(texto):
    achadas = {k for k, rx in RE_CONHECIDAS.items() if rx.search(texto)}
    for m in RE_BANCO.findall(texto):
        palavras = m.strip().split()
        if palavras[0].lower() not in GENERICOS and len(palavras) >= 2:
            achadas.add(m.strip())
    return achadas


def selecionar():
    """Prioriza comunicados que citam instituicoes financeiras citadas tambem em outros
    comunicados: isso cria ligacoes entre casos e permite consultas multi-hop."""
    recorte = ler(RECORTE)
    inst_por_doc = [instituicoes_no_texto(r["contents"]) for r in recorte]
    freq = Counter(b for bs in inst_por_doc for b in bs)
    pontos = []
    for i, (r, bs) in enumerate(zip(recorte, inst_por_doc)):
        if len(r["contents"]) > MAX_CHARS:
            continue
        compartilhadas = [b for b in bs if freq[b] >= 2]
        pontos.append((len(compartilhadas), sum(min(freq[b], 20) for b in compartilhadas), i))
    pontos.sort(reverse=True)
    escolhidos = sorted(i for _, _, i in pontos[:N_CASOS])
    selecao = []
    for i in escolhidos:
        r = recorte[i]
        selecao.append({
            "caso_id": f"DOJ-{i + 1:05d}", "doj_id": r.get("id"), "titulo": r["title"].strip(),
            "data": r["date"][:10], "topicos": r.get("topics") or [],
            "componentes": r.get("components") or [], "texto": r["contents"].strip(),
            "instituicoes_regex": sorted(inst_por_doc[i]),
        })
    gravar(SELECAO, selecao)
    cobertura = Counter(b for c in selecao for b in c["instituicoes_regex"])
    print(f"[selecionar] {len(selecao)} comunicados; instituicoes em 2+ selecionados: "
          f"{sum(1 for v in cobertura.values() if v >= 2)}")
    print("  mais citadas:", ", ".join(f"{k} ({v})" for k, v in cobertura.most_common(8)))


# ------------------------------------------------------------------- extrair
def extrair():
    from f2_esquema import PROMPT, Extracao
    from llm import MODELO, criar_cliente, gerar_json, listar_modelos_texto, sugerir_modelo

    cliente = criar_cliente()
    modelo = MODELO or sugerir_modelo(listar_modelos_texto(cliente))
    selecao = ler(SELECAO)
    DIR_EXTR.mkdir(parents=True, exist_ok=True)
    print(f"[extrair] modelo {modelo}; {len(selecao)} comunicados (retoma de onde parou)")
    for rodada in (1, 2):   # a 2a rodada tenta de novo os que falharam
        if rodada == 2:
            faltam = [c for c in selecao if not (DIR_EXTR / f"{c['caso_id']}.json").exists()]
            if not faltam:
                break
            print(f"[extrair] nova tentativa para {len(faltam)} que falharam")
        parar = _rodada_extracao(cliente, modelo, selecao, PROMPT, Extracao, gerar_json)
        if parar:
            break
    total = len(list(DIR_EXTR.glob("*.json")))
    print(f"[extrair] prontos: {total}/{len(selecao)}")


def _rodada_extracao(cliente, modelo, selecao, PROMPT, Extracao, gerar_json):
    feitos = falhas = 0
    for n, caso in enumerate(selecao, 1):
        destino = DIR_EXTR / f"{caso['caso_id']}.json"
        if destino.exists():
            continue
        texto = f"{caso['titulo']}\n\n{caso['texto']}"
        try:
            res = gerar_json(cliente, modelo, PROMPT + texto, Extracao)
            gravar(destino, {"modelo": modelo, "extracao": res.model_dump()})
            feitos += 1
            print(f"  {n:>3}/{len(selecao)} {caso['caso_id']} ok "
                  f"({len(res.reus)} reus, {len(res.instituicoes)} instituicoes)")
        except Exception as erro:  # noqa: BLE001
            falhas += 1
            print(f"  {n:>3}/{len(selecao)} {caso['caso_id']} FALHOU: {str(erro)[:150]}")
            if "429" in str(erro) and "day" in str(erro).lower():
                print("  Limite diario atingido. Rode de novo amanha: o script retoma.")
                return True
        time.sleep(PAUSA_LLM)
    print(f"[extrair] novos: {feitos} | falhas: {falhas}")
    return False


# ----------------------------------------------------------------- processar
def normalizar(txt):
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", txt).strip()


def chave_org(nome):
    n = normalizar(nome).lower()
    n = re.sub(r"[\.,']", "", n)
    n = re.sub(r"\b(the|inc|incorporated|corp|corporation|llc|ltd|co|company|plc|"
               r"na|national association|fsb|ag|sa)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


# Resolucao de entidades: nomes diferentes da mesma instituicao -> mesma chave.
RESOLUCAO_INST = [
    ("bank of america", r"^bank of america"), ("jpmorgan chase", r"j\.? ?p\.? ?morgan"),
    ("wells fargo", r"^wells fargo"), ("citigroup", r"^citi(group|bank|corp)?\b"),
    ("hsbc", r"\bhsbc\b"), ("ubs", r"^ubs\b"), ("credit suisse", r"^credit suisse"),
    ("deutsche bank", r"^deutsche bank"), ("barclays", r"^barclays"),
    ("goldman sachs", r"^goldman sachs"), ("morgan stanley", r"^morgan stanley"),
    ("bnp paribas", r"bnp paribas"), ("standard chartered", r"^standard chartered"),
    ("royal bank of scotland", r"royal bank of scotland|^rbs\b"),
    ("fannie mae", r"fannie mae|federal national mortgage"),
    ("freddie mac", r"freddie mac|federal home loan mortgage"),
    ("us bank", r"^u\.?s\.? bank\b"), ("suntrust", r"^suntrust"), ("pnc", r"^pnc\b"),
]
RESOLUCAO_ORGAO = [
    ("FBI", r"\bfbi\b|federal bureau of investigation"),
    ("IRS", r"\birs\b|internal revenue service"),
    ("SEC", r"\bsec\b|securities and exchange commission"),
    ("HUD-OIG", r"\bhud\b|housing and urban development"),
    ("FHFA-OIG", r"\bfhfa\b|federal housing finance agency"),
    ("FDIC", r"\bfdic\b|federal deposit insurance"),
    ("USPIS", r"postal inspection|\buspis\b"),
    ("Secret Service", r"secret service|\busss\b"),
    ("SIGTARP", r"sigtarp|troubled asset relief"),
    ("ICE-HSI", r"homeland security investigations|\bhsi\b|\bice\b"),
    ("CFTC", r"\bcftc\b|commodity futures"),
    ("FinCEN", r"fincen|financial crimes enforcement"),
    ("OCC", r"\bocc\b|comptroller of the currency"),
    ("Federal Reserve", r"federal reserve"),
    ("DOJ Criminal Division", r"criminal division"),
    ("DOJ Tax Division", r"tax division"),
    ("DOJ Civil Division", r"civil division"),
    ("DOJ", r"^(the )?(u\.?s\.? )?(justice department|department of justice)( doj)?$"),
]


def chave_inst(nome):
    n = normalizar(nome).lower()
    for chave, rx in RESOLUCAO_INST:
        if re.search(rx, n):
            return chave
    n = chave_org(nome)
    return re.sub(r"\s*&$", "", n)


def chave_orgao(nome, sigla):
    n = normalizar(f"{nome} {sigla or ''}").lower()
    for chave, rx in RESOLUCAO_ORGAO:
        if re.search(rx, n):
            return chave
    return chave_org(nome)


def evidencia_ok(evid, texto_norm_lower):
    e = normalizar(evid or "").lower().strip(" .\"'")
    return len(e) >= 8 and e in texto_norm_lower


def chave_pessoa(nome):
    return re.sub(r"[^a-z ]", "", normalizar(nome).lower()).strip()


def dividir_trechos(texto):
    """Trechos de ~800 caracteres com 100 de sobreposicao, cortando em fim de frase."""
    frases = re.split(r"(?<=[\.\?!])\s+", texto)
    trechos, atual = [], ""
    for f in frases:
        if len(atual) + len(f) + 1 > TAM_TRECHO and atual:
            trechos.append(atual.strip())
            atual = atual[-SOBREPOSICAO:] + " " + f
        else:
            atual = (atual + " " + f).strip()
    if atual.strip():
        trechos.append(atual.strip())
    return trechos


class Codificador:
    """Atribui codigos estaveis a pessoas (Reu_001, Juiz_001...)."""
    PREFIXO = {"reu": "Reu", "juiz": "Juiz", "promotor": "Promotor", "agente": "Agente"}

    def __init__(self):
        self.tabela = {}
        self.cont = Counter()

    def codigo(self, papel, nome, contexto=""):
        # Reus: mesmo nome completo = mesma pessoa. Autoridades: nome + distrito.
        chave = f"{papel}|{chave_pessoa(nome)}|{contexto if papel != 'reu' else ''}"
        if chave not in self.tabela:
            self.cont[papel] += 1
            self.tabela[chave] = {"codigo": f"{self.PREFIXO[papel]}_{self.cont[papel]:03d}",
                                  "nome_real": nome}
        return self.tabela[chave]["codigo"]


def anonimizar(texto, nomes_codigos, protegidas=()):
    """Troca nome completo e sobrenome isolado pelo codigo.
    `protegidas`: palavras de nomes de organizacoes (ex.: 'Wells' em Wells Fargo),
    que nao podem ser trocadas mesmo que coincidam com um sobrenome."""
    for nome, cod in sorted(nomes_codigos, key=lambda x: -len(x[0])):
        texto = re.sub(re.escape(nome), cod, texto)
    for nome, cod in nomes_codigos:
        partes = nome.split()
        if partes and partes[-1].lower() in protegidas:
            continue
        if len(partes) >= 2 and len(partes[-1]) >= 3:
            texto = re.sub(rf"\b{re.escape(partes[-1])}\b(?![\w-])", cod, texto)
    return texto


def processar():
    selecao = {c["caso_id"]: c for c in ler(SELECAO)}
    cod = Codificador()
    casos, est = [], Counter()
    for arq in sorted(DIR_EXTR.glob("*.json")):
        caso = selecao[arq.stem]
        ext = ler(arq)["extracao"]
        if not ext["eh_fraude_financeira"]:
            est["descartado_nao_fraude"] += 1
            continue
        original = f"{caso['titulo']}\n\n{caso['texto']}"
        base = normalizar(original).lower()
        distrito = normalizar(ext.get("distrito") or "") or None

        def filtrar(itens, tipo):
            bons = []
            for it in itens:
                est[f"{tipo}_extraidos"] += 1
                if evidencia_ok(it.get("evidencia"), base):
                    est[f"{tipo}_com_evidencia_valida"] += 1
                    bons.append(it)
            return bons

        reus = filtrar(ext["reus"], "reu")
        insts = filtrar(ext["instituicoes"], "instituicao")
        emps = filtrar(ext["empresas"], "empresa")
        orgs = filtrar(ext["orgaos"], "orgao")
        juizes = filtrar(ext["juizes"], "juiz")
        proms = filtrar(ext["promotores"], "promotor")
        agentes = filtrar(ext["agentes"], "agente")

        nomes_codigos = []
        pessoas = []
        for papel, lista in (("reu", reus), ("juiz", juizes),
                             ("promotor", proms), ("agente", agentes)):
            for p in lista:
                c = cod.codigo(papel, p["nome"], distrito or "")
                nomes_codigos.append((p["nome"].strip(), c))
                pessoas.append({**p, "papel": papel, "codigo": c})

        protegidas = {w.lower() for o in insts + emps + orgs for w in o["nome"].split()}
        texto_anon = anonimizar(caso["texto"], nomes_codigos, protegidas)
        titulo_anon = anonimizar(caso["titulo"], nomes_codigos, protegidas)
        trechos = dividir_trechos(texto_anon)

        def localizar(evid):
            ev = normalizar(anonimizar(evid, nomes_codigos, protegidas)).lower()[:60]
            for i, t in enumerate(trechos):
                if ev and ev in normalizar(t).lower():
                    return i
            return 0

        for p in pessoas:
            p["evidencia"] = anonimizar(p["evidencia"], nomes_codigos, protegidas)
            p["trecho"] = localizar(p["evidencia"])
            p.pop("nome", None)
            if p.get("empresa"):
                p["empresa_chave"] = chave_org(p["empresa"])
        for lst, f_chave in ((insts, lambda o: chave_inst(o["nome"])),
                             (emps, lambda o: chave_org(o["nome"])),
                             (orgs, lambda o: chave_orgao(o["nome"], o.get("sigla")))):
            for o in lst:
                o["evidencia"] = anonimizar(o["evidencia"], nomes_codigos, protegidas)
                o["trecho"] = localizar(o["evidencia"])
                o["chave"] = f_chave(o)
            # o mesmo comunicado pode citar a mesma instituicao duas vezes
            unicos = {}
            for o in lst:
                unicos.setdefault(o["chave"], o)
            lst[:] = list(unicos.values())
        casos.append({
            "caso_id": caso["caso_id"], "doj_id": caso["doj_id"], "titulo": titulo_anon,
            "data": caso["data"], "ano": int(caso["data"][:4]),
            "topicos": caso["topicos"], "componentes": caso["componentes"],
            "texto_anon": texto_anon, "distrito": distrito,
            "valor_esquema_usd": ext.get("valor_esquema_usd"),
            "esquemas": sorted(set(ext["esquemas"])), "crimes": sorted(set(ext["crimes"])),
            "pessoas": pessoas, "instituicoes": insts, "empresas": emps, "orgaos": orgs,
            "trechos": trechos,
        })
    gravar(PROCESSADO, casos)
    gravar(TABELA_PESSOAS, list(cod.tabela.values()))
    gravar(RELATORIO, {"processamento": dict(est), "casos_validos": len(casos)})
    print(f"[processar] casos validos: {len(casos)} | descartados (nao fraude): "
          f"{est['descartado_nao_fraude']}")
    for tipo in ("reu", "instituicao", "empresa", "orgao", "juiz", "promotor", "agente"):
        tot, ok = est[f"{tipo}_extraidos"], est[f"{tipo}_com_evidencia_valida"]
        if tot:
            print(f"  {tipo:<12} {ok}/{tot} com evidencia confirmada no texto ({ok / tot:.0%})")


# ---------------------------------------------------------------- embeddings
def modelo_embedding(cliente):
    import os
    if os.getenv("GEMINI_EMBED_MODEL"):
        return os.getenv("GEMINI_EMBED_MODEL")
    nomes = [m.name.removeprefix("models/") for m in cliente.models.list()
             if "embedContent" in (getattr(m, "supported_actions", None) or [])]
    for preferido in ("gemini-embedding-001", "text-embedding-004"):
        if preferido in nomes:
            return preferido
    estaveis = [n for n in nomes if "embedding" in n and "exp" not in n and "preview" not in n]
    return sorted(estaveis or nomes)[-1]


def normalizado(v):
    norma = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norma for x in v]


def embeddings():
    """Um vetor de 768 dimensoes por trecho (o do caso e a media dos seus trechos)."""
    from google.genai import errors, types

    from llm import criar_cliente

    cliente = criar_cliente()
    modelo = modelo_embedding(cliente)
    casos = ler(PROCESSADO)
    feitos = ler(EMBEDDINGS) if EMBEDDINGS.exists() else {}
    itens = [(f"{c['caso_id']}-T{i:02d}", t) for c in casos for i, t in enumerate(c["trechos"])]
    pendentes = [it for it in itens if it[0] not in feitos]
    print(f"[embeddings] modelo {modelo}; {len(pendentes)} de {len(itens)} trechos pendentes")
    i = 0
    espera = 30
    while i < len(pendentes):
        lote = pendentes[i:i + LOTE_EMB]
        try:
            resp = cliente.models.embed_content(
                model=modelo, contents=[t for _, t in lote],
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT",
                                                 output_dimensionality=DIM))
        except errors.APIError as erro:
            if erro.code == 429:
                print(f"  limite de uso; aguardando {espera}s...")
                time.sleep(espera)
                espera = min(espera * 2, 300)
                continue
            raise
        espera = 30
        for (chave, _), emb in zip(lote, resp.embeddings):
            feitos[chave] = normalizado(emb.values)
        gravar(EMBEDDINGS, feitos)
        i += len(lote)
        print(f"  {i}/{len(pendentes)}")
        if i < len(pendentes):
            tokens = sum(len(t) for _, t in lote) / 3.5
            time.sleep(tokens / 30000 * 60 + 2)
    for c in casos:   # vetor do caso = media normalizada dos trechos
        vs = [feitos[f"{c['caso_id']}-T{i:02d}"] for i in range(len(c["trechos"]))
              if f"{c['caso_id']}-T{i:02d}" in feitos]
        if vs:
            feitos[c["caso_id"]] = normalizado([sum(x) / len(vs) for x in zip(*vs)])
    gravar(EMBEDDINGS, feitos)
    print(f"[embeddings] prontos: {len(feitos)} vetores ({DIM} dimensoes)")


# ------------------------------------------------------------------- ingerir
Q_CASO = """
MERGE (c:Caso {caso_id: $c.caso_id})
SET c.doj_id = $c.doj_id, c.titulo = $c.titulo, c.data = date($c.data), c.ano = $c.ano,
    c.topicos = $c.topicos, c.componentes = $c.componentes, c.texto_anon = $c.texto_anon,
    c.valor_esquema_usd = $c.valor_esquema_usd, c.embedding = $emb_caso
WITH c
FOREACH (_ IN CASE WHEN $c.distrito IS NULL THEN [] ELSE [1] END |
  MERGE (d:Distrito {nome: $c.distrito}) MERGE (c)-[:JULGADO_EM]->(d))
FOREACH (e IN $c.esquemas | MERGE (x:Esquema {nome: e}) MERGE (c)-[:USA_ESQUEMA]->(x))
FOREACH (k IN $c.crimes | MERGE (x:Crime {nome: k}) MERGE (c)-[:ENVOLVE_CRIME]->(x))
"""
Q_TRECHOS = """
MATCH (c:Caso {caso_id: $caso_id})
UNWIND $trechos AS t
MERGE (tr:Trecho {trecho_id: t.trecho_id})
SET tr.caso_id = $caso_id, tr.ordem = t.ordem, tr.texto = t.texto, tr.embedding = t.emb
MERGE (c)-[r:TEM_TRECHO]->(tr) SET r.ordem = t.ordem
"""
Q_PROXIMO = """
MATCH (:Caso {caso_id: $caso_id})-[:TEM_TRECHO]->(t:Trecho)
WITH t ORDER BY t.ordem
WITH collect(t) AS ts
UNWIND range(0, size(ts) - 2) AS i
WITH ts[i] AS a, ts[i + 1] AS b
MERGE (a)-[:PROXIMO]->(b)
"""
Q_PESSOA = """
MATCH (c:Caso {caso_id: $caso_id})
UNWIND $pessoas AS p
MERGE (x:Pessoa {codigo: p.codigo})
WITH c, p, x, $caso_id + '-T' + right('0' + toString(p.trecho), 2) AS tid
FOREACH (_ IN CASE WHEN p.papel = 'reu' THEN [1] ELSE [] END |
  SET x:Reu
  MERGE (x)-[r:REU_EM]->(c)
  SET r.desfecho = p.desfecho, r.pena_meses = p.pena_meses,
      r.restituicao_usd = p.restituicao_usd, r.confisco_usd = p.confisco_usd,
      r.evidencia = p.evidencia, r.trecho_id = tid, r.metodo = 'llm')
FOREACH (_ IN CASE WHEN p.papel = 'juiz' THEN [1] ELSE [] END |
  SET x:Juiz
  MERGE (x)-[r:JULGOU]->(c) SET r.evidencia = p.evidencia, r.trecho_id = tid, r.metodo = 'llm')
FOREACH (_ IN CASE WHEN p.papel = 'promotor' THEN [1] ELSE [] END |
  SET x:Promotor
  MERGE (x)-[r:CONDUZIU]->(c) SET r.evidencia = p.evidencia, r.trecho_id = tid, r.metodo = 'llm')
FOREACH (_ IN CASE WHEN p.papel = 'agente' THEN [1] ELSE [] END |
  SET x:Agente
  MERGE (x)-[r:ATUOU_EM]->(c) SET r.evidencia = p.evidencia, r.trecho_id = tid, r.metodo = 'llm')
WITH x, tid
MATCH (tr:Trecho {trecho_id: tid})
MERGE (tr)-[:MENCIONA]->(x)
"""
Q_INST = """
MATCH (c:Caso {caso_id: $caso_id})
UNWIND $itens AS i
MERGE (x:InstituicaoFinanceira {nome: i.chave})
ON CREATE SET x.nome_exibicao = i.nome, x.tipo = i.tipo, x.aliases = [i.nome]
ON MATCH SET x.aliases = CASE WHEN i.nome IN x.aliases THEN x.aliases ELSE x.aliases + i.nome END
MERGE (c)-[r:ENVOLVE_INSTITUICAO]->(x)
SET r.papel = i.papel, r.evidencia = i.evidencia, r.metodo = 'llm',
    r.trecho_id = $caso_id + '-T' + right('0' + toString(i.trecho), 2)
WITH x, i
MATCH (tr:Trecho {trecho_id: $caso_id + '-T' + right('0' + toString(i.trecho), 2)})
MERGE (tr)-[:MENCIONA]->(x)
"""
Q_EMP = """
MATCH (c:Caso {caso_id: $caso_id})
UNWIND $itens AS i
MERGE (x:Empresa {nome: i.chave})
ON CREATE SET x.nome_exibicao = i.nome, x.aliases = [i.nome]
MERGE (c)-[r:ENVOLVE_EMPRESA]->(x)
SET r.papel = i.papel, r.evidencia = i.evidencia, r.metodo = 'llm'
WITH x, i
MATCH (tr:Trecho {trecho_id: $caso_id + '-T' + right('0' + toString(i.trecho), 2)})
MERGE (tr)-[:MENCIONA]->(x)
"""
Q_ORG = """
MATCH (c:Caso {caso_id: $caso_id})
UNWIND $itens AS i
MERGE (x:Orgao {nome: i.chave})
ON CREATE SET x.nome_exibicao = i.nome, x.sigla = i.sigla
MERGE (x)-[r:INVESTIGOU]->(c)
SET r.evidencia = i.evidencia, r.metodo = 'llm'
WITH x, i
MATCH (tr:Trecho {trecho_id: $caso_id + '-T' + right('0' + toString(i.trecho), 2)})
MERGE (tr)-[:MENCIONA]->(x)
"""
Q_VINCULO = """
UNWIND $itens AS i
MATCH (r:Pessoa {codigo: i.codigo}), (e:Empresa {nome: i.empresa_chave})
MERGE (r)-[:VINCULADO_A]->(e)
"""


def ingerir():
    from conexao import BANCO, criar_driver

    casos = ler(PROCESSADO)
    embs = ler(EMBEDDINGS) if EMBEDDINGS.exists() else {}
    print(f"[ingerir] {len(casos)} casos -> Neo4j")
    with criar_driver() as driver, driver.session(database=BANCO) as s:
        # Recarga completa: os codigos das pessoas sao recalculados a cada 'processar',
        # entao o grafo antigo e apagado para nao misturar codigos de rodadas diferentes.
        antes = s.run("MATCH (n) RETURN count(n) AS q").single()["q"]
        if antes:
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS").consume()
            print(f"  grafo anterior apagado ({antes} nos)")
        for n, c in enumerate(casos, 1):
            cid = c["caso_id"]
            trechos = [{"trecho_id": f"{cid}-T{i:02d}", "ordem": i, "texto": t,
                        "emb": embs.get(f"{cid}-T{i:02d}")} for i, t in enumerate(c["trechos"])]
            caso_params = {k: c[k] for k in ("caso_id", "doj_id", "titulo", "data", "ano",
                                             "topicos", "componentes", "texto_anon",
                                             "valor_esquema_usd", "distrito", "esquemas", "crimes")}

            def tx(t):
                t.run(Q_CASO, c=caso_params, emb_caso=embs.get(cid)).consume()
                t.run(Q_TRECHOS, caso_id=cid, trechos=trechos).consume()
                t.run(Q_PROXIMO, caso_id=cid).consume()
                t.run(Q_PESSOA, caso_id=cid, pessoas=c["pessoas"]).consume()
                t.run(Q_INST, caso_id=cid, itens=c["instituicoes"]).consume()
                t.run(Q_EMP, caso_id=cid, itens=c["empresas"]).consume()
                t.run(Q_ORG, caso_id=cid, itens=c["orgaos"]).consume()
                vinculos = [p for p in c["pessoas"] if p.get("empresa_chave")]
                t.run(Q_VINCULO, itens=vinculos).consume()

            s.execute_write(tx)
            if n % 20 == 0 or n == len(casos):
                print(f"  {n}/{len(casos)}")
    print("[ingerir] concluido")


# ----------------------------------------------------------------- relatorio
def relatorio():
    from conexao import BANCO, criar_driver

    with criar_driver() as driver, driver.session(database=BANCO) as s:
        nos = s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS q "
                    "ORDER BY q DESC").data()
        rels = s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS q "
                     "ORDER BY q DESC").data()
        sem_emb = s.run("MATCH (t:Trecho) WHERE t.embedding IS NULL "
                        "RETURN count(t) AS q").single()["q"]
        multi = s.run("MATCH (i:InstituicaoFinanceira)<-[:ENVOLVE_INSTITUICAO]-(c:Caso) "
                      "WITH i, count(c) AS n WHERE n >= 2 RETURN count(i) AS q").single()["q"]
    print("[relatorio] Nos por rotulo:")
    for r in nos:
        print(f"  {r['l']:<22} {r['q']}")
    print("Relacoes por tipo:")
    for r in rels:
        print(f"  {r['t']:<22} {r['q']}")
    print(f"Trechos sem embedding: {sem_emb}")
    print(f"Instituicoes presentes em 2+ casos (ligam casos): {multi}")
    rel = ler(RELATORIO) if RELATORIO.exists() else {}
    rel["grafo"] = {"nos": nos, "relacoes": rels, "trechos_sem_embedding": sem_emb,
                    "instituicoes_multicaso": multi}
    gravar(RELATORIO, rel)


ETAPAS = {"selecionar": selecionar, "extrair": extrair, "processar": processar,
          "embeddings": embeddings, "ingerir": ingerir, "relatorio": relatorio}

if __name__ == "__main__":
    from registro import espelhar
    espelhar("fase2.log")
    etapa = sys.argv[1] if len(sys.argv) > 1 else "tudo"
    print(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} - etapa: {etapa} =====")
    if etapa == "tudo":
        for nome in ETAPAS:
            ETAPAS[nome]()
    elif etapa in ETAPAS:
        ETAPAS[etapa]()
    else:
        sys.exit(__doc__)
