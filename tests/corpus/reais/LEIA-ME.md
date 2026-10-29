# Corpus de documentos reais

Coloque aqui as apostilas e PDFs reais usados como referencia (veja "Corpus de teste" em `docs/plano_criacao.md`). Os documentos nao sao versionados; os arquivos de referencia sim.

Para cada documento, crie `<nome>.esperado.json` ao lado dele:

```json
{
  "arquivo": "apostila_redes.pdf",
  "titulo": "Apostila de Redes",
  "capitulos": ["Introdução", "Camada física", "Camada de enlace"],
  "contem": ["trecho que deve ser narrado"],
  "nao_contem": ["Cabeçalho repetido", "Página"],
  "ordem": ["trecho que vem antes", "trecho que vem depois"]
}
```

Apenas `arquivo` e `capitulos` sao obrigatorios. `tests/test_corpus.py` testa cada referencia e pula documentos ausentes.
