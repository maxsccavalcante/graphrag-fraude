"""Fase 3 - Algoritmos de grafo: PageRank, Louvain e menor caminho.

O AuraDB Free nao tem o plugin GDS, entao:
- PageRank e Louvain rodam em Python (networkx) sobre uma projecao do grafo
  lida do Neo4j, e o resultado e GRAVADO de volta nos nos (pagerank, comunidade);
- o menor caminho usa o shortestPath nativo do Cypher (funcao menor_caminho).

Projecao: nos Caso, InstituicaoFinanceira, Pessoa, Orgao e Empresa; arestas sao as
relacoes semanticas entre eles. Trecho, Crime, Esquema e Distrito ficam de fora
(sao "hubs" de categoria que dominariam a centralidade).

Uso: python scripts/f3_algoritmos.py
"""
import time

import networkx as nx

from conexao import BANCO, criar_driver

REL_SEMANTICAS = ("REU_EM|JULGOU|CONDUZIU|ATUOU_EM|ENVOLVE_INSTITUICAO|"
                  "ENVOLVE_EMPRESA|INVESTIGOU|VINCULADO_A")
ROTULOS = ["Caso", "InstituicaoFinanceira", "Pessoa", "Orgao", "Empresa"]


def carregar_projecao(sessao):
    arestas = sessao.run(f"""
        MATCH (a)-[r:{REL_SEMANTICAS}]-(b)
        WHERE elementId(a) < elementId(b)
        RETURN elementId(a) AS a, elementId(b) AS b
    """).data()
    nos = sessao.run("""
        MATCH (n) WHERE any(l IN labels(n) WHERE l IN $rotulos)
        RETURN elementId(n) AS id, labels(n) AS rotulos,
               coalesce(n.nome_exibicao, n.titulo, n.codigo, n.nome) AS nome
    """, rotulos=ROTULOS).data()
    g = nx.Graph()
    for n in nos:
        g.add_node(n["id"], rotulos=n["rotulos"], nome=n["nome"])
    g.add_edges_from((e["a"], e["b"]) for e in arestas)
    return g


def pagerank_python(g, alpha=0.85, tol=1e-10, max_iter=200):
    """PageRank por iteracao de potencia (sem scipy). Grafo nao direcionado."""
    n = g.number_of_nodes()
    pr = dict.fromkeys(g, 1.0 / n)
    grau = dict(g.degree())
    for _ in range(max_iter):
        perdido = alpha * sum(pr[v] for v in g if grau[v] == 0) / n
        novo = {}
        for v in g:
            novo[v] = (1 - alpha) / n + perdido + alpha * sum(pr[u] / grau[u] for u in g[v])
        if sum(abs(novo[v] - pr[v]) for v in g) < n * tol:
            return novo
        pr = novo
    return pr


def calcular(g):
    try:
        pagerank = nx.pagerank(g, alpha=0.85)
    except ImportError:   # networkx usa scipy no PageRank; sem scipy, calcula em Python puro
        pagerank = pagerank_python(g, alpha=0.85)
    comunidades = nx.community.louvain_communities(g, seed=42)
    comunidade_de = {}
    for i, membros in enumerate(sorted(comunidades, key=len, reverse=True)):
        for m in membros:
            comunidade_de[m] = i
    return pagerank, comunidade_de, comunidades


def gravar(sessao, pagerank, comunidade_de):
    linhas = [{"id": k, "pr": pagerank[k], "com": comunidade_de[k]} for k in pagerank]
    for i in range(0, len(linhas), 1000):
        sessao.run("""
            UNWIND $linhas AS l
            MATCH (n) WHERE elementId(n) = l.id
            SET n.pagerank = l.pr, n.comunidade = l.com
        """, linhas=linhas[i:i + 1000]).consume()


def menor_caminho(sessao, id_a, id_b, max_saltos=8):
    """Menor caminho entre duas entidades (elementId) pelas relacoes semanticas."""
    reg = sessao.run(f"""
        MATCH (a), (b) WHERE elementId(a) = $a AND elementId(b) = $b
        MATCH p = shortestPath((a)-[:{REL_SEMANTICAS}*..{int(max_saltos)}]-(b))
        RETURN [n IN nodes(p) | {{id: elementId(n), rotulos: labels(n),
                 nome: coalesce(n.nome_exibicao, n.titulo, n.codigo, n.nome)}}] AS nos,
               [r IN relationships(p) | {{tipo: type(r), de: elementId(startNode(r)),
                 para: elementId(endNode(r)), evidencia: r.evidencia}}] AS rels
    """, a=id_a, b=id_b).single()
    return (reg["nos"], reg["rels"]) if reg else (None, None)


def main():
    from registro import espelhar
    espelhar("fase3.log")
    print(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} - algoritmos de grafo =====")
    with criar_driver() as driver, driver.session(database=BANCO) as s:
        g = carregar_projecao(s)
        print(f"Projecao: {g.number_of_nodes()} nos, {g.number_of_edges()} arestas, "
              f"{nx.number_connected_components(g)} componentes conexos")
        pagerank, comunidade_de, comunidades = calcular(g)
        mod = nx.community.modularity(g, comunidades)
        print(f"Louvain: {len(comunidades)} comunidades, modularidade = {mod:.3f}")
        gravar(s, pagerank, comunidade_de)
        print("pagerank e comunidade gravados nos nos.")
        print("\nTop 10 instituicoes financeiras por PageRank:")
        for r in s.run("""MATCH (i:InstituicaoFinanceira) RETURN i.nome_exibicao AS nome,
                          i.pagerank AS pr, COUNT { (i)<-[:ENVOLVE_INSTITUICAO]-() } AS casos
                          ORDER BY pr DESC LIMIT 10""").data():
            print(f"  {r['nome']:<40} PR={r['pr']:.4f}  casos={r['casos']}")
        print("\nMaiores comunidades (rotulo = instituicao de maior PageRank):")
        for r in s.run("""MATCH (n) WHERE n.comunidade IS NOT NULL
                          WITH n.comunidade AS c, count(*) AS tam,
                               collect(CASE WHEN n:InstituicaoFinanceira THEN n END) AS insts
                          WITH c, tam, [i IN insts WHERE i IS NOT NULL] AS insts
                          RETURN c, tam, [i IN insts | i.nome_exibicao][0..4] AS exemplos
                          ORDER BY tam DESC LIMIT 8""").data():
            print(f"  comunidade {r['c']:>3}: {r['tam']:>4} nos | {', '.join(r['exemplos'])}")
        # exemplo de menor caminho entre as duas instituicoes mais centrais
        top = s.run("""MATCH (i:InstituicaoFinanceira) RETURN elementId(i) AS id,
                       i.nome_exibicao AS nome ORDER BY i.pagerank DESC LIMIT 2""").data()
        if len(top) == 2:
            nos, _ = menor_caminho(s, top[0]["id"], top[1]["id"])
            if nos:
                print(f"\nMenor caminho {top[0]['nome']} -> {top[1]['nome']}: "
                      + " -> ".join(n["nome"] for n in nos))


if __name__ == "__main__":
    main()
