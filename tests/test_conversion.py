import threading
import wave
from pathlib import Path

import pytest

from docaudio.audio import ffmpeg as ffmpeg_audio
from docaudio.audio.ffmpeg import Cancelled, chapters_metadata
from docaudio.audio.wav import concat_wavs, duration_seconds
from docaudio.core import conversion
from docaudio.core.conversion import ConversionError, ConversionJob, safe_filename
from docaudio.core.dependencies import find_ffmpeg
from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind
from docaudio.speech.piper_provider import silence
from docaudio.storage.manifest import Manifest
from docaudio.storage.settings import Settings
from docaudio.text_processing.pronunciation import PronunciationDictionary
from docaudio.text_processing.segmenter import group_sentences, segment_chapter, split_sentences

RATE = 16000


class FakeProvider:
    """Gera 0,1 s de audio por trecho e conta as chamadas."""

    name = "Falso"
    offline = True

    def __init__(self, fail_on: str | None = None, cancel_after: int | None = None, cancel=None) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.cancel_after = cancel_after
        self.cancel = cancel

    def list_voices(self):
        return []

    def estimated_cost(self, characters):
        return 0.0

    def synthesize_to_wav(self, text, voice_id, speed, out_path, sentence_pause_ms=0, trailing_silence_ms=0):
        if self.fail_on and self.fail_on in text:
            from docaudio.speech.base import SpeechError

            raise SpeechError("falha simulada")
        self.calls.append(text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(b"\x01\x00" * (RATE // 10) + silence(RATE, 2, trailing_silence_ms))
        if self.cancel_after and len(self.calls) >= self.cancel_after:
            self.cancel.set()
        return out_path


def make_document(tmp_path, chapters=None) -> Document:
    chapters = chapters or [
        Chapter(1, "Introdução", [Block(BlockType.PARAGRAPH, "Primeira frase. Segunda frase.")]),
        Chapter(2, "Capítulo: Prática", [Block(BlockType.PARAGRAPH, "Texto do segundo capítulo.")]),
        Chapter(3, "Vazio", [Block(BlockType.NOTE, "nota", include=False)]),
    ]
    return Document(source=tmp_path / "apostila.pdf", kind=DocumentKind.PDF, title="Apostila", chapters=chapters)


def settings_for(tmp_path, **kwargs) -> Settings:
    return Settings(output_dir=str(tmp_path / "saida"), output_format="wav", **kwargs)


def job(document, settings, provider, **kwargs) -> ConversionJob:
    return ConversionJob(document, settings, provider, dictionary=PronunciationDictionary(), **kwargs)


# ---- trechos ------------------------------------------------------------------


def test_split_sentences_and_group():
    sentences = split_sentences("Primeira frase. Segunda? Sim! 3 itens.\nLinha de tabela.")
    assert sentences == ["Primeira frase.", "Segunda?", "Sim!", "3 itens.", "Linha de tabela."]
    assert group_sentences(["a" * 30, "b" * 30, "c" * 30], limit=70) == ["a" * 30 + " " + "b" * 30, "c" * 30]
    long = "palavra " * 100
    assert all(len(g) <= 120 for g in group_sentences([long], limit=120))


def test_symbol_only_lines_are_not_segments():
    chapter = Chapter(1, "", [Block(BlockType.PARAGRAPH, "if (x) {\nreturn y;\n}"), Block(BlockType.PARAGRAPH, "}")])
    segments = segment_chapter(chapter, lambda t: t, announce_title=False)
    assert [s.text for s in segments] == ["if (x) { return y;"]


def test_piper_turns_unspeakable_text_into_silence(tmp_path):
    from docaudio.core import paths
    from docaudio.speech.piper_provider import PiperProvider

    provider = PiperProvider([paths.bundled_voices_dir()])
    if not provider.find_voice("pt_BR-faber-medium"):
        pytest.skip("voz pt_BR-faber-medium nao instalada")
    out = provider.synthesize_to_wav("}", "pt_BR-faber-medium", 1.0, tmp_path / "s.wav", trailing_silence_ms=500)
    assert duration_seconds(out) == pytest.approx(0.5, abs=0.01)


def test_segment_chapter_pauses_and_title():
    chapter = Chapter(
        1, "Introdução", [Block(BlockType.HEADING, "Sub", level=2), Block(BlockType.PARAGRAPH, "Um. Dois.")]
    )
    segments = segment_chapter(chapter, str.upper, announce_title=True, sentence_pause_ms=100, paragraph_pause_ms=500, limit=5)
    assert [(s.text, s.pause_after_ms) for s in segments] == [
        ("INTRODUÇÃO.", 650), ("SUB.", 650), ("UM.", 100), ("DOIS.", 500),
    ]
    assert segment_chapter(chapter, str.upper, announce_title=False)[0].text == "SUB."


# ---- audio --------------------------------------------------------------------


def test_concat_wavs_adds_trailing_silence(tmp_path):
    provider = FakeProvider()
    parts = [provider.synthesize_to_wav("x", "v", 1, tmp_path / f"{i}.wav") for i in range(3)]
    out = concat_wavs(parts, tmp_path / "out.wav", trailing_silence_ms=500)
    assert duration_seconds(out) == pytest.approx(0.8)


def test_chapters_metadata_escapes_and_times(tmp_path):
    provider = FakeProvider()
    wavs = [provider.synthesize_to_wav("x", "v", 1, tmp_path / f"{i}.wav", trailing_silence_ms=900) for i in range(2)]
    text = chapters_metadata(["Um; dois", "A=B"], wavs, {"title": "Livro #1"})
    assert "title=Livro \\#1" in text
    assert "START=0\nEND=1000\ntitle=Um\\; dois" in text
    assert "START=1000\nEND=2000\ntitle=A\\=B" in text


def test_safe_filename():
    assert safe_filename('Cap. 1: "Redes"/TCP?') == "Cap. 1 Redes TCP"
    assert safe_filename("...") == "sem titulo"
    assert len(safe_filename("x" * 200)) == 80


# ---- conversao ------------------------------------------------------------------


def test_wav_conversion_creates_files_and_manifest(tmp_path):
    provider = FakeProvider()
    result = job(make_document(tmp_path), settings_for(tmp_path), provider).run()
    assert [f.name for f in result.files] == ["01 - Introdução.wav", "02 - Capítulo Prática.wav"]
    manifest = Manifest.load(result.folder / ".docaudio" / "manifest.json")
    assert [c.status for c in manifest.chapters] == ["concluido", "concluido"]
    assert not (result.folder / ".docaudio" / "trechos").exists()
    # titulo anunciado + duas frases no primeiro capitulo
    assert provider.calls[:2] == ["Introdução.", "Primeira frase. Segunda frase."]


def test_second_run_reuses_finished_chapters(tmp_path):
    settings = settings_for(tmp_path)
    job(make_document(tmp_path), settings, FakeProvider()).run()
    provider = FakeProvider()
    result = job(make_document(tmp_path), settings, provider).run()
    assert provider.calls == []
    assert result.reused_chapters == 2


def test_edited_chapter_is_regenerated_and_renamed_output_replaced(tmp_path):
    settings = settings_for(tmp_path)
    first = job(make_document(tmp_path), settings, FakeProvider()).run()
    document = make_document(tmp_path)
    document.chapters[1].title = "Prática"
    document.chapters[1].blocks[0].text = "Texto alterado."
    provider = FakeProvider()
    result = job(document, settings, provider).run()
    assert result.reused_chapters == 1
    assert "Texto alterado." in provider.calls
    assert not (first.folder / "02 - Capítulo Prática.wav").exists()
    assert (first.folder / "02 - Prática.wav").exists()


def test_cancel_then_resume_skips_generated_segments(tmp_path):
    settings = settings_for(tmp_path)
    cancel = threading.Event()
    provider = FakeProvider(cancel_after=2, cancel=cancel)
    with pytest.raises(Cancelled):
        job(make_document(tmp_path), settings, provider, cancel=cancel).run()
    assert len(provider.calls) == 2

    resumed = FakeProvider()
    result = job(make_document(tmp_path), settings, resumed).run()
    assert len(result.files) == 2
    assert "Introdução." not in resumed.calls  # trecho ja gerado antes da interrupcao
    assert len(resumed.calls) == 2


def test_speech_failure_becomes_conversion_error(tmp_path):
    with pytest.raises(ConversionError, match="falha simulada"):
        job(make_document(tmp_path), settings_for(tmp_path), FakeProvider(fail_on="segundo")).run()


def test_mp3_requires_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(conversion, "find_ffmpeg", lambda _: None)
    settings = Settings(output_dir=str(tmp_path), output_format="mp3")
    with pytest.raises(ConversionError, match="FFmpeg"):
        job(make_document(tmp_path), settings, FakeProvider()).run()


def test_document_without_text_is_rejected(tmp_path):
    document = make_document(tmp_path, [Chapter(1, "Vazio", [])])
    with pytest.raises(ConversionError, match="não tem capítulos"):
        job(document, settings_for(tmp_path), FakeProvider()).run()


needs_ffmpeg = pytest.mark.skipif(find_ffmpeg() is None, reason="FFmpeg não instalado")


@needs_ffmpeg
@pytest.mark.parametrize("fmt", ["mp3", "m4a", "m4b"])
def test_real_ffmpeg_outputs(tmp_path, fmt):
    settings = Settings(output_dir=str(tmp_path / "saida"), output_format=fmt)
    result = job(make_document(tmp_path), settings, FakeProvider()).run()
    expected = 2 if fmt == "mp3" else 1
    assert len(result.files) == expected
    assert all(f.stat().st_size > 0 and f.suffix == f".{fmt}" for f in result.files)
    assert [p.name for p in (result.folder / ".docaudio").iterdir()] == ["manifest.json"]


@needs_ffmpeg
def test_ffmpeg_cancel_kills_process(tmp_path):
    provider = FakeProvider()
    wav = provider.synthesize_to_wav("x", "v", 1, tmp_path / "a.wav", trailing_silence_ms=2000)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        ffmpeg_audio.encode_mp3(find_ffmpeg(), wav, tmp_path / "a.mp3", {}, cancel)
    assert not (tmp_path / "a.mp3").exists()


def test_nome_do_arquivo_encurta_para_caber_no_limite_do_windows(tmp_path):
    folder = tmp_path / ("pasta comprida " * 5).strip()
    name = conversion.fit_name(folder, "01 - " + "Título longo de capítulo " * 6, "mp3")
    assert name.endswith(".mp3") and name.startswith("01 - Título")
    assert len(str(folder / name)) <= conversion.MAX_PATH
    assert conversion.fit_name(tmp_path, "01 - Curto", "mp3") == "01 - Curto.mp3"
