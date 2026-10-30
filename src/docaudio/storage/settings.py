"""Preferencias do usuario gravadas em settings.json."""

import json
import logging
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from docaudio.core import paths

log = logging.getLogger(__name__)

DEFAULT_VOICE = "pt_BR-faber-medium"
# Vozes Kokoro ja vem no mesmo modelo das vozes em portugues: nada a baixar.
DEFAULT_VOICES = {"en": "kokoro:af_heart", "es": "kokoro:ef_dora"}
MIN_SPEED = 0.5
MAX_SPEED = 2.0
OUTPUT_FORMATS = ("mp3", "m4a", "m4b", "wav")


@dataclass
class Settings:
    voice: str = DEFAULT_VOICE
    # 1.0 e a velocidade natural da voz; 1.5 fala 50% mais rapido.
    speed: float = 1.0
    output_dir: str = ""
    ffmpeg_path: str = ""
    last_open_dir: str = ""
    # Regras de leitura por tipo de bloco.
    read_tables: bool = True
    read_urls: bool = False
    read_notes: bool = False
    read_references: bool = False
    read_code: bool = False
    # Corrige palavras digitadas sem acento ("Informacao" -> "Informação").
    restore_accents: bool = True
    # Trava da clonagem de voz: False impede qualquer tentativa nesta maquina.
    clone_enabled: bool = True
    # OCR de paginas digitalizadas (Tesseract embutido no PyMuPDF).
    ocr_enabled: bool = True
    ocr_language: str = "por"
    # "rapido" (tessdata_fast, ~10x mais rapido) ou "preciso" (tessdata_best).
    ocr_quality: str = "rapido"
    tessdata_dir: str = ""
    # Capitulos maiores que isto sao divididos em partes (0 = sem limite).
    max_chapter_chars: int = 0
    # Saida: "mp3" (um arquivo por capitulo), "m4a" ou "m4b" (audiolivro unico) ou "wav" (sem FFmpeg).
    # M4A e M4B sao o mesmo arquivo; so o M4A abre nos players do Windows.
    output_format: str = "mp3"
    announce_titles: bool = True
    sentence_pause_ms: int = 250
    paragraph_pause_ms: int = 700
    chapter_pause_ms: int = 1500
    # Modo questoes: pausa para pensar e resposta do gabarito logo depois de cada questao.
    study_mode: bool = False
    think_pause_s: int = 5
    # Idioma dos documentos: "auto" (detecta), "pt", "en" ou "es".
    document_language: str = "auto"
    # Voz de cada idioma alem do portugues (que usa `voice`), ex.: {"en": "kokoro:af_heart"}.
    voices_by_language: dict = field(default_factory=dict)
    # Dicionarios tematicos de pronuncia ligados (nomes de resources/pronunciation/temas).
    pronunciation_themes: list[str] = field(
        default_factory=lambda: ["ti", "direito", "latim", "administracao_publica", "contabilidade", "saude"]
    )
    # Pasta vigiada: documentos novos nela entram sozinhos na fila de conversao.
    watch_dir: str = ""
    watch_enabled: bool = False
    # Lista de documentos da ultima sessao, reaberta ao iniciar.
    documents: list[str] = field(default_factory=list)
    # Aba de opcoes aberta por ultimo (0 = Voz e saida, 1 = Leitura, 2 = Audio, 3 = Player).
    options_tab: int = 0
    # Aba Player: ultimo arquivo aberto e velocidade.
    player_file: str = ""
    player_speed: float = 1.0

    def __post_init__(self) -> None:
        try:
            self.speed = float(self.speed)
        except (TypeError, ValueError):
            self.speed = 1.0
        self.speed = min(MAX_SPEED, max(MIN_SPEED, self.speed))
        if self.output_format not in OUTPUT_FORMATS:
            self.output_format = "mp3"
        if self.document_language not in ("auto", "pt", "en", "es"):
            self.document_language = "auto"
        if not isinstance(self.voices_by_language, dict):
            self.voices_by_language = {}
        if not isinstance(self.documents, list):
            self.documents = []
        self.documents = [str(d) for d in self.documents if isinstance(d, str) and d]

    def voice_for(self, language: str) -> str:
        """Voz usada para documentos no idioma; o portugues usa a voz principal."""
        if language == "pt":
            # Numa copia feita por for_language, a voz do portugues fica guardada no mapa.
            return self.voices_by_language.get("pt") or self.voice
        return self.voices_by_language.get(language) or DEFAULT_VOICES.get(language, self.voice)

    def for_language(self, language: str) -> "Settings":
        """Copia com a voz do idioma (cada documento da fila usa a do seu idioma).

        As vozes dos outros idiomas continuam no mapa: o modo bilingue usa duas.
        """
        voices = {**self.voices_by_language, "pt": self.voice_for("pt")}
        return replace(self, voice=self.voice_for(language), voices_by_language=voices)

    def resolved_output_dir(self) -> Path:
        return Path(self.output_dir) if self.output_dir else paths.default_output_dir()

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.settings_file()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("conteudo nao e um objeto JSON")
        except (OSError, ValueError) as exc:
            log.warning("Configuracoes invalidas em %s (%s); usando padroes.", path, exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or paths.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
