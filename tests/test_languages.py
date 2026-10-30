"""Leitura em ingles e espanhol: deteccao, normalizacao, vozes por idioma e fila."""

import pytest

from docaudio.core.language import detect_language, language_of_voice, phrase
from docaudio.core.loader import load_document
from docaudio.speech.kokoro_provider import VOICES, voice_language
from docaudio.storage.settings import DEFAULT_VOICES, Settings
from docaudio.text_processing.normalize import normalize
from docaudio.text_processing.segmenter import segment_chapter
from docaudio.core.model import Block, BlockType, Chapter

EN = "The protocol is used by the server and the client. It is not possible to connect without it, and we can test it."
ES = "El protocolo es usado por el servidor y los clientes. No es posible conectar sin él, pero también hay otras formas."
PT = "O protocolo é usado pelo servidor e pelos clientes. Não é possível conectar sem ele, e também não há outra forma."


@pytest.mark.parametrize("text, expected", [(EN, "en"), (ES, "es"), (PT, "pt"), ("", "pt"), ("12345 ###", "pt")])
def test_deteccao_de_idioma(text, expected):
    assert detect_language(text) == expected


def test_documento_detecta_idioma_e_nao_corrige_acentos_em_ingles(tmp_path):
    path = tmp_path / "manual.txt"
    path.write_text(f"Introduction\n\n{EN}\n\nInformation is power.", encoding="utf-8")
    document = load_document(path, Settings())
    assert document.language == "en"
    text = " ".join(b.text for c in document.chapters for b in c.blocks)
    assert "Information" in text and "Informação" not in text
    # Idioma escolhido na mao vence a deteccao.
    assert load_document(path, Settings(document_language="es")).language == "es"


def test_normalizacao_em_ingles_e_espanhol_deixa_numeros_para_a_voz():
    assert normalize("Visit www.site.com in 2024 & save 15%.", None, "en") == (
        "Visit site dot com in 2024 and save 15%."
    )
    assert normalize("(C) Escriba a ayuda@site.es", None, "es") == "Opción C: Escriba a ayuda arroba site punto es"
    assert normalize("CAPÍTULO UNO INTRODUCCIÓN GENERAL", None, "es") == "capítulo uno introducción general"
    # Portugues continua com a normalizacao completa.
    assert normalize("Em 2024", None) == "Em dois mil e vinte e quatro"


def test_frases_do_programa_no_idioma_do_documento():
    chapter = Chapter(1, "Intro", [Block(BlockType.CODE, "x = 1", include=False), Block(BlockType.PARAGRAPH, "Hello.")])
    segments = segment_chapter(chapter, lambda t: t, announce_title=False, language="en")
    assert segments[0].text == "Code example omitted from narration."
    assert phrase("answer", "es", number=3, letter="B") == "Respuesta de la pregunta 3: letra B."


def test_vozes_kokoro_por_idioma():
    assert voice_language("af_heart") == ("en-us", "en_US")
    assert voice_language("bm_george") == ("en-gb", "en_GB")
    assert voice_language("ef_dora") == ("es", "es_ES")
    assert voice_language("pf_dora") == ("pt-br", "pt_BR")
    languages = {language_of_voice(voice_language(key)[1]) for key in VOICES}
    assert languages == {"pt", "en", "es"}
    assert all(voice.removeprefix("kokoro:") in VOICES for voice in DEFAULT_VOICES.values())


def test_cada_idioma_lembra_sua_voz():
    settings = Settings(voice="pt_BR-faber-medium", voices_by_language={"en": "kokoro:bm_george"})
    assert settings.voice_for("pt") == "pt_BR-faber-medium"
    assert settings.voice_for("en") == "kokoro:bm_george"
    assert settings.voice_for("es") == DEFAULT_VOICES["es"]
    assert settings.for_language("en").voice == "kokoro:bm_george"
    assert Settings(voices_by_language="x", document_language="fr").voices_by_language == {}
    assert Settings(document_language="fr").document_language == "auto"


def test_fila_usa_a_voz_do_idioma_de_cada_documento(tmp_path):
    from docaudio.ui.conversion_worker import ConversionWorker, QueueItem
    from test_conversion import FakeProvider

    used = []

    class Recording(FakeProvider):
        def synthesize_to_wav(self, text, voice_id, *args, **kwargs):
            used.append(voice_id)
            return super().synthesize_to_wav(text, voice_id, *args, **kwargs)

    english, portuguese = tmp_path / "en.txt", tmp_path / "pt.txt"
    english.write_text(EN, encoding="utf-8")
    portuguese.write_text(PT, encoding="utf-8")
    settings = Settings(output_dir=str(tmp_path / "saida"), output_format="wav", voice="pt-voz",
                        voices_by_language={"en": "en-voz"})
    worker = ConversionWorker(
        [QueueItem(str(english), None, settings), QueueItem(str(portuguese), None, settings),
         QueueItem(str(english), None, Settings(**{**settings.__dict__, "voice": "voz-original"}), pick_voice=False)],
        Recording(),
    )
    worker.run()
    assert used[0] == "en-voz" and "pt-voz" in used and used[-1] == "voz-original"


def test_cada_botao_baixar_recebe_o_que_deve_baixar(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    from docaudio.core import translation
    from docaudio.core.language import LANGUAGES
    from docaudio.speech import voice_download
    from docaudio.ui.languages_dialog import LanguagesDialog

    calls = []
    monkeypatch.setattr(voice_download, "download_voice", lambda voice, *rest: calls.append(("voz", voice)))
    monkeypatch.setattr(voice_download, "download_tessdata", lambda language, *rest: calls.append(("ocr", language)))
    monkeypatch.setattr(translation, "download", lambda src, dst, *rest: calls.append(("traducao", src, dst)))
    monkeypatch.setattr(translation, "is_installed", lambda src, dst: False)

    class Provider:
        def list_voices(self):
            return []

    dialog = LanguagesDialog(Provider(), Settings(), lambda: None)
    dialog._build = lambda: None  # mantem os mesmos botoes entre um clique e outro
    buttons = [b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "Baixar"]
    for button in buttons:
        button.click()
        while dialog._workers:
            app.processEvents()
        assert dialog.status.text().startswith("Instalado:"), dialog.status.text()
    dialog.close()

    voices = {("voz", v[0]) for language in LANGUAGES for v in voice_download.PIPER_CATALOG.get(language, [])}
    assert voices <= set(calls)
    assert {c[1:] for c in calls if c[0] == "traducao"} == set(translation.pairs())
    assert {c[1] for c in calls if c[0] == "ocr"} <= set(LANGUAGES) and any(c[0] == "ocr" for c in calls)
