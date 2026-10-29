# DocAudio

Conversor local de documentos DOCX e PDF para audio em portugues brasileiro, com voz offline (Piper). O plano completo esta em [docs/plano_criacao.md](docs/plano_criacao.md).

Estado atual: **Etapas 1 a 4** concluidas (base, empacotamento, leitura de DOCX e PDF com previa editavel, normalizacao pt-BR, geracao de audio em MP3, M4B ou WAV com retomada, e OCR de PDFs digitalizados). Faltam a voz online opcional (Etapa 5) e o instalador (Etapa 6).

## Preparar o ambiente

Requer Python 3.11+ no Windows.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\docaudio baixar-voz pt_BR-faber-medium --destino resources/voices
```

Se o pip falhar com `CERTIFICATE_VERIFY_FAILED` (proxy ou antivirus que inspeciona HTTPS), use os certificados do Windows:

```powershell
$env:PIP_USE_FEATURE = "truststore"
```

MP3 e M4B precisam do FFmpeg no PATH ou em `resources/bin` (sem ele, use o formato WAV). Para distribuir no instalador, prefira um build LGPL, por exemplo `ffmpeg-n*-win64-lgpl-shared` de https://github.com/BtbN/FFmpeg-Builds/releases: copie `bin/ffmpeg.exe` e as DLLs para `resources/bin`.

## Vozes (todas offline)

| Fonte | Vozes pt-BR | Instalacao |
|---|---|---|
| Piper | Faber, Cadu, Jeff (masculinas), Edresson (masculina, baixa qualidade) | `docaudio baixar-voz pt_BR-cadu-medium` |
| Piper com efeito | Faber, Cadu e Jeff em versao feminina e infantil (tom alterado) | automatico para vozes Piper de qualidade media |
| Kokoro | Dora (feminina), Alex e Santa (masculinas) | `docaudio baixar-voz kokoro` (~120 MB) |
| Windows | Maria (feminina) e outras vozes pt instaladas no sistema | ja vem com o Windows em portugues |

`docaudio vozes` lista todas as vozes disponiveis.

O OCR usa o Tesseract embutido no PyMuPDF, com as paginas reconhecidas em paralelo, e precisa apenas dos dados do idioma. O modelo rapido (padrao) fica em `resources/tessdata`; o preciso, cerca de 10x mais lento, em `resources/tessdata_best` (opcao "OCR preciso" na interface):

```powershell
curl -L -o resources\tessdata\por.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/por.traineddata
curl -L -o resources\tessdata_best\por.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata
```

A pronuncia de siglas e termos fica em `resources/pronunciation/padrao.csv`; o dicionario do usuario (Ferramentas > Editar dicionario de pronuncia) tem prioridade.

## Uso

```powershell
.venv\Scripts\docaudio-gui                 # interface
.venv\Scripts\docaudio verificar           # dependencias locais
.venv\Scripts\docaudio vozes               # vozes instaladas
.venv\Scripts\docaudio ler apostila.pdf    # capitulos e blocos extraidos
.venv\Scripts\docaudio ler apostila.pdf --referencia > tests\corpus\reais\apostila.esperado.json
.venv\Scripts\docaudio converter apostila.pdf --formato mp3   # retoma se interrompido
.venv\Scripts\docaudio converter apostila.pdf --formato m4b --capitulos 1,2
.venv\Scripts\docaudio falar "Texto" -o saida.wav --velocidade 1.2
.venv\Scripts\docaudio baixar-voz pt_BR-cadu-medium
```

## Testes

```powershell
.venv\Scripts\python -m pytest
```

O corpus sintetico (`tests/corpus/sinteticos.py`) e gerado a cada execucao. Documentos reais entram em `tests/corpus/reais` com um arquivo `.esperado.json` (veja o LEIA-ME da pasta).

## Executavel

```powershell
.venv\Scripts\pyinstaller packaging\docaudio.spec --noconfirm
```

Gera `dist\DocAudio\DocAudio.exe` com a voz de `resources/voices` e os binarios de `resources/bin`. Sem argumentos abre a interface; com argumentos aceita os mesmos comandos de `docaudio`.

## Onde ficam os dados

| | Desenvolvimento | Executavel |
|---|---|---|
| Configuracoes, logs, temporarios | `data/` | `%LOCALAPPDATA%\DocAudio` |
| Saida padrao | `output/` | `Documentos\DocAudio` |
| Vozes adicionais | `data/voices` | `%LOCALAPPDATA%\DocAudio\voices` |

A variavel `DOCAUDIO_DATA_DIR` substitui a pasta de dados.
