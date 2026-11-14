"""Passo 1.4 - Testa a conexao com o Neo4j e mostra o estado atual do banco.

Uso (na raiz do projeto, com o .env preenchido):
    python scripts/testar_conexao.py

Nao altera nada no banco: so faz leituras.
"""
import sys

from neo4j.exceptions import AuthError, ServiceUnavailable

from conexao import BANCO, URI, criar_driver, variaveis_faltando


def main() -> int:
    faltando = variaveis_faltando()
    if faltando:
        print(f"[ERRO] Faltam variaveis no .env: {', '.join(faltando)}")
        return 1

    print(f"Conectando em {URI} (banco: {BANCO})...")
    try:
        with criar_driver() as driver:
            driver.verify_connectivity()
            print("[OK] Conexao estabelecida.\n")

            with driver.session(database=BANCO) as s:
                comp = s.run(
                    "CALL dbms.components() YIELD name, versions, edition "
                    "RETURN name, versions[0] AS versao, edition"
                ).single()
                print(f"Servidor : {comp['name']} {comp['versao']} ({comp['edition']})")

                try:
                    sim = s.run(
                        "RETURN vector.similarity.cosine([1.0, 0.0], [1.0, 0.0]) AS sim"
                    ).single()["sim"]
                    print(f"Vetores  : suportado (teste de cosseno = {sim})")
                except Exception as erro:  # noqa: BLE001
                    print(f"Vetores  : NAO suportado ({erro.__class__.__name__})")

                total_nos = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
                total_rel = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
                print(f"\nNos no banco     : {total_nos}")
                print(f"Relacoes no banco: {total_rel}")

                if total_nos:
                    print("\nRotulos existentes (dados antigos?):")
                    for linha in s.run(
                        "MATCH (n) UNWIND labels(n) AS rotulo "
                        "RETURN rotulo, count(*) AS qtd ORDER BY qtd DESC"
                    ):
                        print(f"  - {linha['rotulo']}: {linha['qtd']}")

                indices = s.run("SHOW INDEXES YIELD name, type RETURN name, type").data()
                restricoes = s.run("SHOW CONSTRAINTS YIELD name, type RETURN name, type").data()
                print(f"\nIndices    : {len(indices)}")
                for i in indices:
                    print(f"  - {i['name']} ({i['type']})")
                print(f"Constraints: {len(restricoes)}")
                for c in restricoes:
                    print(f"  - {c['name']} ({c['type']})")

    except AuthError:
        print("[ERRO] Usuario ou senha incorretos. Confira o .env.")
        return 1
    except ServiceUnavailable as erro:
        print("[ERRO] Nao foi possivel alcancar o servidor.")
        print("       Confira se a instancia esta 'Running' no console do Aura.")
        print(f"       Detalhe: {erro}")
        return 1

    print("\n[OK] Teste concluido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
