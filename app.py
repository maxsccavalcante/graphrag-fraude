"""Fase 4 - Interface do GraphRAG de fraudes financeiras.

Rodar: .venv\\Scripts\\streamlit run app.py   (ou duplo clique em app_windows.bat)
"""
import html
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import graphrag as gr  # noqa: E402
from f3_algoritmos import menor_caminho  # noqa: E402

st.set_page_config(page_title="GraphRAG - Fraudes Financeiras", page_icon="🕸️", layout="wide")

CORES = {"Caso": "#4C78A8", "Reu": "#E45756", "Juiz": "#B279A2", "Promotor": "#9D755D",
         "Agente": "#BAB0AC", "InstituicaoFinanceira": "#F58518", "Empresa": "#EECA3B",
         "Orgao": "#54A24B", "Distrito": "#72B7B2", "Esquema": "#FF9DA6", "Crime": "#79706E"}
EXEMPLOS = [
    "Quais instituicoes financeiras aparecem em mais casos?",
    "Quais orgaos investigaram casos que envolvem o UBS?",
    "Quais outras instituicoes aparecem em casos junto com o Bank of America?",
    "Quais esquemas de fraude aparecem em casos do Southern District of New York?",
    "Quantos reus foram condenados em casos de fraude hipotecaria?",
    "Qual foi a pena do diretor do Banco Atlantida do Brasil?",
]


def desenhar(arestas, destaque=(), altura=560):
    net = Network(height=f"{altura}px", width="100%", directed=True, bgcolor="#ffffff",
                  cdn_resources="remote")
    net.barnes_hut(gravity=-6000, spring_length=140)
    vistos = set()
    for a in arestas:
        for nid, nome, tipo in ((a["a"], a["a_nome"], a["a_tipo"]),
                                (a["b"], a["b_nome"], a["b_tipo"])):
            if nid not in vistos:
                vistos.add(nid)
                net.add_node(nid, label=str(nome)[:40],
                             title=html.escape(f"{tipo}: {nome}"),
                             color=CORES.get(tipo, "#999999"),
                             size=26 if str(nome) in destaque else 16,
                             borderWidth=4 if str(nome) in destaque else 1)
        net.add_edge(a["a"], a["b"], title=html.escape(f"{a['rel']}: {a.get('evidencia') or ''}"),
                     label=a["rel"], font={"size": 9})
    # o HTML e gerado aqui pelo pyvis, com textos escapados
    if hasattr(st, "iframe"):
        st.iframe(net.generate_html(), height=altura + 20)
    else:
        components.html(net.generate_html(), height=altura + 20)


@st.cache_data(ttl=300)
def estatisticas():
    nos = gr.ler_cypher("MATCH (n) UNWIND labels(n) AS l RETURN l AS rotulo, count(*) AS qtd "
                        "ORDER BY qtd DESC")
    rels = gr.ler_cypher("MATCH ()-[r]->() RETURN count(r) AS q")[0]["q"]
    return nos, rels


# ------------------------------------------------------------------ cabecalho
st.title("🕸️ GraphRAG / KAG — Fraudes Financeiras (DOJ)")
st.caption("Projeto Final de Tendencias em Ciencia da Computacao — UNIPE. Grafo construido a "
           "partir de comunicados oficiais do Departamento de Justica dos EUA; pessoas anonimizadas.")

with st.sidebar:
    st.header("Grafo no Neo4j")
    try:
        nos, rels = estatisticas()
        st.metric("Relacoes", rels)
        st.dataframe(pd.DataFrame(nos), hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Sem conexao com o Neo4j: {e}")
    st.markdown("**Legenda**")
    st.markdown(" ".join(f"<span style='color:{c}'>●</span> {t}" for t, c in CORES.items()),
                unsafe_allow_html=True)

aba_perg, aba_expl, aba_aval = st.tabs(["💬 Perguntar", "🔎 Explorar o grafo", "📊 Avaliacao"])

# ------------------------------------------------------------------ perguntar
with aba_perg:
    col1, col2 = st.columns([3, 1])
    with col2:
        modo = st.radio("Modo", ["graphrag", "rag_vetorial", "llm_puro"],
                        format_func={"graphrag": "GraphRAG (grafo + hibrido)",
                                     "rag_vetorial": "RAG so vetorial",
                                     "llm_puro": "LLM sem contexto"}.get)
        exemplo = st.selectbox("Exemplos", [""] + EXEMPLOS)
    with col1:
        pergunta = st.text_area("Pergunta", value=exemplo, height=90,
                                placeholder="Ex.: Quais orgaos investigaram casos do UBS?")
        perguntar = st.button("Perguntar", type="primary")

    if perguntar and pergunta.strip():
        with st.spinner("Consultando o grafo e o Gemini..."):
            try:
                st.session_state["resultado"] = gr.responder(pergunta.strip(), modo)
            except Exception as e:  # noqa: BLE001
                st.session_state["resultado"] = None
                st.error(f"Erro: {e}")
    r = st.session_state.get("resultado")
    if r:
        rot = r["confianca_rotulo"]
        cor = {"alta": "green", "media": "orange", "baixa": "red"}.get(rot, "gray")
        valor = "" if r["confianca"] is None else f" ({r['confianca']:.2f})"
        st.markdown(f"### Resposta  :{cor}-background[confianca: {rot}{valor}]")
        if r.get("aviso"):
            st.warning(r["aviso"])
        st.write(r["resposta"])
        st.caption(f"Modo: {r['modo']} · {r['segundos']} s · citacoes validas: "
                   f"{', '.join(r['citacoes']) or '-'}"
                   + (f" · citacoes descartadas (inexistentes): {', '.join(r['citacoes_invalidas'])}"
                      if r.get("citacoes_invalidas") else ""))

        if r["modo"] == "graphrag":
            with st.expander("Consulta Cypher gerada (Text2Cypher, somente leitura)", expanded=True):
                st.code(r["cypher"] or "(nenhuma)", language="cypher")
                if r["erro_cypher"]:
                    st.warning(f"Consulta nao executada: {r['erro_cypher']}")
                if r["linhas_cypher"]:
                    st.dataframe(pd.DataFrame(r["linhas_cypher"]).astype(str),
                                 width="stretch")
        if r["modo"] != "llm_puro":
            c1, c2 = st.columns([3, 2])
            with c1:
                st.subheader("Subgrafo usado na resposta")
                usados = r["casos_usados"] or sorted({t["caso_id"] for t in r["trechos"]})[:5]
                arestas = gr.subgrafo(usados)
                if arestas:
                    desenhar(arestas, destaque=set(usados))
                else:
                    st.info("Nenhum caso no subgrafo.")
            with c2:
                st.subheader("Nos e trechos usados")
                if r["fatos"]:
                    st.dataframe(pd.DataFrame(r["fatos"])[["caso_id", "titulo", "distrito"]],
                                 hide_index=True, width="stretch")
                for t in r["trechos"]:
                    with st.expander(f"{t['id']} (score {t.get('rrf', t.get('score', 0)):.3f})"):
                        st.write(t["texto"])
                if r["ligacoes"]:
                    st.markdown("**Ligacoes multi-hop (mesma instituicao em outros casos)**")
                    st.dataframe(pd.DataFrame(r["ligacoes"]).astype(str), hide_index=True)

# ------------------------------------------------------------------ explorar
with aba_expl:
    st.subheader("Instituicoes mais centrais (PageRank)")
    try:
        top = gr.ler_cypher("""
            MATCH (i:InstituicaoFinanceira) WHERE i.pagerank IS NOT NULL
            RETURN i.nome_exibicao AS instituicao, round(i.pagerank, 5) AS pagerank,
                   i.comunidade AS comunidade,
                   COUNT { (i)<-[:ENVOLVE_INSTITUICAO]-() } AS casos
            ORDER BY pagerank DESC LIMIT 15""")
        if top:
            st.dataframe(pd.DataFrame(top), hide_index=True, width="stretch")
        else:
            st.info("Rode scripts/f3_algoritmos.py para calcular PageRank e Louvain.")

        st.subheader("Comunidades (Louvain)")
        coms = gr.ler_cypher("""
            MATCH (n) WHERE n.comunidade IS NOT NULL
            WITH n.comunidade AS comunidade, count(*) AS nos,
                 [x IN collect(n) WHERE x:InstituicaoFinanceira | x.nome_exibicao][0..5] AS instituicoes,
                 [x IN collect(n) WHERE x:Caso | x.caso_id] AS casos
            RETURN comunidade, nos, size(casos) AS casos, instituicoes
            ORDER BY nos DESC LIMIT 12""")
        if coms:
            st.dataframe(pd.DataFrame(coms).astype({"instituicoes": str}), hide_index=True,
                         width="stretch")
            escolha = st.selectbox("Ver comunidade", [c["comunidade"] for c in coms])
            casos_com = [c["caso_id"] for c in gr.ler_cypher(
                "MATCH (c:Caso {comunidade: $c}) RETURN c.caso_id AS caso_id "
                "ORDER BY c.pagerank DESC LIMIT 8", c=escolha)]
            if casos_com:
                desenhar(gr.subgrafo(casos_com), destaque=set(casos_com), altura=480)

        st.subheader("Menor caminho entre duas entidades (shortestPath)")
        entidades = gr.ler_cypher("""
            MATCH (n) WHERE (n:InstituicaoFinanceira OR n:Orgao OR n:Reu) AND n.pagerank IS NOT NULL
            RETURN elementId(n) AS id, coalesce(n.nome_exibicao, n.codigo) AS nome,
                   [l IN labels(n) WHERE l <> 'Pessoa'][0] AS tipo
            ORDER BY n.pagerank DESC LIMIT 300""")
        opcoes = {f"{e['nome']} ({e['tipo']})": e["id"] for e in entidades}
        if len(opcoes) >= 2:
            ca, cb = st.columns(2)
            a = ca.selectbox("De", list(opcoes), index=0)
            b = cb.selectbox("Para", list(opcoes), index=1)
            if st.button("Calcular caminho"):
                with gr.driver().session(database=gr.BANCO) as s:
                    nos_c, rels_c = menor_caminho(s, opcoes[a], opcoes[b])
                if not nos_c:
                    st.warning("Nao ha caminho entre essas entidades (componentes diferentes).")
                else:
                    st.success(" → ".join(n["nome"] for n in nos_c) +
                               f"   ({len(rels_c)} saltos)")
                    nome_de = {n["id"]: n for n in nos_c}
                    arestas = [{"a": r["de"], "a_nome": nome_de[r["de"]]["nome"],
                                "a_tipo": [l for l in nome_de[r["de"]]["rotulos"] if l != "Pessoa"][0],
                                "b": r["para"], "b_nome": nome_de[r["para"]]["nome"],
                                "b_tipo": [l for l in nome_de[r["para"]]["rotulos"] if l != "Pessoa"][0],
                                "rel": r["tipo"], "evidencia": r["evidencia"]} for r in rels_c]
                    desenhar(arestas, destaque={nos_c[0]["nome"], nos_c[-1]["nome"]}, altura=380)
    except Exception as e:  # noqa: BLE001
        st.error(f"Erro ao consultar o grafo: {e}")

# ------------------------------------------------------------------ avaliacao
with aba_aval:
    arq = Path(__file__).parent / "data" / "interim" / "avaliacao.json"
    if not arq.exists():
        st.info("Rode scripts/f3_avaliacao.py para gerar a comparacao GraphRAG x RAG x LLM.")
    else:
        aval = json.loads(arq.read_text(encoding="utf-8"))
        st.subheader("Resumo por modo")
        resumo = pd.DataFrame(aval["resumo"]).T
        st.dataframe(resumo, width="stretch")
        st.bar_chart(resumo[["acerto", "taxa_alucinacao"]])
        st.subheader("Respostas por pergunta")
        st.dataframe(pd.DataFrame(aval["detalhes"]), hide_index=True, width="stretch")
