"""Passo 1.5 - Aplica o schema (constraints + indices) e valida.

Uso (na raiz do projeto):
    python scripts/aplicar_schema.py

O que faz:
  1. Executa cada comando de schema/schema.cypher (idempotente).
  2. Espera os indices ficarem ONLINE.
  3. Confere se tudo o que o schema define existe no banco.
  4. Testa as regras na pratica tentando gravar dados invalidos (o banco deve
     recusar). Qualquer dado de teste aceito e apagado: nada fica gravado.
"""
import re
import sys
from pathlib import Path

from neo4j.exceptions import ClientError, Neo4jError

from conexao import BANCO, URI, criar_driver, variaveis_faltando

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_SCHEMA = RAIZ / "schema" / "schema.cypher"

CONSTRAINTS_ESPERADAS = {
    "caso_id_chave", "trecho_id_chave", "pessoa_codigo_chave",
    "instituicao_nome_chave", "empresa_nome_chave", "orgao_nome_chave",
    "distrito_nome_chave", "esquema_nome_chave", "crime_nome_chave",
    "trecho_texto_obrigatorio", "caso_titulo_obrigatorio",
    "caso_data_tipo", "caso_ano_tipo", "reu_em_evidencia_obrigatoria",
}
INDICES_ESPERADOS = {
    "caso_data_idx": "RANGE", "caso_ano_idx": "RANGE",
    "trecho_caso_idx": "RANGE", "reu_em_desfecho_idx": "RANGE",
    "envolve_instituicao_papel_idx": "RANGE",
    "trecho_texto_ft": "FULLTEXT", "caso_titulo_ft": "FULLTEXT",
    "organizacao_nome_ft": "FULLTEXT",
    "trecho_embedding_vec": "VECTOR", "caso_embedding_vec": "VECTOR",
}


def ler_comandos(caminho):
    linhas = [l for l in caminho.read_text(encoding="utf-8").splitlines()
              if not l.strip().startswith("//")]
    return [c.strip() for c in "\n".join(linhas).split(";") if c.strip()]


def nome_do_comando(cmd):
    m = re.search(r"(?:CONSTRAINT|INDEX)\s+(\w+)", cmd)
    return m.group(1) if m else cmd[:40]


def aplicar(driver):
    print(f"[1] Aplicando {ARQUIVO_SCHEMA.relative_to(RAIZ)}")
    erros = 0
    with driver.session(database=BANCO) as s:
        for cmd in ler_comandos(ARQUIVO_SCHEMA):
            try:
                s.run(cmd).consume()
                print(f"    [OK]   {nome_do_comando(cmd)}")
            except Neo4jError as erro:
                erros += 1
                print(f"    [ERRO] {nome_do_comando(cmd)}: {erro.message}")
        print("\n[2] Aguardando indices ficarem ONLINE...")
        s.run("CALL db.awaitIndexes(300)").consume()
    return erros


def conferir(driver):
    print("\n[3] Conferindo o que existe no banco")
    falhas = 0
    with driver.session(database=BANCO) as s:
        constraints = {r["name"]: r["type"] for r in s.run(
            "SHOW CONSTRAINTS YIELD name, type RETURN name, type")}
        indices = {r["name"]: (r["type"], r["state"]) for r in s.run(
            "SHOW INDEXES YIELD name, type, state RETURN name, type, state")}

    for nome in sorted(CONSTRAINTS_ESPERADAS):
        if nome in constraints:
            print(f"    [OK]     constraint {nome} ({constraints[nome]})")
        else:
            falhas += 1
            print(f"    [FALTA]  constraint {nome}")
    for nome, tipo in sorted(INDICES_ESPERADOS.items()):
        achado = indices.get(nome)
        if achado and achado[0] == tipo and achado[1] == "ONLINE":
            print(f"    [OK]     indice {nome} ({tipo}, ONLINE)")
        else:
            falhas += 1
            print(f"    [FALHOU] indice {nome}: esperado {tipo}/ONLINE, achado {achado}")
    return falhas


def limpar_testes(driver):
    """Apaga qualquer no de teste (marcado com _teste: true)."""
    with driver.session(database=BANCO) as s:
        s.run("MATCH (n) WHERE n._teste = true DETACH DELETE n").consume()


def deve_falhar(driver, descricao, comandos):
    """Tenta gravar e CONFIRMAR (commit) dados invalidos; o banco deve recusar.

    Regras de unicidade sao checadas na hora; regras de existencia e de tipo
    so no commit. Por isso o teste precisa confirmar a transacao.
    Se o banco aceitar, os nos de teste sao apagados em seguida.
    """
    with driver.session(database=BANCO) as s:
        try:
            with s.begin_transaction() as tx:
                for cmd, params in comandos:
                    tx.run(cmd, params).consume()
                tx.commit()
        except ClientError as erro:
            print(f"    [OK]     {descricao}: recusado ({erro.code.split('.')[-1]})")
            return 0
    limpar_testes(driver)
    print(f"    [FALHOU] {descricao}: o banco ACEITOU (deveria recusar)")
    return 1


def deve_funcionar(driver, descricao, cmd, params=None):
    # Os procedimentos db.index.*.queryNodes geram aviso de "deprecated"
    # no Aura (substituidos por SEARCH); a migracao fica para a Fase 3.
    with driver.session(database=BANCO,
                        notifications_disabled_classifications=["DEPRECATION"]) as s:
        try:
            linhas = s.run(cmd, params or {}).data()
            print(f"    [OK]     {descricao} ({len(linhas)} resultado(s))")
            return 0
        except Neo4jError as erro:
            print(f"    [FALHOU] {descricao}: {erro.message}")
            return 1


def testar_regras(driver):
    print("\n[4] Testando as regras (dados de teste; nada fica gravado)")
    caso = ("CREATE (:Caso {caso_id: $id, titulo: 't', data: date('2015-01-01'), "
            "ano: 2015, _teste: true})")
    falhas = 0
    falhas += deve_falhar(driver, "Caso com caso_id repetido", [
        (caso, {"id": "TESTE-1"}), (caso, {"id": "TESTE-1"})])
    falhas += deve_falhar(driver, "Caso sem caso_id", [
        ("CREATE (:Caso {titulo: 't', _teste: true})", {})])
    falhas += deve_falhar(driver, "Caso com data em texto (tipo errado)", [
        ("CREATE (:Caso {caso_id: 'TESTE-2', titulo: 't', data: '2015-01-01', _teste: true})", {})])
    falhas += deve_falhar(driver, "Codigo de pessoa repetido entre papeis (Reu x Juiz)", [
        ("CREATE (:Pessoa:Reu {codigo: 'Teste_001', _teste: true})", {}),
        ("CREATE (:Pessoa:Juiz {codigo: 'Teste_001', _teste: true})", {})])
    falhas += deve_falhar(driver, "Trecho sem texto", [
        ("CREATE (:Trecho {trecho_id: 'TESTE-T1', _teste: true})", {})])
    falhas += deve_falhar(driver, "Relacao REU_EM sem evidencia", [
        ("CREATE (:Pessoa:Reu {codigo: 'Teste_002', _teste: true})-[:REU_EM]->"
         "(:Caso {caso_id: 'TESTE-3', titulo: 't', _teste: true})", {})])

    vetor = [0.0] * 767 + [1.0]
    falhas += deve_funcionar(
        driver, "Busca vetorial em trecho_embedding_vec",
        "CALL db.index.vector.queryNodes('trecho_embedding_vec', 5, $v) "
        "YIELD node, score RETURN node, score", {"v": vetor})
    falhas += deve_funcionar(
        driver, "Busca fulltext em trecho_texto_ft",
        "CALL db.index.fulltext.queryNodes('trecho_texto_ft', 'bank fraud') "
        "YIELD node, score RETURN node, score")
    limpar_testes(driver)
    return falhas


def main():
    faltando = variaveis_faltando()
    if faltando:
        print(f"[ERRO] Faltam variaveis no .env: {', '.join(faltando)}")
        return 1
    print(f"Banco: {URI} ({BANCO})\n")
    with criar_driver() as driver:
        driver.verify_connectivity()
        erros = aplicar(driver)
        falhas = conferir(driver)
        falhas += testar_regras(driver)
        with driver.session(database=BANCO) as s:
            nos = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        print(f"\nNos no banco apos os testes: {nos} (esperado: 0)")
    total = erros + falhas + (1 if nos else 0)
    print("\n" + ("[OK] Schema aplicado e validado." if total == 0
                  else f"[ATENCAO] {total} problema(s) acima."))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
