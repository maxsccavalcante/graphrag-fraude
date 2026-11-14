"""Conexao unica com o Neo4j, usada por todos os scripts do projeto.

Por que o truststore: o Python nao reconhecia a autoridade certificadora
(SSL.com) usada pelo Neo4j Aura em alguns computadores Windows. O truststore
faz o Python validar o certificado com o repositorio do proprio sistema
operacional (Windows, macOS ou Linux), sem desligar a verificacao.
"""
import os

import truststore

truststore.inject_into_ssl()

from dotenv import load_dotenv  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

load_dotenv()

URI = os.getenv("NEO4J_URI")
USUARIO = os.getenv("NEO4J_USERNAME")
SENHA = os.getenv("NEO4J_PASSWORD")
BANCO = os.getenv("NEO4J_DATABASE") or "neo4j"


def variaveis_faltando():
    return [nome for nome, valor in {
        "NEO4J_URI": URI,
        "NEO4J_USERNAME": USUARIO,
        "NEO4J_PASSWORD": SENHA,
    }.items() if not valor]


def criar_driver():
    """Cria o driver do Neo4j. Use com 'with criar_driver() as driver:'."""
    return GraphDatabase.driver(URI, auth=(USUARIO, SENHA))
