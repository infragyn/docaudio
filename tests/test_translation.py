"""Traducao offline: pacote de modelo, documento traduzido, modo bilingue e vozes por trecho."""

import wave
import zipfile

import pytest

from docaudio.audio.wav import concat_wavs, duration_seconds
from docaudio.core import paths, translation
from docaudio.core.model import Block, BlockType, Chapter, Document, DocumentKind
from docaudio.storage.settings import Settings
from docaudio.text_processing.segmenter import segment_chapter


@pytest.fixture
def fake_translator(monkeypatch):
    """Traducao falsa e deterministica: prefixa o idioma de destino."""
    monkeypatch.setattr(
        translation._translator, "translate_text", lambda text, src, dst: f"[{dst}] {text}" if text else text
    )


def document(tmp_path):
    blocks = [
        Block(BlockType.PARAGRAPH, "A pilha segue LIFO."),
        Block(BlockType.CODE, "x = pilha.pop()"),
        Block(BlockType.TABLE, "Estrutura: Fila; Regra: FIFO"),
    ]
    return Document(tmp_path / "apostila.docx", DocumentKind.DOCX, "Apostila", [Chapter(1, "Estruturas", blocks)])


def test_documento_traduzido_e_uma_copia_sem_traduzir_codigo(tmp_path, fake_translator):
    original = document(tmp_path)
    translated = translation.translate_document(original, "en")
    assert translated.language == "en" and translated.translated_from == "pt"
    assert translated.title == "[en] Apostila"
    assert translated.chapters[0].title == "[en] Estruturas"
    assert [b.text for b in translated.chapters[0].blocks] == [
        "[en] A pilha segue LIFO.", "x = pilha.pop()", "[en] Estrutura: Fila; Regra: FIFO",
    ]
    assert original.chapters[0].blocks[0].text == "A pilha segue LIFO."  # original intacto


def test_bilingue_intercala_original_e_traducao(tmp_path, fake_translator):
    translated = translation.translate_document(document(tmp_path), "es", bilingual=True)
    assert [(b.language, b.text) for b in translated.chapters[0].blocks] == [
        ("pt", "A pilha segue LIFO."),
        ("", "[es] A pilha segue LIFO."),
        ("", "x = pilha.pop()"),
        ("pt", "Estrutura: Fila; Regra: FIFO"),
        ("", "[es] Estrutura: Fila; Regra: FIFO"),
    ]
    segments = segment_chapter(
        translated.chapters[0], lambda t: t, announce_title=False, language="es",
        normalizer_for=lambda lang: (lambda t: t.upper()),
    )
    assert [(s.language, s.text) for s in segments][:2] == [("pt", "A PILHA SEGUE LIFO."), ("", "[es] A pilha segue LIFO.")]


def test_conversao_bilingue_usa_a_voz_de_cada_idioma(tmp_path, fake_translator):
    from docaudio.core.conversion import ConversionJob
    from docaudio.text_processing.pronunciation import PronunciationDictionary
    from test_conversion import FakeProvider

    used = []

    class Recording(FakeProvider):
        def synthesize_to_wav(self, text, voice_id, *args, **kwargs):
            used.append((voice_id, text))
            return super().synthesize_to_wav(text, voice_id, *args, **kwargs)

    translated = translation.translate_document(document(tmp_path), "en", bilingual=True)
    settings = Settings(output_dir=str(tmp_path / "saida"), output_format="wav", voice="voz-pt",
                        voices_by_language={"en": "voz-en"}, announce_titles=False).for_language("en")
    ConversionJob(translated, settings, Recording(), PronunciationDictionary()).run()
    voices = [voice for voice, _ in used]
    assert voices[:2] == ["voz-pt", "voz-en"]
    assert set(voices) == {"voz-pt", "voz-en"}


def test_junta_trechos_de_vozes_com_taxas_diferentes(tmp_path):
    def tone(path, rate, seconds):
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\x10\x00" * int(rate * seconds))
        return path

    joined = concat_wavs([tone(tmp_path / "a.wav", 22050, 1.0), tone(tmp_path / "b.wav", 24000, 0.5)], tmp_path / "c.wav")
    assert duration_seconds(joined) == pytest.approx(1.5, abs=0.01)


def test_pacote_instala_so_o_modelo_e_recusa_caminhos_fora_da_pasta(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    archive = tmp_path / "pt_en.argosmodel"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("translate-pt_en-1_9/model/model.bin", b"m")
        package.writestr("translate-pt_en-1_9/sentencepiece.model", b"s")
        package.writestr("translate-pt_en-1_9/stanza/pt/tokenize.pt", b"t")
    translation.install_archive(archive, "pt", "en")
    assert translation.is_installed("pt", "en")
    assert not (translation.pair_dir("pt", "en") / "stanza").exists()

    evil = tmp_path / "evil.argosmodel"
    with zipfile.ZipFile(evil, "w") as package:
        package.writestr("x/../../fora.txt", b"x")
    with pytest.raises(translation.TranslationError):
        translation.install_archive(evil, "es", "en")
    assert not (tmp_path / "fora.txt").exists()


def test_sem_modelo_a_traducao_explica_onde_baixar(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAUDIO_DATA_DIR", str(tmp_path))
    with pytest.raises(translation.TranslationError, match="Idiomas e vozes"):
        translation.Translator().translate_text("Olá.", "es", "pt")


PROJECT_MODELS = paths.project_root() / "data" / "traducao"


@pytest.mark.skipif(not (PROJECT_MODELS / "pt_en" / "model" / "model.bin").is_file(), reason="modelo pt→en não baixado")
def test_traducao_real_portugues_para_ingles(monkeypatch):
    # Modelo do projeto (so leitura); o cache fica na pasta temporaria do teste.
    monkeypatch.setattr(translation, "pair_dir", lambda src, dst: PROJECT_MODELS / f"{src}_{dst}")
    text = translation.Translator().translate_text("A pilha segue a política LIFO.\nO servidor responde.", "pt", "en")
    lines = text.split("\n")
    assert len(lines) == 2 and "LIFO" in lines[0] and "server" in lines[1].lower() and "▁" not in text
