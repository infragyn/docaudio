"""Entrada do executavel: sem argumentos abre a interface, com argumentos usa a linha de comando."""

import multiprocessing
import sys

from docaudio.__main__ import main

if __name__ == "__main__":
    # O OCR usa processos paralelos; no executavel eles reiniciam este mesmo .exe.
    multiprocessing.freeze_support()
    sys.exit(main())
