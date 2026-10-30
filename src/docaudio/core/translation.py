"""Traducao offline entre portugues, ingles e espanhol.

Usa os modelos do Argos Translate (derivados do OPUS-MT, licenca CC-BY 4.0)
executados direto pelo CTranslate2 + SentencePiece: sem PyTorch. Cada sentido
(ex.: pt -> en) e um pacote de ~70 MB baixado uma vez para dados/traducao.

O documento traduzido e uma copia: o original continua igual, e a previa
mostra a traducao para revisao antes de converter.
"""

import copy
import hashlib
import json
import logging
import shutil
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from docaudio.core import paths
from docaudio.core.language import LANGUAGES
from docaudio.core.model import BlockType, Document
from docaudio.text_processing.segmenter import split_sentences

log = logging.getLogger(__name__)

PACKAGE_URL = "https://argos-net.com/v1/translate-{src}_{dst}-{version}.argosmodel"
VERSIONS = {("pt", "en"): "1_9", ("en", "pt"): "1_9", ("es", "en"): "1_9", ("en", "es"): "1_0",
            ("pt", "es"): "1_0", ("es", "pt"): "1_0"}
PACKAGE_MB = 70
USER_AGENT = "DocAudio/0.1 (+offline audiobook converter)"
CREDIT = (
    "Modelos de tradução do Argos Translate, derivados do OPUS-MT (Jörg Tiedemann e Santhosh Thottingal, "
    "“OPUS-MT — Building open translation services for the World”, EAMT 2020), licença CC-BY 4.0."
)
# Blocos que nao se traduzem: codigo, enderecos e sumario.
UNTRANSLATED = {BlockType.CODE, BlockType.URL, BlockType.TOC}
BATCH = 16

Progress = Callable[[str], None]


class TranslationError(RuntimeError):
    pass


def models_dir() -> Path:
    return paths.data_dir() / "traducao"


def pair_dir(src: str, dst: str) -> Path:
    return models_dir() / f"{src}_{dst}"


def is_installed(src: str, dst: str) -> bool:
    folder = pair_dir(src, dst)
    return (folder / "model" / "model.bin").is_file() and (folder / "sentencepiece.model").is_file()


def pairs() -> list[tuple[str, str]]:
    return list(VERSIONS)


def download(src: str, dst: str, progress: Callable[[str, int, int], None] | None = None) -> Path:
    """Baixa e extrai o modelo (so o que o CTranslate2 usa; o divisor de frases stanza fica de fora)."""
    if (src, dst) not in VERSIONS:
        raise TranslationError(f"Tradução de {src} para {dst} não disponível.")
    target = pair_dir(src, dst)
    if is_installed(src, dst):
        return target
    models_dir().mkdir(parents=True, exist_ok=True)
    archive = models_dir() / f"{src}_{dst}.argosmodel.part"
    url = PACKAGE_URL.format(src=src, dst=dst, version=VERSIONS[(src, dst)])
    log.info("Baixando %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(256 * 1024):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(archive.name, done, total)
        if total and archive.stat().st_size != total:
            raise TranslationError("download incompleto")
        install_archive(archive, src, dst)
    finally:
        archive.unlink(missing_ok=True)
    return target


def install_archive(archive: Path, src: str, dst: str) -> Path:
    """Extrai um pacote .argosmodel (baixado aqui ou trazido pelo usuario) para dados/traducao."""
    target = pair_dir(src, dst)
    staging = target.with_name(target.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(archive) as package:
            for member in package.namelist():
                inner = member.split("/", 1)[1] if "/" in member else ""
                if not inner or inner.startswith("stanza/") or member.endswith("/"):
                    continue
                # Caminhos sempre dentro da pasta do modelo.
                destination = (staging / inner).resolve()
                if staging.resolve() not in destination.parents:
                    raise TranslationError(f"caminho inválido no pacote: {member}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(package.read(member))
    except zipfile.BadZipFile as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise TranslationError(f"pacote de tradução inválido: {exc}") from exc
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return target


class Translator:
    """Carrega cada modelo uma vez; uma traducao por vez (o CTranslate2 ja usa varios nucleos)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded: dict[tuple[str, str], tuple[object, object]] = {}
        self._cache: dict[str, str] = {}
        self._cache_dirty = False

    def _model(self, src: str, dst: str):
        if (src, dst) not in self._loaded:
            if not is_installed(src, dst):
                raise TranslationError(
                    f"Modelo de tradução {LANGUAGES[src].lower()} → {LANGUAGES[dst].lower()} não instalado "
                    "(Ferramentas > Idiomas e vozes)."
                )
            import ctranslate2
            import sentencepiece

            folder = pair_dir(src, dst)
            log.info("Carregando modelo de tradução %s → %s", src, dst)
            translator = ctranslate2.Translator(str(folder / "model"), device="cpu", compute_type="int8")
            tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.model"))
            self._loaded[(src, dst)] = (translator, tokenizer)
        return self._loaded[(src, dst)]

    # ---- cache em disco: retraduzir o mesmo documento e instantaneo ----------------

    def _cache_file(self) -> Path:
        return models_dir() / "cache.json"

    def _load_cache(self) -> None:
        if self._cache:
            return
        try:
            self._cache = json.loads(self._cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cache = {}

    def _save_cache(self) -> None:
        if self._cache_dirty:
            self._cache_file().parent.mkdir(parents=True, exist_ok=True)
            self._cache_file().write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            self._cache_dirty = False

    @staticmethod
    def _key(src: str, dst: str, sentence: str) -> str:
        return hashlib.sha1(f"{src}>{dst}>{sentence}".encode("utf-8")).hexdigest()

    def translate_sentences(self, sentences: list[str], src: str, dst: str) -> list[str]:
        with self._lock:
            self._load_cache()
            missing = [s for s in dict.fromkeys(sentences) if self._key(src, dst, s) not in self._cache]
            if missing:
                translator, tokenizer = self._model(src, dst)
                for start in range(0, len(missing), BATCH):
                    batch = missing[start:start + BATCH]
                    tokens = tokenizer.encode(batch, out_type=str)
                    results = translator.translate_batch(
                        tokens, beam_size=2, max_decoding_length=256, replace_unknowns=True
                    )
                    for sentence, result in zip(batch, results):
                        # "▁" marca o inicio de palavra no SentencePiece.
                        text = "".join(result.hypotheses[0]).replace("▁", " ").strip()
                        self._cache[self._key(src, dst, sentence)] = text
                self._cache_dirty = True
                self._save_cache()
            return [self._cache[self._key(src, dst, s)] for s in sentences]

    def translate_text(self, text: str, src: str, dst: str) -> str:
        """Traduz frase a frase, mantendo as quebras de linha (linhas de tabela, itens)."""
        lines = text.split("\n")
        pieces = [split_sentences(line) or ([line] if line.strip() else []) for line in lines]
        flat = [sentence for line in pieces for sentence in line]
        translated = iter(self.translate_sentences(flat, src, dst)) if flat else iter(())
        return "\n".join(" ".join(next(translated) for _ in line) for line in pieces)


_translator = Translator()


def translate_document(
    document: Document, target: str, progress: Progress | None = None, bilingual: bool = False
) -> Document:
    """Copia do documento no idioma `target` (titulos, paragrafos, listas, tabelas e notas).

    Com `bilingual`, cada bloco original (no seu idioma, com a sua voz) vem seguido
    da traducao: bom para estudar o outro idioma.
    """
    source = document.language
    if source == target:
        return document
    translated = copy.deepcopy(document)
    blocks = [b for c in translated.chapters for b in c.blocks if b.type not in UNTRANSLATED and b.text.strip()]
    wanted = {id(b) for b in blocks}
    total = len(blocks) + len(translated.chapters)
    done = 0
    translated.title = _translator.translate_text(document.title, source, target)
    for chapter in translated.chapters:
        chapter.title = _translator.translate_text(chapter.title, source, target)
        done += 1
        new_blocks = []
        for block in chapter.blocks:
            if id(block) not in wanted:
                new_blocks.append(block)
                continue
            text = _translator.translate_text(block.text, source, target)
            if bilingual:
                new_blocks.append(replace(block, language=source))
            new_blocks.append(replace(block, text=text, original_text=text, language=""))
            done += 1
            if progress and done % 10 == 0:
                progress(f"Traduzindo… {done * 100 // max(total, 1)}%")
        chapter.blocks = new_blocks
    translated.language = target
    translated.translated_from = source
    log.info(
        "%s traduzido de %s para %s (%d blocos%s).",
        document.source.name, LANGUAGES[source].lower(), LANGUAGES[target].lower(), len(blocks),
        ", bilíngue" if bilingual else "",
    )
    return translated
