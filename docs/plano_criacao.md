# Plano de criacao do conversor de documentos para audio

## Objetivo

Criar um aplicativo local para Windows que receba arquivos DOCX e PDF, extraia e organize o texto e gere arquivos de audio em portugues brasileiro. O programa deve funcionar com voz offline e permitir servicos de voz online como alternativa opcional.

## Escopo da primeira versao

- Selecionar um ou varios arquivos DOCX ou PDF.
- Extrair texto preservando titulos e a ordem de leitura.
- Identificar PDFs sem camada de texto e encaminha-los para OCR.
- Mostrar uma previa editavel antes da conversao.
- Dividir o documento por titulos, capitulos ou limite de tamanho.
- Gerar um arquivo MP3 por capitulo, com opcao de M4B unico com marcadores de capitulo.
- Permitir escolher voz e velocidade, com amostra audivel antes de converter.
- Normalizar siglas, numeros, datas, valores e abreviacoes antes da narracao.
- Mostrar duracao estimada do audio e, nos modos online, custo estimado antes de converter.
- Registrar andamento, erros e arquivos produzidos.

## Arquitetura recomendada

### Separacao entre nucleo e interface

Todo o fluxo de conversao fica em `docaudio.core`, sem dependencia de PySide6:

```text
extrair -> normalizar -> dividir -> sintetizar -> montar audio
```

A interface apenas chama o nucleo e exibe o estado. Isso permite testar o fluxo sem interface, depurar por linha de comando (`python -m docaudio converter arquivo.pdf`) e executar a sintese fora da thread da interface.

### Modelo de dados

```text
Documento
  origem, tipo (docx | pdf | pdf_ocr), metadados
  Capitulo[]
    numero, titulo
    Bloco[]
      tipo (titulo | paragrafo | tabela | nota | url | referencia | lista)
      texto_original
      texto_normalizado (editavel na previa)
      incluir (bool, conforme configuracao do tipo)
      Trecho[]
        texto (frase ou grupo de frases curto o bastante para o motor de voz)
        hash (texto + voz + velocidade)
        estado (pendente | gerado | erro)
        arquivo_wav
```

- A previa editavel altera `texto_normalizado`.
- As regras para tabelas, URLs, notas e referencias alteram `incluir` por tipo de bloco.
- A retomada usa o estado e o hash de cada trecho: trechos ja gerados e inalterados nao sao sintetizados de novo.

### Interface

Aplicativo desktop em Python com PySide6. A interface tera selecao de documentos, lista de capitulos, previa do texto, configuracoes de voz com botao de amostra, progresso com cancelamento e acesso a pasta de saida. A sintese roda em `QThread` ou pool de processos para nunca travar a janela.

### Extracao de documentos

- DOCX: python-docx, usando estilos de titulo quando existirem e, na falta deles, heuristicas de formatacao (negrito, tamanho, numeracao).
- PDF com texto: PyMuPDF, com:
  - deteccao de cabecalhos e rodapes repetidos entre paginas;
  - juncao de palavras hifenizadas no fim da linha;
  - ordem de leitura em documentos de duas colunas;
  - titulos inferidos por tamanho e peso da fonte quando nao houver sumario (outline).
- PDF digitalizado: Tesseract via PyMuPDF (`get_textpage_ocr`), acionado somente quando a pagina nao tiver camada de texto. Evita a dependencia de OCRmyPDF e Ghostscript.

### Normalizacao de texto em pt-BR

- Siglas e termos tecnicos a partir de dicionario editavel.
- Numeros cardinais e ordinais, datas, horas, porcentagens e valores em R$.
- Abreviacoes comuns: art., inc., n., Dr., Sr., pag., etc.
- Dicionarios em `resources/pronunciation/*.csv` (colunas `termo,pronuncia,diferenciar_maiusculas`), com um dicionario do usuario que tem prioridade sobre o padrao.

### Sintese de voz

- Modo offline: Piper (pacote `piper-tts`) com voz em portugues brasileiro.
- Modo qualidade: Azure Speech, configurado pelo usuario.
- ElevenLabs fica fora desta versao; o adaptador do Azure valida a interface de provedores e outros podem ser adicionados depois.

Todos os provedores implementam a mesma interface (`sintetizar(trecho, voz, velocidade) -> wav`, `listar_vozes()`, `custo_estimado(caracteres)`). O texto e dividido em trechos curtos (frase ou paragrafo) antes da sintese.

O aplicativo devera funcionar sem credenciais externas no modo Piper. Chaves de servicos online ficam no Gerenciador de Credenciais do Windows via `keyring`, nunca no codigo nem em arquivos de configuracao.

### Audio

FFmpeg sera usado para unir trechos, normalizar volume e produzir MP3 ou M4B.

- Pausas configuraveis entre frases, paragrafos e capitulos.
- Titulo do capitulo narrado no inicio de cada faixa (opcional).
- Volume normalizado para -16 LUFS.
- Metadados: titulo do capitulo, numero da faixa, album com o nome do documento de origem.
- Nomes de saida no formato `01 - Titulo do capitulo.mp3`.

### Armazenamento local

- Preferencias em `data/settings.json`.
- Cada conversao tem uma pasta propria com `manifest.json` (estado do documento, capitulos e trechos), trechos WAV temporarios e audios finais em subpastas separadas.
- O historico e a lista de manifestos existentes.
- O usuario podera limpar os temporarios pela interface.

SQLite nao e necessario nesta versao; pode ser adotado se o historico crescer a ponto de exigir consultas.

## Estrutura planejada

```text
documentos-para-audio
  README.md
  pyproject.toml
  src
    docaudio
      __main__.py         linha de comando
      app.py              entrada da interface
      core                fluxo de conversao e modelo de dados
      ui
      extractors
      speech              provedores (piper, azure)
      audio
      text_processing     normalizacao e divisao em trechos
      storage             settings e manifestos
  tests
    corpus                documentos de referencia
  resources
    pronunciation
    voices                modelos Piper (.onnx + .json)
  data
  output
  docs
    plano_criacao.md
```

## Corpus de teste

Antes da Etapa 2, reunir de 5 a 10 documentos reais que cubram os casos dificeis:

- DOCX com estilos de titulo e DOCX sem estilos;
- PDF de apostila com cabecalho e rodape repetidos;
- PDF em duas colunas;
- PDF digitalizado;
- documento com muitas siglas, numeros e valores;
- documento com tabelas, URLs e notas de rodape.

Cada documento tera um arquivo de referencia com os titulos esperados, usado nos testes automatizados.

## Etapas de implementacao

### Etapa 1 Base do aplicativo e prova de empacotamento

Criar o projeto Python (`pyproject.toml`, git), a janela principal, configuracoes, logs e selecao de arquivos. Verificar na inicializacao se FFmpeg e o modelo de voz Piper estao disponiveis e avisar o usuario.

Gerar desde ja um executavel com PyInstaller que abra a janela e narre uma frase fixa com o Piper, para validar cedo o empacotamento de PySide6 e onnxruntime.

Decidir a distribuicao das dependencias: FFmpeg e o modelo de voz pt-BR padrao vao junto com o instalador; vozes adicionais e Tesseract podem ser baixados sob demanda.

Criterio de aceite: o executavel abre em uma maquina Windows limpa, seleciona arquivos e reproduz a frase de teste.

### Etapa 2 Leitura de DOCX e PDF

Implementar extracao para o modelo de dados, limpeza de cabecalhos e rodapes, reconhecimento de titulos, regras por tipo de bloco e previa editavel.

Criterio de aceite: em todos os documentos do corpus com texto, os titulos detectados coincidem com a referencia e nao sobram cabecalhos ou rodapes repetidos.

### Etapa 3 Voz offline e audio final

Integrar o Piper e o FFmpeg juntos: normalizacao, divisao em trechos, sintese, cache por hash, concatenacao com pausas, normalizacao de volume, metadados e saida em MP3 e M4B. Incluir amostra de voz, estimativa de duracao, cancelamento e retomada.

Criterio de aceite: a primeira entrega funcional (abaixo) e cumprida, sem cortes ou estalos audiveis entre trechos, e uma conversao interrompida retoma sem refazer trechos ja gerados.

### Etapa 4 OCR

Detectar paginas sem camada de texto, executar OCR com Tesseract (idioma `por`) e destacar na previa o texto reconhecido para revisao antes da narracao.

Criterio de aceite: o PDF digitalizado do corpus gera texto revisavel e audio sem intervencao no terminal.

### Etapa 5 Voz online opcional

Adicionar o provedor Azure Speech pelo adaptador comum, com credenciais via `keyring`, custo estimado antes da conversao e funcionamento normal do aplicativo sem internet.

Criterio de aceite: com a internet desligada, o modo Piper continua funcionando e o modo Azure exibe erro claro sem travar.

### Etapa 6 Instalador

Gerar o instalador final para Windows, com validacao das dependencias locais, atalhos e desinstalacao limpa.

Criterio de aceite: instalar, converter um documento do corpus e desinstalar em uma maquina Windows limpa.

## Regras de qualidade

- Nenhum documento sera enviado para a internet no modo offline.
- O aplicativo sempre exibira qual mecanismo de voz esta ativo.
- Conversoes interrompidas poderao ser retomadas por trecho.
- O texto sera revisavel, e o custo estimado exibido, antes de consumir uma API paga.
- Tabelas, URLs, notas de rodape e referencias poderao ser ignoradas ou narradas conforme configuracao.
- A interface nunca trava durante a conversao e toda conversao pode ser cancelada.
- O projeto tera testes para extracao (com o corpus), divisao de texto, normalizacao e recuperacao de falhas.

## Licencas

- PyMuPDF e licenciado sob AGPL; se o aplicativo for distribuido, o codigo-fonte tambem devera ser disponibilizado sob AGPL ou sera necessaria licenca comercial. Alternativa permissiva a avaliar: pypdfium2.
- Verificar a licenca de cada modelo de voz Piper distribuido.
- PySide6 (LGPL) e FFmpeg (LGPL/GPL conforme build) devem ter suas licencas incluidas no instalador.

## Primeira entrega funcional

A primeira entrega sera considerada pronta quando um usuario conseguir selecionar uma apostila DOCX ou um PDF com texto, revisar os capitulos, ouvir uma amostra da voz e gerar arquivos MP3 localmente com Piper sem utilizar o terminal.
