"""Conversao de um documento em audio: trechos -> capitulos -> MP3, M4B ou WAV.

Estrutura na pasta de saida:

    <saida>/<titulo do documento>/
        01 - Capitulo.mp3 ...           (ou <titulo>.m4a, <titulo>.m4b)
        .docaudio/manifest.json         estado para retomar
        .docaudio/trechos/<hash>.wav    temporarios, apagados ao concluir
        .docaudio/capitulos/<hash>.wav  temporarios, apagados ao concluir

Trechos e capitulos sao identificados pelo hash do texto e das configuracoes de
voz: ao retomar, o que ja existe com o mesmo hash nao e sintetizado de novo.
"""

import hashlib
import json
import logging
import re
import shutil
import threading
from collections.abc import Callable
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from docaudio import APP_NAME
from docaudio.audio import ffmpeg as ffmpeg_audio
from docaudio.audio.ffmpeg import Cancelled
from docaudio.audio.cover import make_cover
from docaudio.audio.wav import AudioError, concat_wavs, duration_seconds
from docaudio.core.dependencies import find_ffmpeg
from docaudio.core.model import Chapter, Document
from docaudio.core.study import study_document_chapters
from docaudio.speech.base import SpeechError, SpeechProvider
from docaudio.storage.manifest import ChapterState, Manifest
from docaudio.storage.settings import Settings
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing.pronunciation import PronunciationDictionary, load_default
from docaudio.text_processing.segmenter import Segment, segment_chapter

log = logging.getLogger(__name__)

WORK_DIR = ".docaudio"
MAX_NAME = 80


class ConversionError(RuntimeError):
    """Falha da conversao com mensagem pronta para o usuario."""


@dataclass
class Progress:
    document: str
    chapter_index: int
    chapter_count: int
    chapter_title: str
    done_segments: int
    total_segments: int
    message: str

    @property
    def fraction(self) -> float:
        return self.done_segments / self.total_segments if self.total_segments else 0.0


@dataclass
class ConversionResult:
    folder: Path
    files: list[Path] = field(default_factory=list)
    reused_chapters: int = 0


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:MAX_NAME].rstrip(" .") or "sem titulo"


# O Windows recusa caminhos acima de 260 caracteres; sobra folga para ".part" e temporarios.
MAX_PATH = 240


def fit_name(folder: Path, stem: str, extension: str) -> str:
    """Nome do arquivo encurtado, se preciso, para o caminho completo caber no limite do Windows."""
    room = MAX_PATH - len(str(folder)) - len(extension) - 2
    if len(stem) > room:
        stem = stem[: max(room, 8)].rstrip(" .-")
    return f"{stem}.{extension}"


def _hash(*parts) -> str:
    data = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def output_folder(document: Document, settings: Settings) -> Path:
    return settings.resolved_output_dir() / safe_filename(document.title or document.source.stem)


@dataclass(frozen=True)
class Interrupted:
    """Conversao que parou no meio (cancelada, com erro ou com o programa fechado)."""

    document: Path
    folder: Path
    title: str
    voice: str
    speed: float
    format: str
    done_segments: int
    total_segments: int
    updated_at: str
    language: str = ""
    translated_from: str = ""
    bilingual: bool = False

    @property
    def fraction(self) -> float:
        return min(1.0, self.done_segments / self.total_segments) if self.total_segments else 0.0


def find_interrupted(output_dir: Path) -> list[Interrupted]:
    """Conversoes por terminar na pasta de saida, da mais recente para a mais antiga.

    Os trechos ja narrados ficam em .docaudio/trechos ate a conversao terminar,
    entao a pasta existir com algum audio indica trabalho a retomar.
    """
    found = []
    if not output_dir.is_dir():
        return found
    for manifest_path in output_dir.glob(f"*/{WORK_DIR}/manifest.json"):
        work = manifest_path.parent
        pieces = list((work / "trechos").glob("*.wav")) if (work / "trechos").is_dir() else []
        manifest = Manifest.load(manifest_path) if pieces else None
        if manifest is None or not Path(manifest.document).is_file():
            continue
        found.append(
            Interrupted(
                document=Path(manifest.document),
                folder=work.parent,
                title=manifest.title,
                voice=manifest.voice,
                speed=manifest.speed,
                format=manifest.format,
                done_segments=len(pieces),
                total_segments=sum(c.segments for c in manifest.chapters),
                updated_at=manifest.updated_at,
                language=manifest.language,
                translated_from=manifest.translated_from,
                bilingual=manifest.bilingual,
            )
        )
    return sorted(found, key=lambda item: item.updated_at, reverse=True)


@dataclass
class _PlannedChapter:
    track: int
    chapter: Chapter
    segments: list[Segment]
    hash: str
    output_name: str


class ConversionJob:
    def __init__(
        self,
        document: Document,
        settings: Settings,
        provider: SpeechProvider,
        dictionary: PronunciationDictionary | None = None,
        progress: Callable[[Progress], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        self.document = document
        self.settings = settings
        self.provider = provider
        # O dicionario recebido vale para portugues; outros idiomas usam o seu.
        if document.language != "pt":
            dictionary = load_default(document.language)
        self.dictionary = dictionary if dictionary is not None else load_default()
        self.progress_callback = progress
        self.cancel = cancel or threading.Event()
        self.folder = output_folder(document, settings)
        self.work = self.folder / WORK_DIR
        self.manifest_path = self.work / "manifest.json"
        self._done = 0
        self._total = 0
        # Por hash do capitulo: tempos dos trechos e duracao total, gravados no manifesto.
        self._timings: dict[str, list] = {}
        self._durations: dict[str, float] = {}

    # ---- planejamento ------------------------------------------------------

    def _voice_key(self) -> tuple:
        s = self.settings
        return (self._engine(), s.voice, round(s.speed, 2), s.sentence_pause_ms)

    def _engine(self) -> str:
        # Nome do mecanismo da voz (ex.: Piper), estavel para o reaproveitamento de trechos.
        engine_name = getattr(self.provider, "engine_name", None)
        return engine_name(self.settings.voice) if engine_name else self.provider.name

    def plan(self) -> list[_PlannedChapter]:
        s = self.settings
        chapters = [c for c in self.document.included_chapters() if c.char_count() > 0]
        if s.study_mode:
            studied = study_document_chapters(self.document, s.think_pause_s * 1000)
            chapters = [studied[c.number] for c in chapters]
        planned = []
        for track, chapter in enumerate(chapters, start=1):
            segments = segment_chapter(
                chapter,
                lambda text: normalize(text, self.dictionary, self.document.language),
                announce_title=s.announce_titles,
                sentence_pause_ms=s.sentence_pause_ms,
                paragraph_pause_ms=s.paragraph_pause_ms,
                language=self.document.language,
                normalizer_for=self._normalizer_for,
            )
            if not segments:
                continue
            chapter_hash = _hash(
                self._voice_key(), s.chapter_pause_ms, [(g.text, g.pause_after_ms, g.language) for g in segments]
            )
            name = fit_name(self.folder, f"{track:02d} - {safe_filename(chapter.title)}", "wav" if s.output_format == "wav" else "mp3")
            planned.append(_PlannedChapter(track, chapter, segments, chapter_hash, name))
        return planned

    # ---- execucao ------------------------------------------------------------

    def _report(self, planned: _PlannedChapter | None, count: int, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(
                Progress(
                    document=self.document.source.name,
                    chapter_index=planned.track if planned else count,
                    chapter_count=count,
                    chapter_title=planned.chapter.title if planned else "",
                    done_segments=self._done,
                    total_segments=self._total,
                    message=message,
                )
            )

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def _normalizer_for(self, language: str):
        """Normalizacao de um bloco em outro idioma (modo bilingue), com o dicionario desse idioma."""
        dictionary = load_default(language)
        return lambda text: normalize(text, dictionary, language)

    def _segment_voice(self, segment: Segment) -> str:
        return self.settings.voice_for(segment.language) if segment.language else self.settings.voice

    def _segment_wav(self, segment: Segment) -> Path:
        voice = self._segment_voice(segment)
        key = _hash(self._voice_key(), segment.text, segment.pause_after_ms, voice if segment.language else "")
        return self.work / "trechos" / f"{key[:32]}.wav"

    def _chapter_wav(self, planned: _PlannedChapter, count: int) -> Path:
        """Sintetiza os trechos que faltam e une tudo no WAV do capitulo."""
        target = self.work / "capitulos" / f"{planned.hash[:32]}.wav"
        if target.exists():
            self._done += len(planned.segments)
            self._record_timings(planned)
            return target
        wavs = []
        for segment in planned.segments:
            self._check_cancel()
            wav = self._segment_wav(segment)
            if not wav.exists():
                self.provider.synthesize_to_wav(
                    segment.text,
                    self._segment_voice(segment),
                    self.settings.speed,
                    wav,
                    sentence_pause_ms=self.settings.sentence_pause_ms,
                    trailing_silence_ms=segment.pause_after_ms,
                )
            wavs.append(wav)
            self._done += 1
            self._report(planned, count, f"Narrando “{planned.chapter.title}”")
        self._record_timings(planned)
        return concat_wavs(wavs, target, trailing_silence_ms=self.settings.chapter_pause_ms)

    def _cover(self) -> Path | None:
        """Capa gerada uma vez por conversao (na pasta de trabalho)."""
        if not hasattr(self, "_cover_path"):
            self._cover_path = make_cover(self.document, self.work / "capa.jpg")
        return self._cover_path

    def _record_timings(self, planned: _PlannedChapter) -> None:
        """Inicio de cada trecho no audio do capitulo (os WAVs dos trechos ainda existem aqui)."""
        timings, position = [], 0.0
        for segment in planned.segments:
            wav = self._segment_wav(segment)
            timings.append([round(position, 2), segment.text])
            position += duration_seconds(wav) if wav.exists() else 0.0
        self._timings[planned.hash] = timings
        self._durations[planned.hash] = position + self.settings.chapter_pause_ms / 1000

    def run(self) -> ConversionResult:
        s = self.settings
        ffmpeg = find_ffmpeg(s.ffmpeg_path)
        if s.output_format in ("mp3", "m4a", "m4b") and not ffmpeg:
            raise ConversionError(
                "FFmpeg não encontrado: necessário para gerar MP3, M4A ou M4B. "
                "Instale o FFmpeg ou escolha a saída em WAV."
            )
        planned = self.plan()
        if not planned:
            raise ConversionError(f"“{self.document.title}” não tem capítulos com texto para narrar.")

        self.work.mkdir(parents=True, exist_ok=True)
        previous = Manifest.load(self.manifest_path)
        manifest = Manifest(
            document=str(self.document.source),
            title=self.document.title,
            voice=s.voice,
            speed=s.speed,
            format=s.output_format,
            chapters=[ChapterState(p.track, p.chapter.title, p.hash, segments=len(p.segments)) for p in planned],
            language=self.document.language,
            translated_from=self.document.translated_from,
            bilingual=self.document.bilingual,
        )
        # Gravado ja no inicio: se o programa fechar no meio, a conversao aparece para retomar.
        manifest.save(self.manifest_path)
        result = ConversionResult(self.folder)
        self._total = sum(len(p.segments) for p in planned)
        count = len(planned)
        log.info(
            "Convertendo %s: %d capítulo(s), %d trecho(s), formato %s, voz %s.",
            self.document.source.name, count, self._total, s.output_format, s.voice,
        )
        try:
            if s.output_format in ("m4a", "m4b"):
                self._run_m4b(planned, manifest, previous, ffmpeg, result)
            else:
                self._run_per_chapter(planned, manifest, previous, ffmpeg, result)
        except Cancelled:
            manifest.save(self.manifest_path)
            log.info("Conversão de %s interrompida; será retomada do ponto em que parou.", self.document.source.name)
            raise
        except (SpeechError, AudioError, OSError) as exc:
            manifest.save(self.manifest_path)
            raise ConversionError(f"Falha ao converter “{self.document.title}”: {exc}") from exc

        self._remove_stale_outputs(previous, manifest)
        manifest.save(self.manifest_path)
        for temp in ("trechos", "capitulos"):
            shutil.rmtree(self.work / temp, ignore_errors=True)
        for temp in ("capitulos.txt", "capitulos.ffmeta", "capa.jpg"):
            (self.work / temp).unlink(missing_ok=True)
        log.info("Conversão concluída: %d arquivo(s) em %s", len(result.files), self.folder)
        self._report(None, count, "Concluído")
        return result

    def _narrator(self) -> str:
        find_voice = getattr(self.provider, "find_voice", None)
        voice = find_voice(self.settings.voice) if find_voice else None
        return voice.label if voice else self.settings.voice

    def _metadata(self, title: str, track: str = "") -> dict[str, str]:
        # Apps de audiolivro agrupam por album/artista e mostram o narrador.
        author = self.document.author or self.document.title
        return {
            "title": title,
            "album": self.document.title,
            "artist": author,
            "album_artist": author,
            "composer": f"Narração: {self._narrator()}",
            "track": track,
            "genre": "Audiobook",
            "date": str(datetime.now().year),
            "comment": f"Gerado pelo {APP_NAME} a partir de {self.document.source.name}",
        }

    def _run_per_chapter(self, planned, manifest, previous, ffmpeg, result) -> None:
        count = len(planned)
        for item, state in zip(planned, manifest.chapters):
            output = self.folder / item.output_name
            old = previous.chapter(item.hash) if previous else None
            if old and old.status == "concluido" and old.output == item.output_name and output.exists():
                state.status, state.output = "concluido", item.output_name
                state.timings = old.timings
                self._done += len(item.segments)
                result.files.append(output)
                result.reused_chapters += 1
                self._report(item, count, f"“{item.chapter.title}” já estava pronto")
                continue
            wav = self._chapter_wav(item, count)
            self._check_cancel()
            self._report(item, count, f"Gerando {output.suffix[1:].upper()} de “{item.chapter.title}”")
            if self.settings.output_format == "wav":
                shutil.copyfile(wav, output)
            else:
                metadata = self._metadata(item.chapter.title, f"{item.track}/{count}")
                ffmpeg_audio.encode_mp3(ffmpeg, wav, output, metadata, self.cancel, self._cover())
            wav.unlink(missing_ok=True)
            state.status, state.output = "concluido", item.output_name
            state.timings = self._timings.get(item.hash, [])
            manifest.save(self.manifest_path)
            result.files.append(output)

    def _run_m4b(self, planned, manifest, previous, ffmpeg, result) -> None:
        count = len(planned)
        final_hash = _hash([p.hash for p in planned], [p.chapter.title for p in planned])
        output = self.folder / fit_name(self.folder, safe_filename(self.document.title or self.document.source.stem), self.settings.output_format)
        if previous and previous.final_hash == final_hash and output.exists():
            for state in manifest.chapters:
                state.status = "concluido"
                if old := previous.chapter(state.hash):
                    state.timings, state.start_s = old.timings, old.start_s
            manifest.final_output, manifest.final_hash = output.name, final_hash
            self._done = self._total
            result.files.append(output)
            result.reused_chapters = count
            return
        wavs = []
        start = 0.0
        for item, state in zip(planned, manifest.chapters):
            wavs.append(self._chapter_wav(item, count))
            state.status = "concluido"
            state.timings, state.start_s = self._timings.get(item.hash, []), round(start, 2)
            start += self._durations.get(item.hash, 0.0)
            manifest.save(self.manifest_path)
        self._check_cancel()
        self._report(planned[-1], count, f"Gerando audiolivro {output.suffix[1:].upper()}")
        ffmpeg_audio.encode_m4b(
            ffmpeg, wavs, [p.chapter.title for p in planned], output,
            self._metadata(self.document.title), self.work, self.cancel, self._cover(),
        )
        manifest.final_output, manifest.final_hash = output.name, final_hash
        result.files.append(output)

    def _remove_stale_outputs(self, previous: Manifest | None, current: Manifest) -> None:
        """Apaga arquivos gerados antes pelo DocAudio que nao fazem mais parte da conversao.

        So vale para o mesmo formato: trocar MP3 por M4B mantem os MP3 ja gerados.
        """
        if not previous or previous.format != current.format:
            return
        keep = {c.output for c in current.chapters if c.output} | {current.final_output}
        old = {c.output for c in previous.chapters if c.output} | {previous.final_output}
        for name in old - keep:
            if name and (self.folder / name).is_file():
                (self.folder / name).unlink()
                log.info("Arquivo antigo removido: %s", name)
