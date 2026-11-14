"""Diagnostico do certificado TLS na conexao com o Neo4j Aura.

1. Mostra QUEM emitiu o certificado que chega no seu computador
   (se for um antivirus ou proxy, ele aparece aqui).
2. Testa a conexao usando o repositorio de certificados do Windows
   (pacote truststore), que normalmente resolve esse tipo de bloqueio.
Nao imprime a senha. Nao altera nada no banco.
"""
import os
import socket
import ssl
import tempfile
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()
URI = os.getenv("NEO4J_URI", "")
USUARIO = os.getenv("NEO4J_USERNAME", "")
SENHA = os.getenv("NEO4J_PASSWORD", "")
BANCO = os.getenv("NEO4J_DATABASE") or "neo4j"
HOST = urlparse(URI).hostname or ""


def emissor_do_certificado(porta):
    """Le o certificado recebido (sem validar) so para saber quem o emitiu."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((HOST, porta), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=HOST) as tls:
            der = tls.getpeercert(binary_form=True)
    pem = ssl.DER_cert_to_PEM_cert(der)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(pem)
            caminho = f.name
        info = ssl._ssl._test_decode_cert(caminho)  # noqa: SLF001
        os.remove(caminho)
        emissor = ", ".join(f"{k}={v}" for parte in info["issuer"] for k, v in parte)
        sujeito = ", ".join(f"{k}={v}" for parte in info["subject"] for k, v in parte)
        return f"emitido por: {emissor}\n         para: {sujeito}"
    except Exception as erro:  # noqa: BLE001
        return f"(nao consegui decodificar: {erro})"


def testar_com_truststore():
    import truststore

    truststore.inject_into_ssl()
    from neo4j import GraphDatabase

    with GraphDatabase.driver(URI, auth=(USUARIO, SENHA)) as driver:
        with driver.session(database=BANCO) as s:
            return s.run("RETURN 1 AS ok").single()["ok"]


def main():
    print(f"Servidor: {HOST}\n")
    print("--- 1. Quem emitiu o certificado recebido ---")
    for porta in (7687, 443):
        try:
            print(f"Porta {porta}: {emissor_do_certificado(porta)}")
        except Exception as erro:  # noqa: BLE001
            print(f"Porta {porta}: erro ao ler certificado: {erro}")
    print("\n(O normal seria um emissor publico, como Let's Encrypt ou Google Trust Services.")
    print(" Nome de antivirus ou proxy aqui = ele esta interceptando a conexao.)")

    print("\n--- 2. Conexao usando os certificados do Windows (truststore) ---")
    try:
        valor = testar_com_truststore()
        print(f"[OK] Conectou no banco '{BANCO}' (RETURN 1 = {valor})")
    except Exception as erro:  # noqa: BLE001
        print(f"[FALHOU] {erro.__class__.__name__}: {erro}")
    print("\nFim do diagnostico.")


if __name__ == "__main__":
    main()
