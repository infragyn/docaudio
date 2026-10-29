"""Normalizacao do texto para narracao em pt-BR.

Ordem: enderecos web -> dicionario -> linhas em caixa alta -> abreviacoes ->
valores, porcentagens, datas, horas, ordinais, unidades, algarismos romanos ->
numeros -> siglas -> simbolos.
"""

import re

from docaudio.text_processing.numbers import cardinal, decimal, ordinal, roman_to_int
from docaudio.text_processing.pronunciation import PronunciationDictionary

UPPER = "A-ZÁÉÍÓÚÂÊÔÃÕÇÀÜ"
VOWELS = set("AEIOUÁÉÍÓÚÂÊÔÃÕÀÜ")
LETTER_NAMES = {
    "A": "á", "B": "bê", "C": "cê", "D": "dê", "E": "é", "F": "éfe", "G": "gê", "H": "agá",
    "I": "í", "J": "jota", "K": "cá", "L": "éle", "M": "ême", "N": "êne", "O": "ó", "P": "pê",
    "Q": "quê", "R": "érre", "S": "ésse", "T": "tê", "U": "u", "V": "vê", "W": "dáblio",
    "X": "xis", "Y": "ípsilon", "Z": "zê", "Ç": "cê cedilha",
}
CONSONANT_CLUSTERS = {
    "BR", "CR", "DR", "FR", "GR", "PR", "TR", "VR", "BL", "CL", "FL", "GL", "PL", "CH", "LH", "NH",
}
MONTHS = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]

# Abreviacao -> extenso. Entradas com "." final mantem o ponto se encerrarem a frase.
ABBREVIATIONS = {
    "art.": "artigo", "arts.": "artigos", "inc.": "inciso", "al.": "alínea",
    "nº": "número", "n.º": "número",
    "pág.": "página", "págs.": "páginas", "pp.": "páginas",
    "cap.": "capítulo", "caps.": "capítulos", "fig.": "figura", "figs.": "figuras",
    "tab.": "tabela", "vol.": "volume", "vols.": "volumes", "ed.": "edição", "séc.": "século",
    "Dr.": "doutor", "Dra.": "doutora", "Drs.": "doutores", "Sr.": "senhor", "Sra.": "senhora",
    "Srs.": "senhores", "Srta.": "senhorita", "Prof.": "professor", "Profa.": "professora",
    "Profª": "professora", "Eng.": "engenheiro", "Av.": "avenida", "tel.": "telefone",
    "ex.:": "por exemplo:", "ex.": "exemplo", "obs.:": "observação:", "obs.": "observação",
    "etc.": "etcétera", "aprox.": "aproximadamente", "máx.": "máximo", "mín.": "mínimo",
    "p.ex.": "por exemplo", "i.e.": "isto é", "e.g.": "por exemplo", "cf.": "conforme",
    "op. cit.": "obra citada", "et al.": "e colaboradores", "Ltda.": "limitada", "S.A.": "sociedade anônima",
}
SENTENCE_FINAL_ABBREVIATIONS = {"etc.", "ed.", "aprox.", "Ltda.", "S.A.", "et al.", "op. cit."}
UNITS = {
    "km/h": ("quilômetro por hora", "quilômetros por hora"),
    "km²": ("quilômetro quadrado", "quilômetros quadrados"),
    "m²": ("metro quadrado", "metros quadrados"),
    "m³": ("metro cúbico", "metros cúbicos"),
    "km": ("quilômetro", "quilômetros"), "cm": ("centímetro", "centímetros"),
    "mm": ("milímetro", "milímetros"), "m": ("metro", "metros"),
    "kg": ("quilo", "quilos"), "mg": ("miligrama", "miligramas"), "g": ("grama", "gramas"),
    "ml": ("mililitro", "mililitros"), "l": ("litro", "litros"), "L": ("litro", "litros"),
    "°C": ("grau Celsius", "graus Celsius"), "ºC": ("grau Celsius", "graus Celsius"),
    "°": ("grau", "graus"), "min": ("minuto", "minutos"), "s": ("segundo", "segundos"),
    "TB": ("terabyte", "terabytes"), "GB": ("gigabyte", "gigabytes"),
    "MB": ("megabyte", "megabytes"), "KB": ("quilobyte", "quilobytes"), "kB": ("quilobyte", "quilobytes"),
    "GHz": ("giga-hertz", "giga-hertz"), "MHz": ("mega-hertz", "mega-hertz"),
    "kHz": ("quilo-hertz", "quilo-hertz"), "Hz": ("hertz", "hertz"),
    "W": ("watt", "watts"), "kW": ("quilowatt", "quilowatts"), "V": ("volt", "volts"),
}
CURRENCIES = {"R$": ("real", "reais"), "US$": ("dólar", "dólares"), "U$": ("dólar", "dólares"), "€": ("euro", "euros"), "£": ("libra", "libras")}
ROMAN_CONTEXT = r"século|séculos|capítulo|parte|volume|título|livro|unidade|módulo|seção|anexo|tomo|fase|nível|inciso|lei"
REGNAL_NAMES = r"Pedro|João|Luís|Henrique|Elizabeth|Elizabete|Carlos|Bento|Francisco|Pio|Leão|Gregório|Filipe|Felipe|Jorge|Eduardo|Ricardo|Guilherme|Napoleão|Paulo|Manuel"

NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# CPF, CNPJ, CEP e telefones sao lidos digito a digito.
_DIGIT_CODES = re.compile(
    r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"
    r"|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"
    r"|\b\d{5}-\d{3}\b"
    r"|\(?\b\d{2}\)?\s?\d{4,5}-\d{4}\b"
)
_MONEY = re.compile(
    r"(R\$|US\$|U\$|€|£)\s?(" + NUMBER + r")(?:\s(mil|milhão|milhões|bilhão|bilhões|trilhão|trilhões)\b)?"
)
_PERCENT = re.compile(r"(" + NUMBER + r")\s?%")
_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_TIME_H = re.compile(r"\b(\d{1,2})h(\d{2})?(?:min)?\b")
_TIME_COLON = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
# "1° semestre" e ordinal (uso comum, embora incorreto); "30° de inclinação" e grau.
_ORDINAL = re.compile(r"\b(\d+)\s?([ºª]|°(?=\s+(?!(?:de|e|a|C|F)\b)[a-záéíóú]))")
_UNIT = re.compile(
    r"(?<![\w,.])(" + NUMBER + r")\s?("
    + "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
    + r")(?![\w²³])"
)
_ROMAN_CONTEXT = re.compile(rf"\b({ROMAN_CONTEXT})\s+([IVXLCDM]+)\b", re.IGNORECASE)
_REGNAL = re.compile(rf"\b({REGNAL_NAMES})\s+([IVXLCDM]+)\b")
_RANGE = re.compile(r"\b(\d+)\s?[-–]\s?(\d+)\b")
# Numeracao de secoes (1.2.3) e versoes (3.11), sem confundir com milhares (1.234).
_SECTION = re.compile(r"\b(?!\d{1,3}(?:\.\d{3})+\b)\d+(?:\.\d+)+\b(?!,\d)")
_NUMBER = re.compile(r"(?<![\w])-?(" + NUMBER + r")(?![\w])")
_ACRONYM = re.compile(rf"(?<![\w-])([{UPPER}]{{2,7}})(s?)(?![\w-])")
_SPACES = re.compile(r"[ \t]+")


def _number_words(token: str, feminine: bool = False) -> str:
    token = token.replace(".", "")
    if "," in token:
        integer, fraction = token.split(",", 1)
        return decimal(integer, fraction, feminine)
    if len(token) > 6 and not token.startswith("0"):
        return cardinal(int(token), feminine) if len(token) <= 9 else " ".join(cardinal(int(d)) for d in token)
    if token.startswith("0") and len(token) > 1:
        return " ".join(cardinal(int(d)) for d in token)
    return cardinal(int(token), feminine)


def _is_one(token: str) -> bool:
    return token.replace(".", "") in ("1", "1,0", "1,00")


def spell(acronym: str) -> str:
    return " ".join(LETTER_NAMES.get(c, c.lower()) for c in acronym.upper())


def is_pronounceable(word: str) -> bool:
    """Heuristica para siglas lidas como palavra (ONU, NASA) ou soletradas (IBGE, PIB)."""
    word = word.upper()
    if len(word) <= 2 or not any(c in VOWELS for c in word):
        return False
    if word[-1] not in VOWELS and word[-1] not in "SRLMNZ":
        return False
    for match in re.finditer(r"[^AEIOUÁÉÍÓÚÂÊÔÃÕÀÜ]+", word):
        run = match.group()
        if len(run) >= 3:
            return False
        if len(run) == 2 and run not in CONSONANT_CLUSTERS:
            # Encontros como "NS", "SC", "RT" so sao naturais no meio da palavra.
            if match.start() == 0 or match.end() == len(word) or run[0] not in "SRLMN":
                return False
    return True


def _lower_caps_lines(text: str) -> str:
    """Linhas inteiras em caixa alta (titulos) sao lidas como texto comum, nao como siglas."""
    lines = []
    for line in text.split("\n"):
        # Sem nenhuma minuscula: "O IBGE e o PIB" tem minusculas e mantem as siglas.
        words = re.findall(r"[^\W\d_]{2,}", line)
        if len(words) >= 2 and not any(c.islower() for c in line) and len(" ".join(words)) > 12:
            line = line.lower()
        lines.append(line)
    return "\n".join(lines)


CAPS_STOPWORDS = {
    "A", "O", "AS", "OS", "E", "DE", "DA", "DO", "DAS", "DOS", "EM", "NO", "NA", "NOS", "NAS", "AO", "AOS",
    "PARA", "POR", "PELO", "PELA", "PELOS", "PELAS", "COM", "QUE", "SE", "OU", "UM", "UMA", "SOBRE", "ENTRE",
}
_CAPS_RUN = re.compile(rf"(?<![\w-])[{UPPER}]+(?:[ \t/–—-]+[{UPPER}]+)+(?![\w-])")


def _lower_caps_runs(text: str) -> str:
    """Trechos em caixa alta dentro de frases ("DAS INSCRIÇÕES PARA CANDIDATOS") viram texto comum.

    Sequencias so de siglas ("TCP IP", "HTML CSS") continuam como siglas.
    """

    def replace(match: re.Match) -> str:
        words = re.findall(rf"[{UPPER}]+", match.group())
        has_stopword = any(w in CAPS_STOPWORDS for w in words)
        # Palavra comum: 5+ letras e 2+ vogais (CONTROLE, EXTERNO); siglas raramente sao assim.
        long_words = sum(len(w) >= 5 and sum(c in VOWELS for c in w) >= 2 for w in words)
        # "DO CARGO" vira texto; "O IBGE" (artigo + sigla) continua com a sigla.
        is_text = long_words >= 2 or (has_stopword and long_words >= 1)
        return match.group().lower() if is_text else match.group()

    return _CAPS_RUN.sub(replace, text)


def _speak_url(match: re.Match) -> str:
    raw = match.group()
    # Pontuacao colada no fim pertence a frase, nao ao endereco.
    trailing = raw[len(raw.rstrip(".,;:!?")):]
    url = re.sub(r"^(https?://)?(www\.)?", "", raw[: len(raw) - len(trailing)], flags=re.IGNORECASE)
    domain = re.split(r"[/?#]", url, maxsplit=1)[0]
    return " ponto ".join(part for part in domain.split(".") if part) + trailing


def _speak_email(match: re.Match) -> str:
    user, domain = match.group().split("@", 1)
    return f"{user.replace('.', ' ponto ')} arroba {' ponto '.join(domain.split('.'))}"


def _abbreviations(text: str) -> str:
    for abbreviation in sorted(ABBREVIATIONS, key=len, reverse=True):
        spoken = ABBREVIATIONS[abbreviation]
        pattern = re.compile(rf"(?<![\w.]){re.escape(abbreviation)}(?=(\s+\S+)?)", re.IGNORECASE)

        def replace(match: re.Match, spoken=spoken, abbreviation=abbreviation) -> str:
            following = match.group(1)
            next_word = following.split()[0] if following else ""
            ends_sentence = not next_word or (next_word[0].isupper() and not next_word.isupper())
            # "etc." no fim da frase mantem o ponto; "cap. IV" ou "Dr. Silva" nao.
            keep_period = abbreviation in SENTENCE_FINAL_ABBREVIATIONS and ends_sentence
            return spoken + ("." if keep_period else "")

        text = pattern.sub(replace, text)
    # "p. 15" -> "página 15" (so antes de numero, para nao confundir com iniciais).
    return re.sub(r"(?<![\w.])p\.\s?(?=\d)", "página ", text)


def _money(match: re.Match) -> str:
    symbol, amount, scale = match.groups()
    singular, plural = CURRENCIES[symbol]
    if scale:
        return f"{_number_words(amount)} {scale} de {plural}"
    integer, _, cents = amount.replace(".", "").partition(",")
    value = int(integer)
    words = cardinal(value)
    if value == 0 and cents:
        text = ""
    elif words.endswith(("milhão", "milhões", "bilhão", "bilhões", "trilhão", "trilhões")):
        text = f"{words} de {plural}"
    else:
        text = f"{words} {singular if value == 1 else plural}"
    if cents and int(cents.ljust(2, "0")[:2]):
        cents_value = int(cents.ljust(2, "0")[:2])
        cents_text = f"{cardinal(cents_value)} {'centavo' if cents_value == 1 else 'centavos'}"
        text = f"{text} e {cents_text}" if text else cents_text
    return text


def _date(match: re.Match) -> str:
    day, month, year = (int(g) for g in match.groups())
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return match.group()
    day_text = "primeiro" if day == 1 else cardinal(day)
    return f"{day_text} de {MONTHS[month - 1]} de {cardinal(year)}"


def _time(hours: int, minutes: int | None) -> str:
    text = f"{cardinal(hours, feminine=True)} {'hora' if hours == 1 else 'horas'}"
    if minutes:
        text += f" e {cardinal(minutes)} {'minuto' if minutes == 1 else 'minutos'}"
    return text


def _time_h(match: re.Match) -> str:
    hours = int(match.group(1))
    minutes = int(match.group(2)) if match.group(2) else None
    if hours > 24 or (minutes is not None and minutes > 59):
        return match.group()
    return _time(hours, minutes)


def _unit(match: re.Match) -> str:
    amount, unit = match.groups()
    singular, plural = UNITS[unit]
    return f"{_number_words(amount)} {singular if _is_one(amount) else plural}"


def _roman_context(match: re.Match) -> str:
    value = roman_to_int(match.group(2))
    return f"{match.group(1)} {cardinal(value)}" if value else match.group()


def _regnal(match: re.Match) -> str:
    value = roman_to_int(match.group(2))
    return f"{match.group(1)} {ordinal(value)}" if value else match.group()


def _section(match: re.Match) -> str:
    return " ponto ".join(cardinal(int(part)) for part in match.group().split("."))


def _number(match: re.Match) -> str:
    sign = "menos " if match.group().startswith("-") else ""
    return sign + _number_words(match.group(1))


def _acronym(match: re.Match) -> str:
    word, plural = match.groups()
    if is_pronounceable(word):
        return match.group()
    spelled = spell(word)
    return spelled + ("s" if plural else "")


# Alternativas de questao no inicio da linha: "(A) texto", "A) texto".
_ALTERNATIVE = re.compile(r"(?m)^\s*(?:\(([A-Ea-e])\)|([A-E])\))\s+")
# Notacao assintotica: O(n), O(log n), O(n²), O(2ⁿ), Θ(n), Ω(n).
_BIG_O = re.compile(r"(?<![\w])([OΘΩ])\(([^()]{1,20})\)")
BIG_O_NAMES = {"O": "ó", "Θ": "téta", "Ω": "ômega"}
_SUPERSCRIPTS = {"²": " ao quadrado", "³": " ao cubo", "ⁿ": " elevado a n"}
# Letra isolada seguida de pontuacao ("resposta C.", "item B;"): nome da letra.
# Inclui A, E e O, que fora desse contexto seriam artigo ou conjuncao.
_ANSWER_LETTER = re.compile(r"(?i)\b(resposta|letra|alternativa|gabarito|item)(:?\s+)(?-i:([A-E]))\b")
_LONE_LETTER = re.compile(r"(?<![\w.'-])([B-DF-NP-Z])(?=\s*[;,.:)]|\s*$)")


def _big_o(match: re.Match) -> str:
    expression = match.group(2).strip()
    for symbol, words in _SUPERSCRIPTS.items():
        expression = expression.replace(symbol, words)
    expression = re.sub(r"\blog\s*", "log de ", expression)
    expression = expression.replace("!", " fatorial").replace("*", " vezes ")
    return f"{BIG_O_NAMES[match.group(1)]} de {expression}"


def _alternative(match: re.Match) -> str:
    letter = (match.group(1) or match.group(2)).upper()
    return f"Alternativa {LETTER_NAMES[letter]}: "


def normalize(text: str, dictionary: PronunciationDictionary | None = None) -> str:
    text = _BIG_O.sub(_big_o, text)
    text = _ALTERNATIVE.sub(_alternative, text)
    text = re.sub(r"(\w)²", r"\1 ao quadrado", text)
    text = re.sub(r"(\w)³", r"\1 ao cubo", text)
    text = _URL.sub(_speak_url, text)
    text = _EMAIL.sub(_speak_email, text)
    if dictionary:
        text = dictionary.apply(text)
    text = _lower_caps_lines(text)
    text = _lower_caps_runs(text)
    text = re.sub(r"\be/ou\b", "e ou", text)
    text = _abbreviations(text)
    text = _DIGIT_CODES.sub(lambda m: " ".join(cardinal(int(d)) for d in re.sub(r"\D", "", m.group())), text)
    text = _MONEY.sub(_money, text)
    text = _PERCENT.sub(lambda m: f"{_number_words(m.group(1))} por cento", text)
    text = _DATE.sub(_date, text)
    text = _TIME_H.sub(_time_h, text)
    text = _TIME_COLON.sub(lambda m: _time(int(m.group(1)), int(m.group(2))), text)
    text = _ORDINAL.sub(lambda m: ordinal(int(m.group(1)), feminine=m.group(2) == "ª"), text)
    text = _UNIT.sub(_unit, text)
    text = _ROMAN_CONTEXT.sub(_roman_context, text)
    text = _REGNAL.sub(_regnal, text)
    text = _RANGE.sub(lambda m: f"{m.group(1)} a {m.group(2)}", text)
    text = _SECTION.sub(_section, text)
    text = _NUMBER.sub(_number, text)
    # Siglas compostas com hifen ("CI-CD"): cada parte e lida separadamente.
    text = re.sub(rf"(?<![\w-])([{UPPER}]{{2,7}})-([{UPPER}]{{2,7}})(?![\w-])", r"\1 \2", text)
    text = _ACRONYM.sub(_acronym, text)
    text = _ANSWER_LETTER.sub(lambda m: m.group(1) + m.group(2) + LETTER_NAMES[m.group(3)], text)
    text = _LONE_LETTER.sub(lambda m: LETTER_NAMES[m.group(1)], text)
    text = _symbols(text)
    return "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n")).strip()


def _symbols(text: str) -> str:
    replacements = [
        (r"§§", " parágrafos "), (r"§", " parágrafo "), (r"\s&\s", " e "), (r"\s\+\s", " mais "),
        (r"\s>=\s", " maior ou igual a "), (r"\s<=\s", " menor ou igual a "), (r"\s!=\s", " diferente de "),
        (r"\s>\s", " maior que "), (r"\s<\s", " menor que "), (r"\(\s*\*?\s*\)", ""),
        (r"\s=\s", " igual a "), (r"%", " por cento"), (r"\s[–—]\s", ", "), (r"[|→⇒►•▪◦]", ", "),
        (r"[ºª]", ""), (r"[*_#~^`]", " "), (r"\.{3,}|…", "..."), (r"([!?]){2,}", r"\1"),
        (r"\s+([,.;:!?])", r"\1"), (r",\s*,", ","),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text
