"""Diagnostico da conexao com o Neo4j Aura (passo 1.4).

Testa, em ordem: DNS, porta 7687 (Bolt), porta 443 (HTTPS), conexao do driver
com o banco do .env e com o banco "neo4j", e a Query API HTTPS do Aura.
Nao imprime a senha. Nao altera nada no banco.
"""
import base64
import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
URI = os.getenv("NEO4J_URI", "")
USUARIO = os.getenv("NEO4J_USERNAME", "")
SENHA = os.getenv("NEO4J_PASSWORD", "")
BANCO_ENV = os.getenv("NEO4J_DATABASE") or "neo4j"
HOST = urlparse(URI).hostname or ""


def etapa(titulo):
    print(f"\n--- {titulo} ---")


def testar_porta(porta):
    try:
        with socket.create_connection((HOST, porta), timeout=8):
            print(f"[OK] Porta {porta} aberta")
            return True
    except OSError as erro:
        print(f"[FALHOU] Porta {porta}: {erro}")
        return False


def testar_driver(banco):
    try:
        with GraphDatabase.driver(URI, auth=(USUARIO, SENHA)) as driver:
            with driver.session(database=banco) as s:
                valor = s.run("RETURN 1 AS ok").single()["ok"]
        print(f"[OK] Driver conectou no banco '{banco}' (RETURN 1 = {valor})")
        return True
    except Exception as erro:  # noqa: BLE001
        print(f"[FALHOU] Driver no banco '{banco}': {erro.__class__.__name__}: {erro}")
        return False


def testar_query_api(banco):
    url = f"https://{HOST}/db/{banco}/query/v2"
    token = base64.b64encode(f"{USUARIO}:{SENHA}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=json.dumps({"statement": "RETURN 1 AS ok"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            print(f"[OK] Query API HTTPS no banco '{banco}': HTTP {r.status}")
            return True
    except urllib.error.HTTPError as erro:
        corpo = erro.read().decode(errors="ignore")[:200]
        print(f"[FALHOU] Query API no banco '{banco}': HTTP {erro.code} {corpo}")
    except Exception as erro:  # noqa: BLE001
        print(f"[FALHOU] Query API no banco '{banco}': {erro.__class__.__name__}: {erro}")
    return False


def main():
    print(f"URI: {URI}")
    print(f"Banco no .env: {os.getenv('NEO4J_DATABASE')!r}")

    etapa("1. DNS")
    try:
        print(f"[OK] {HOST} -> {socket.gethostbyname(HOST)}")
    except OSError as erro:
        print(f"[FALHOU] DNS: {erro}")

    etapa("2. Portas")
    testar_porta(7687)
    testar_porta(443)

    etapa("3. Driver Bolt")
    bancos = [BANCO_ENV] + (["neo4j"] if BANCO_ENV != "neo4j" else [])
    for banco in bancos:
        testar_driver(banco)

    etapa("4. Query API HTTPS (porta 443)")
    for banco in bancos:
        testar_query_api(banco)

    print("\nFim do diagnostico.")


if __name__ == "__main__":
    main()
