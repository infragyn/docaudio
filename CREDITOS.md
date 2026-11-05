# Créditos e licenças de terceiros

O DocAudio é distribuído sob a [AGPL-3.0 ou posterior](LICENSE). Ele depende das bibliotecas, vozes e modelos abaixo, cada um com a sua licença. As versões são as usadas no desenvolvimento; o executável empacota as bibliotecas marcadas como "no executável".

## Bibliotecas

| Componente | Uso no DocAudio | Licença | No executável |
|---|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (MuPDF e Tesseract embutidos) | Leitura de PDF e OCR | AGPL-3.0 (ou licença comercial da Artifex) | sim |
| [Piper TTS](https://github.com/OHF-Voice/piper1-gpl) | Síntese de voz | GPL-3.0 ou posterior | sim |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) (via Piper e espeakng-loader) | Fonemas para Piper e Kokoro | GPL-3.0 ou posterior | sim |
| [espeakng-loader](https://github.com/thewh1teagle/espeakng-loader) | Carrega o espeak-ng para o Kokoro | MIT | sim |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | Síntese de voz Kokoro | MIT | sim |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Executa os modelos de voz | MIT | sim |
| [PySide6 / Qt for Python](https://www.qt.io/qt-for-python) | Interface gráfica | LGPL-3.0 | sim |
| [PyAV](https://github.com/PyAV-Org/PyAV) (com bibliotecas FFmpeg) | Player: decodificação e velocidade | BSD-3-Clause (FFmpeg: LGPL-2.1 ou posterior) | sim |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | Tradução offline | MIT | sim |
| [SentencePiece](https://github.com/google/sentencepiece) | Tokenização da tradução | Apache-2.0 | sim |
| [python-docx](https://github.com/python-openxml/python-docx) | Leitura de DOCX e exportação da tradução | MIT | sim |
| [lxml](https://lxml.de/) | XML de DOCX, PPTX e EPUB | BSD-3-Clause | sim |
| [NumPy](https://numpy.org/) | Processamento de áudio | BSD-3-Clause | sim |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) (PortAudio) | Saída de áudio e gravação | MIT | sim |
| [comtypes](https://github.com/enthought/comtypes) | Vozes do Windows (SAPI) | MIT | sim |
| [FFmpeg](https://ffmpeg.org/) | Gerar MP3, M4A e M4B | LGPL-2.1+ ou GPL, conforme o build | não (instalado à parte) |
| [PyInstaller](https://pyinstaller.org/) | Gerar o executável | GPL-2.0 com exceção para programas empacotados | não (só no build) |
| [pytest](https://pytest.org/) | Testes | MIT | não |

### Clonagem de voz (opcional, ambiente separado)

| Componente | Licença |
|---|---|
| [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI) | MIT |
| [PyTorch](https://pytorch.org/) | BSD-3-Clause |

A clonagem só funciona quando o ambiente `.venv-clonagem` existe na máquina; nada disso vai no executável. Use a clonagem apenas com a sua voz ou com autorização da pessoa gravada.

## Vozes

| Voz | Origem | Licença |
|---|---|---|
| Piper pt-BR Faber, Cadu e Jeff | [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices), dados de [OHF-Voice/voice-datasets](https://github.com/OHF-Voice/voice-datasets) | CC0 (conforme o MODEL_CARD de cada voz) |
| Outras vozes Piper (pt-BR Edresson, inglês e espanhol) | [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) | Varia por voz: veja o `MODEL_CARD` de cada uma antes de redistribuir |
| Kokoro-82M (Dora, Alex, Santa e vozes em inglês e espanhol) | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 |
| Vozes do Windows (Maria, Zira etc.) | Instaladas no sistema | Licença do Windows; não são distribuídas com o DocAudio |

Somente a voz Faber vai junto do executável. As demais são baixadas pelo usuário em *Ferramentas → Idiomas e vozes*.

## Modelos e dados

| Item | Origem | Licença |
|---|---|---|
| Modelos de tradução pt/en/es | [Argos Translate](https://github.com/argosopentech/argos-translate), derivados do OPUS-MT (Jörg Tiedemann e Santhosh Thottingal, "OPUS-MT — Building open translation services for the World", EAMT 2020) | CC-BY 4.0 |
| Dados de OCR (`tessdata_fast` e `tessdata_best`) | [tesseract-ocr](https://github.com/tesseract-ocr) | Apache-2.0 |
| Dicionários de pronúncia em `resources/pronunciation` | Criados para o DocAudio | AGPL-3.0, como o resto do projeto |
