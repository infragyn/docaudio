"""Idiomas de leitura: portugues, ingles e espanhol.

O portugues tem a normalizacao completa do DocAudio; em ingles e espanhol os
numeros, datas e moedas ficam com o fonetizador das vozes (espeak-ng), que ja
conhece esses idiomas.
"""

import re
from collections import Counter

LANGUAGES = {"pt": "Português", "en": "Inglês", "es": "Espanhol"}
AUTO = "auto"
DEFAULT = "pt"

# Palavras muito frequentes e exclusivas (ou quase) de cada idioma.
STOPWORDS = {
    "pt": {"não", "você", "são", "também", "está", "então", "uma", "os", "ao", "pelo", "pela", "isso", "muito",
           "quando", "seu", "sua", "foi", "pode", "mais", "entre", "após", "às", "nas", "nos", "dos", "das", "é"},
    "es": {"el", "los", "las", "del", "y", "es", "una", "por", "con", "para", "como", "pero", "más", "también",
           "está", "son", "fue", "puede", "cuando", "muy", "entre", "sin", "sobre", "él", "ella", "porque", "hay"},
    "en": {"the", "and", "of", "to", "is", "in", "that", "it", "for", "with", "as", "was", "on", "are", "be",
           "this", "by", "an", "or", "which", "from", "at", "not", "have", "can", "will", "you", "we"},
}
WORD = re.compile(r"[a-záéíóúâêôãõçñü]+", re.IGNORECASE)
SAMPLE_CHARS = 20_000


def detect_language(text: str) -> str:
    """Idioma mais provavel pelo vocabulario comum; portugues em caso de duvida."""
    words = Counter(w.lower() for w in WORD.findall(text[:SAMPLE_CHARS]))
    if not words:
        return DEFAULT
    scores = {lang: sum(words[w] for w in vocabulary) for lang, vocabulary in STOPWORDS.items()}
    # Acentos e letras proprias desempatam portugues e espanhol.
    scores["pt"] += 3 * sum(words[w] for w in words if re.search(r"[ãõç]", w))
    scores["es"] += 3 * sum(words[w] for w in words if "ñ" in w)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else DEFAULT


def language_of_voice(voice_language: str) -> str:
    """'pt_BR' -> 'pt', 'en_GB' -> 'en'."""
    return (voice_language or DEFAULT)[:2].lower()


# Frases que o proprio DocAudio fala, em cada idioma.
PHRASES = {
    "skipped_code": {
        "pt": "Exemplo de código omitido na narração.",
        "en": "Code example omitted from narration.",
        "es": "Ejemplo de código omitido en la narración.",
    },
    "answer": {
        "pt": "Resposta da questão {number}: letra {letter}.",
        "en": "Answer to question {number}: letter {letter}.",
        "es": "Respuesta de la pregunta {number}: letra {letter}.",
    },
    "sample": {
        "pt": "Olá! Esta é uma amostra da voz escolhida para narrar seus documentos.",
        "en": "Hello! This is a sample of the voice chosen to narrate your documents.",
        "es": "¡Hola! Esta es una muestra de la voz elegida para narrar sus documentos.",
    },
}


def phrase(key: str, language: str, **values) -> str:
    texts = PHRASES[key]
    return texts.get(language, texts[DEFAULT]).format(**values)
