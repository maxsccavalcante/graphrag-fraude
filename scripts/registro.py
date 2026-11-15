"""Mostra a saida na tela E grava num arquivo .log (para conferir depois)."""
import sys
from pathlib import Path


class _Espelho:
    def __init__(self, original, arquivo):
        self.original, self.arquivo = original, arquivo

    def write(self, texto):
        self.original.write(texto)
        self.original.flush()
        self.arquivo.write(texto)
        self.arquivo.flush()

    def flush(self):
        self.original.flush()
        self.arquivo.flush()


def espelhar(nome_log):
    raiz = Path(__file__).resolve().parent.parent
    arquivo = open(raiz / nome_log, "a", encoding="utf-8")
    sys.stdout = _Espelho(sys.stdout, arquivo)
    sys.stderr = _Espelho(sys.stderr, arquivo)
