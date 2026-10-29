"""Restauracao de acentos em textos digitados sem acentuacao.

A voz le o que esta escrito: "Informacao" sai com som de "k". Tres fontes, da
mais segura para a menos:

1. o proprio documento: se "Informação" aparece acentuada em outro ponto,
   "Informacao" vira "Informação";
2. lista de palavras comuns sem ambiguidade (nao, capitulo, codigo...);
3. terminacoes que em portugues sempre levam acento (-cao, -coes, -encia...).

Palavras cuja forma sem acento tambem existe (esta/está, publico/público,
referencia/referência) nunca sao alteradas.
"""

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable

ACCENTED_CHARS = set("áéíóúâêôãõàçÁÉÍÓÚÂÊÔÃÕÀÇ")
WORD = re.compile(r"[A-Za-zÀ-ÿ]+")

# Formas sem acento que tambem sao palavras validas (ou verbos homografos).
AMBIGUOUS = {
    "a", "as", "e", "o", "os", "esta", "estas", "este", "nos", "pais", "por", "pode", "tem", "vem", "contem",
    "publico", "publica", "publicas", "publicos", "pratica", "praticas", "pratico", "secretaria", "secretarias",
    "referencia", "referencias", "influencia", "influencias", "gerencia", "gerencias", "evidencia", "evidencias",
    "diferencia", "potencia", "sequencia", "sequencias", "silencia", "licencia", "agencia", "vivencia",
    "calculo", "calculos", "numero", "fabrica", "duvida", "duvidas", "musica", "critica", "criticas",
    "historia", "analise", "valido", "valida", "liquido", "deposito", "transito", "habito",
    "copia", "copias", "sabia", "seria", "serie", "medico", "medica", "media", "pelo", "pela", "para", "da",
    "de", "do", "se", "la", "ama", "mas", "baba", "camara", "secretario",
    # Verbo x adjetivo: "o processo continua" x "integração contínua".
    "continua", "continuas", "continuo", "continuos", "maquina", "maquinas", "fabricas", "duvidas",
    "valido", "validos", "validas", "cria", "crie", "varia", "varias", "obvia", "previa", "previas",
}

# Palavras comuns em apostilas e editais, sem homografo usual sem acento.
COMMON = {
    "nao": "não", "entao": "então", "sao": "são", "tambem": "também", "apos": "após", "atraves": "através",
    "alem": "além", "voce": "você", "voces": "vocês", "tres": "três", "mes": "mês", "porem": "porém",
    "ninguem": "ninguém", "alguem": "alguém", "proximo": "próximo", "proxima": "próxima",
    "capitulo": "capítulo", "capitulos": "capítulos", "codigo": "código", "codigos": "códigos",
    "pagina": "página", "paginas": "páginas", "nivel": "nível", "niveis": "níveis", "indice": "índice",
    "indices": "índices", "metodo": "método", "metodos": "métodos", "logica": "lógica", "logico": "lógico",
    "basico": "básico", "basica": "básica", "basicos": "básicos", "teorico": "teórico", "teorica": "teórica",
    "tecnico": "técnico", "tecnica": "técnica", "tecnicos": "técnicos", "tecnicas": "técnicas",
    "automatico": "automático", "automatica": "automática", "dinamico": "dinâmico", "dinamica": "dinâmica",
    "estatico": "estático", "estatica": "estática", "area": "área", "areas": "áreas", "conteudo": "conteúdo",
    "conteudos": "conteúdos", "modulo": "módulo", "modulos": "módulos", "versao": "versão",
    "questao": "questão", "questoes": "questões", "memoria": "memória", "memorias": "memórias",
    "historico": "histórico", "periodo": "período", "periodos": "períodos", "proprio": "próprio",
    "propria": "própria", "proprios": "próprios", "proprias": "próprias", "necessario": "necessário",
    "necessaria": "necessária", "obrigatorio": "obrigatório", "obrigatoria": "obrigatória",
    "minimo": "mínimo", "maximo": "máximo", "medio": "médio", "rapido": "rápido",
    "ultimo": "último", "ultima": "última", "unico": "único", "unica": "única", "orgao": "órgão",
    "orgaos": "órgãos", "estao": "estão", "ate": "até", "ja": "já", "so": "só", "ha": "há", "sera": "será",
    "pe": "pé", "fe": "fé", "ingles": "inglês", "frances": "francês",
    "facil": "fácil", "dificil": "difícil", "util": "útil", "uteis": "úteis", "possivel": "possível",
    "disponivel": "disponível", "responsavel": "responsável", "saude": "saúde", "juridico": "jurídico",
    "juridica": "jurídica", "economico": "econômico", "eletronico": "eletrônico", "eletronica": "eletrônica",
    "ola": "olá", "sumario": "sumário", "glossario": "glossário", "exercicio": "exercício", "exercicios": "exercícios",
    "silaba": "sílaba", "silabas": "sílabas", "credito": "crédito", "debito": "débito", "orcamento": "orçamento",
    "servico": "serviço", "servicos": "serviços", "comeco": "começo", "inicio": "início", "termino": "término",
}

# Terminacoes que exigem acento (aplicadas a palavras com 5+ letras).
SUFFIXES = [
    (re.compile(r"coes$"), "ções"), (re.compile(r"cao$"), "ção"),
    (re.compile(r"soes$"), "sões"), (re.compile(r"(?<=[^aeiou])sao$"), "são"),
    (re.compile(r"ssao$"), "ssão"), (re.compile(r"ssoes$"), "ssões"),
    (re.compile(r"encia(s?)$"), r"ência\1"), (re.compile(r"ancia(s?)$"), r"ância\1"),
    (re.compile(r"aveis$"), "áveis"), (re.compile(r"avel$"), "ável"),
    (re.compile(r"iveis$"), "íveis"), (re.compile(r"ivel$"), "ível"),
    (re.compile(r"ologico(s?)$"), r"ológico\1"), (re.compile(r"ologica(s?)$"), r"ológica\1"),
    (re.compile(r"grafico(s?)$"), r"gráfico\1"), (re.compile(r"metrico(s?)$"), r"métrico\1"),
    (re.compile(r"atico(s?)$"), r"ático\1"), (re.compile(r"atica(s?)$"), r"ática\1"),
]
# Terminacoes "-atica" tem verbos homografos raros; excecoes conhecidas:
SUFFIX_EXCEPTIONS = {"pratica", "praticas", "pratico"}


def strip_accents(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word) if not unicodedata.combining(c))


def _has_accent(word: str) -> bool:
    return any(c in ACCENTED_CHARS for c in word)


def _match_case(original: str, fixed: str) -> str:
    if original.isupper() and len(original) > 1:
        return fixed.upper()
    if original[:1].isupper():
        return fixed[:1].upper() + fixed[1:]
    return fixed


def learn_from(texts: Iterable[str]) -> dict[str, str]:
    """Forma acentuada de cada palavra que, no documento, aparece mais com acento do que sem.

    "Informação" (30x) contra "Informacao" (2x) ensina a correcao; "que" (500x)
    contra "quê" (2x) nao, porque a forma sem acento e a usual.
    """
    variants: defaultdict[str, Counter[str]] = defaultdict(Counter)
    plain: Counter[str] = Counter()
    for text in texts:
        for word in WORD.findall(text):
            lower = word.lower()
            if _has_accent(word):
                variants[strip_accents(lower)][lower] += 1
            else:
                plain[lower] += 1
    learned = {}
    for key, counts in variants.items():
        form, count = counts.most_common(1)[0]
        if sum(counts.values()) >= plain[key]:
            learned[key] = form
    return learned


def restore_word(word: str, learned: dict[str, str] | None = None) -> str:
    lower = word.lower()
    if _has_accent(word) or lower in AMBIGUOUS or len(lower) < 2:
        return word
    # Siglas curtas em maiusculas (SO, TI, NO) nao sao palavras a acentuar;
    # so "NAO", "ATE", "SAO"... da lista comum, com 3+ letras, sao corrigidas.
    if word.isupper() and len(word) <= 4 and not (lower in COMMON and len(lower) >= 3):
        return word
    if learned and lower in learned and learned[lower] != lower:
        return _match_case(word, learned[lower])
    if lower in COMMON:
        return _match_case(word, COMMON[lower])
    if len(lower) >= 5 and lower not in SUFFIX_EXCEPTIONS:
        for pattern, replacement in SUFFIXES:
            if pattern.search(lower):
                return _match_case(word, pattern.sub(replacement, lower))
    return word


class AccentRestorer:
    """Restaura acentos usando o vocabulario acentuado do proprio documento."""

    def __init__(self, document_texts: Iterable[str] = ()) -> None:
        self.learned = learn_from(document_texts)
        self.changes: Counter[tuple[str, str]] = Counter()

    def restore(self, text: str) -> str:
        def replace(match: re.Match) -> str:
            word = match.group()
            fixed = restore_word(word, self.learned)
            if fixed != word:
                self.changes[(word, fixed)] += 1
            return fixed

        return WORD.sub(replace, text)

    @property
    def total(self) -> int:
        return sum(self.changes.values())
