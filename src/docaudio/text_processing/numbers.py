"""Numeros por extenso em portugues brasileiro."""

UNITS = [
    "zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove",
    "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove",
]
TENS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
HUNDREDS = [
    "", "cento", "duzentos", "trezentos", "quatrocentos",
    "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos",
]
# (singular, plural) para cada grupo de tres digitos a partir dos milhares.
SCALES = [("mil", "mil"), ("milhão", "milhões"), ("bilhão", "bilhões"), ("trilhão", "trilhões")]

ORDINAL_UNITS = ["", "primeiro", "segundo", "terceiro", "quarto", "quinto", "sexto", "sétimo", "oitavo", "nono"]
ORDINAL_TENS = [
    "", "décimo", "vigésimo", "trigésimo", "quadragésimo",
    "quinquagésimo", "sexagésimo", "septuagésimo", "octogésimo", "nonagésimo",
]
ORDINAL_HUNDREDS = [
    "", "centésimo", "ducentésimo", "trecentésimo", "quadringentésimo",
    "quingentésimo", "sexcentésimo", "septingentésimo", "octingentésimo", "noningentésimo",
]

MAX_CARDINAL = 10**15 - 1


def _feminine(word: str) -> str:
    if word == "um":
        return "uma"
    if word == "dois":
        return "duas"
    if word.endswith("entos"):
        return word[:-5] + "entas"
    return word


def _below_thousand(n: int, feminine: bool) -> str:
    if n == 100:
        return "cem"
    words = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        words.append(HUNDREDS[hundreds])
    if rest:
        if rest < 20:
            words.append(UNITS[rest])
        else:
            tens, unit = divmod(rest, 10)
            words.append(TENS[tens] + (f" e {UNITS[unit]}" if unit else ""))
    text = " e ".join(words)
    if feminine:
        text = " ".join(_feminine(w) for w in text.split(" "))
    return text


def cardinal(n: int, feminine: bool = False) -> str:
    """1234 -> 'mil duzentos e trinta e quatro'; 2001 -> 'dois mil e um'."""
    if n < 0:
        return "menos " + cardinal(-n, feminine)
    if n < 1000:
        return UNITS[0] if n == 0 else _below_thousand(n, feminine)
    if n > MAX_CARDINAL:
        return " ".join(UNITS[int(d)] for d in str(n))

    groups = []
    while n:
        n, group = divmod(n, 1000)
        groups.append(group)

    parts: list[tuple[int, str]] = []
    for scale, group in reversed(list(enumerate(groups))):
        if not group:
            continue
        if scale == 0:
            parts.append((group, _below_thousand(group, feminine)))
        elif scale == 1:
            # "mil", nunca "um mil"; o genero vale para os milhares (duas mil pessoas).
            prefix = "" if group == 1 else _below_thousand(group, feminine) + " "
            parts.append((group, prefix + "mil"))
        else:
            singular, plural = SCALES[scale - 1]
            # Milhoes e bilhoes sao substantivos masculinos.
            parts.append((group, f"{_below_thousand(group, False)} {singular if group == 1 else plural}"))

    if len(parts) == 1:
        return parts[0][1]
    last_value, last_text = parts[-1]
    head = " ".join(text for _, text in parts[:-1])
    # "E" antes do ultimo grupo quando ele e menor que cem ou centena exata.
    joiner = " e " if last_value < 100 or last_value % 100 == 0 else " "
    return head + joiner + last_text


def ordinal(n: int, feminine: bool = False) -> str:
    """1 -> 'primeiro', 22 -> 'vigésimo segundo', 1000 -> 'milésimo'."""
    if n <= 0 or n > 1000:
        return cardinal(n, feminine)
    if n == 1000:
        words = ["milésimo"]
    else:
        hundreds, rest = divmod(n, 100)
        tens, unit = divmod(rest, 10)
        words = [w for w in (ORDINAL_HUNDREDS[hundreds], ORDINAL_TENS[tens], ORDINAL_UNITS[unit]) if w]
    if feminine:
        words = [w[:-1] + "a" for w in words]
    return " ".join(words)


def decimal(integer_part: str, fraction_part: str, feminine: bool = False) -> str:
    """'3', '14' -> 'três vírgula quatorze'; zeros a esquerda sao lidos um a um."""
    text = cardinal(int(integer_part), feminine) + " vírgula "
    zeros = len(fraction_part) - len(fraction_part.lstrip("0"))
    words = ["zero"] * zeros
    if fraction_part.lstrip("0"):
        words.append(cardinal(int(fraction_part)))
    return text + " ".join(words)


def roman_to_int(roman: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    roman = roman.upper()
    if not roman or any(c not in values for c in roman):
        return None
    total = 0
    for current, following in zip(roman, roman[1:] + " "):
        value = values[current]
        total += -value if following != " " and values[following] > value else value
    # Rejeita formas invalidas como "IIII" ou "VX" comparando com a forma canonica.
    return total if int_to_roman(total) == roman else None


def int_to_roman(n: int) -> str:
    table = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    result = ""
    for value, symbol in table:
        while n >= value:
            result += symbol
            n -= value
    return result
