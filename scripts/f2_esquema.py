"""Fase 2 - Esquema fechado da extracao (o LLM so pode devolver isto).

Cada item extraido traz a frase EXATA do texto que o comprova (evidencia).
O codigo descarta qualquer item cuja evidencia nao exista no comunicado.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

ESQUEMAS = Literal[
    "Fraude hipotecaria",
    "Esquema Ponzi",
    "Fraude de investimentos ou valores mobiliarios",
    "Roubo de identidade",
    "Fraude de cartao",
    "Fraude de cheques",
    "Fraude de emprestimos",
    "Desvio por funcionario (insider)",
    "Lavagem de dinheiro",
    "Evasao fiscal ou contas offshore",
    "Fraude eletronica (phishing ou invasao)",
    "Outro",
]
CRIMES = Literal[
    "bank fraud", "wire fraud", "mail fraud", "money laundering", "conspiracy",
    "securities fraud", "Bank Secrecy Act violation", "tax evasion",
    "identity theft", "false statements", "bribery", "embezzlement", "other",
]
DESFECHOS = Literal["denunciado", "confessou", "condenado", "sentenciado", "acordo", "outro"]


class Evidenciado(BaseModel):
    evidencia: str = Field(description="Frase copiada EXATAMENTE do texto, sem alterar nada.")


class Reu(Evidenciado):
    nome: str = Field(description="Nome completo da pessoa fisica acusada.")
    desfecho: DESFECHOS
    pena_meses: Optional[int] = Field(None, description="Pena de prisao em meses, se citada.")
    restituicao_usd: Optional[float] = Field(None, description="Restituicao ordenada em dolares.")
    confisco_usd: Optional[float] = Field(None, description="Confisco (forfeiture) em dolares.")
    empresa: Optional[str] = Field(None, description="Empresa a que o reu esta ligado, se citada.")


class Instituicao(Evidenciado):
    nome: str
    tipo: Literal["banco", "cooperativa de credito", "corretora", "seguradora", "outra"]
    papel: Literal["vitima", "acusada", "intermediaria"]


class Empresa(Evidenciado):
    nome: str = Field(description="Empresa que NAO e instituicao financeira.")
    papel: Literal["vitima", "acusada", "usada no esquema"]


class Orgao(Evidenciado):
    nome: str = Field(description="Orgao que investigou ou processou (ex.: FBI, IRS, SEC).")
    sigla: Optional[str] = None


class Pessoa(Evidenciado):
    nome: str


class Extracao(BaseModel):
    eh_fraude_financeira: bool = Field(
        description="True se o comunicado trata de fraude contra/por banco, investimento, "
                    "credito, hipoteca, cartao ou lavagem de dinheiro.")
    esquemas: list[ESQUEMAS]
    crimes: list[CRIMES]
    distrito: Optional[str] = Field(None, description="Ex.: 'Eastern District of New York'.")
    valor_esquema_usd: Optional[float] = Field(
        None, description="Valor total da fraude em dolares, se citado.")
    reus: list[Reu]
    instituicoes: list[Instituicao]
    empresas: list[Empresa]
    orgaos: list[Orgao]
    juizes: list[Pessoa]
    promotores: list[Pessoa]
    agentes: list[Pessoa] = Field(description="Agentes/chefes de orgaos citados pelo nome.")


PROMPT = """Voce extrai informacoes estruturadas de comunicados oficiais do Departamento
de Justica dos EUA (DOJ) para montar um grafo de conhecimento sobre fraudes financeiras.

Regras:
1. Use SOMENTE o que esta no texto. Nao invente nem complete com conhecimento externo.
2. Em todo item, 'evidencia' deve ser um trecho COPIADO EXATAMENTE do texto
   (mesmas palavras, mesma pontuacao), curto (uma frase ou parte dela).
3. 'reus' sao apenas pessoas fisicas acusadas. Empresas ou bancos acusados vao em
   'instituicoes' (papel 'acusada') ou 'empresas'.
4. 'instituicoes' sao so instituicoes financeiras (bancos, cooperativas, corretoras).
5. Valores em dolares como numero (ex.: '$2.5 million' -> 2500000). Penas em meses
   (ex.: '5 years' -> 60). Se nao estiver no texto, deixe vazio (null).
6. Se o texto nao for sobre fraude financeira, marque eh_fraude_financeira = false
   e devolva as listas vazias.

COMUNICADO:
"""
