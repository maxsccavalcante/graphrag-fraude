"""Acesso unico ao LLM (Gemini) usado em todo o projeto.

- SDK oficial google-genai (o endpoint "compativel com OpenAI" recusa
  alguns formatos novos de chave).
- Saida sempre em JSON validado por um modelo Pydantic (esquema fechado).
- Espera e tenta de novo quando o limite de uso (erro 429) e atingido.
"""
import os
import re
import time

import truststore

truststore.inject_into_ssl()

from dotenv import load_dotenv  # noqa: E402
from google import genai  # noqa: E402
from google.genai import errors, types  # noqa: E402

load_dotenv()

CHAVE = os.getenv("GEMINI_API_KEY")
MODELO = os.getenv("GEMINI_MODEL")  # opcional; se vazio, o teste sugere um


def criar_cliente():
    if not CHAVE:
        raise RuntimeError("GEMINI_API_KEY nao encontrada no .env")
    # timeout de 3 min por pedido (em ms): sem isso, uma conexao travada fica parada para sempre
    return genai.Client(api_key=CHAVE, http_options=types.HttpOptions(timeout=180_000))


def listar_modelos_texto(cliente):
    """Modelos da conta que geram texto (generateContent)."""
    nomes = []
    for m in cliente.models.list():
        acoes = getattr(m, "supported_actions", None) or []
        if "generateContent" in acoes:
            nomes.append(m.name.removeprefix("models/"))
    return sorted(nomes)


def sugerir_modelo(nomes):
    """Flash-Lite estavel de versao mais alta.

    No plano gratuito desta conta (AI Studio, nov/2026) os modelos Flash
    tem 20 pedidos/dia, enquanto os Flash-Lite tem 500/dia: so os Lite
    comportam a extracao de ~200 comunicados.
    """
    excluir = ("preview", "exp", "tts", "image", "audio", "live", "thinking")
    candidatos = [n for n in nomes if "flash-lite" in n and not any(x in n for x in excluir)]
    if not candidatos:
        candidatos = [n for n in nomes if "flash-lite" in n]

    def versao(nome):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", nome)
        return float(m.group(1)) if m else 0.0

    return max(candidatos, key=versao) if candidatos else None


def gerar_json(cliente, modelo, prompt, esquema, tentativas=5):
    """Chama o modelo e devolve um objeto do tipo `esquema` (Pydantic)."""
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=esquema,
    )
    espera = 10
    for tentativa in range(1, tentativas + 1):
        try:
            resposta = cliente.models.generate_content(
                model=modelo, contents=prompt, config=config)
            if resposta.parsed is None:
                raise ValueError("resposta sem JSON valido")
            return resposta.parsed
        except errors.APIError as erro:
            # 429 = limite de uso; 500/503 = servidor sobrecarregado (temporario)
            if erro.code in (429, 500, 503) and tentativa < tentativas:
                motivo = "limite de uso atingido" if erro.code == 429 else "servidor ocupado"
                print(f"    {motivo} ({erro.code}); aguardando {espera}s...")
                time.sleep(espera)
                espera *= 2
                continue
            raise
    raise RuntimeError("numero maximo de tentativas atingido")
