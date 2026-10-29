import pytest

from docaudio.text_processing.normalize import is_pronounceable, normalize, spell
from docaudio.text_processing.numbers import cardinal, decimal, ordinal, roman_to_int
from docaudio.text_processing.pronunciation import Entry, PronunciationDictionary, read_csv


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "zero"), (16, "dezesseis"), (21, "vinte e um"), (100, "cem"), (101, "cento e um"),
        (999, "novecentos e noventa e nove"), (1000, "mil"), (1001, "mil e um"), (1100, "mil e cem"),
        (1234, "mil duzentos e trinta e quatro"), (2024, "dois mil e vinte e quatro"),
        (1_000_000, "um milhão"), (1_500_000, "um milhão e quinhentos mil"),
        (2_000_001, "dois milhões e um"), (3_200_000_000, "três bilhões e duzentos milhões"),
    ],
)
def test_cardinal(n, expected):
    assert cardinal(n) == expected


def test_feminine_and_ordinal():
    assert cardinal(2, feminine=True) == "duas"
    assert cardinal(201, feminine=True) == "duzentas e uma"
    assert ordinal(1) == "primeiro"
    assert ordinal(22, feminine=True) == "vigésima segunda"
    assert ordinal(100) == "centésimo"
    assert decimal("3", "05") == "três vírgula zero cinco"


def test_roman():
    assert roman_to_int("XXI") == 21
    assert roman_to_int("IV") == 4
    assert roman_to_int("IIII") is None
    assert roman_to_int("ABC") is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("R$ 1.234,56", "mil duzentos e trinta e quatro reais e cinquenta e seis centavos"),
        ("R$ 1,00", "um real"),
        ("R$ 0,50", "cinquenta centavos"),
        ("R$ 2,5 milhões", "dois vírgula cinco milhões de reais"),
        ("US$ 1.000.000", "um milhão de dólares"),
        ("2,5%", "dois vírgula cinco por cento"),
        ("12/03/2024", "doze de março de dois mil e vinte e quatro"),
        ("01/01/2000", "primeiro de janeiro de dois mil"),
        ("14h30", "quatorze horas e trinta minutos"),
        ("1h", "uma hora"),
        ("às 18:45", "às dezoito horas e quarenta e cinco minutos"),
        ("1º lugar", "primeiro lugar"),
        ("3ª edição", "terceira edição"),
        ("1° semestre", "primeiro semestre"),
        ("30° de inclinação", "trinta graus de inclinação"),
        ("25 °C", "vinte e cinco graus Celsius"),
        ("5 km", "cinco quilômetros"),
        ("1 kg", "um quilo"),
        ("século XXI", "século vinte e um"),
        ("Dom Pedro II", "Dom Pedro segundo"),
        ("1.1 Introdução", "um ponto um Introdução"),
        ("1.500 páginas", "mil e quinhentos páginas"),
        ("páginas 10-20", "páginas dez a vinte"),
        ("-5 graus", "menos cinco graus"),
        ("CPF 123.456.789-00", "cê pê éfe um dois três quatro cinco seis sete oito nove zero zero"),
    ],
)
def test_normalize_values(text, expected):
    assert normalize(text, PronunciationDictionary([Entry("CPF", "cê pê éfe", True)])) == expected


def test_abbreviations_keep_sentence_end_only_when_needed():
    assert normalize("Veja o cap. IV, a fig. 3 e a pág. 15 etc. Depois, p. 20.") == (
        "Veja o capítulo quatro, a figura três e a página quinze etcétera. Depois, página vinte."
    )
    assert normalize("O Dr. Silva e a Sra. Souza.") == "O doutor Silva e a senhora Souza."
    assert normalize("Conforme o art. 5º, § 2º.") == "Conforme o artigo quinto, parágrafo segundo."


def test_urls_and_emails_are_read_by_domain():
    assert normalize("Veja https://www.site.com.br/a/b?x=1.") == "Veja site ponto com ponto br."
    assert normalize("contato@escola.edu.br") == "contato arroba escola ponto edu ponto br"


def test_acronyms_spelled_or_read():
    assert normalize("O IBGE e o PIB, a ONU e a NASA.") == "O í bê gê é e o pê í bê, a ONU e a NASA."
    assert is_pronounceable("UNESCO") and is_pronounceable("DETRAN")
    assert not is_pronounceable("INSS") and not is_pronounceable("UFRJ") and not is_pronounceable("TI")
    assert spell("TCP") == "tê cê pê"


def test_all_caps_lines_are_read_as_text():
    assert normalize("CAPÍTULO 1 – FUNDAMENTOS DE REDES") == "capítulo um, fundamentos de redes"


def test_dictionary_priority_and_case(tmp_path):
    default = tmp_path / "padrao.csv"
    user = tmp_path / "usuario.csv"
    default.write_text("termo,pronuncia,diferenciar_maiusculas\nUSP,usp,sim\nsite,saite,nao\n", encoding="utf-8")
    user.write_text("termo,pronuncia,diferenciar_maiusculas\nsite,sáite,nao\n# x,y,\n", encoding="utf-8")
    dictionary = PronunciationDictionary.from_csv(default, user)
    assert len(dictionary) == 2
    assert dictionary.apply("O Site da USP e da usp.") == "O sáite da usp e da usp."
    assert read_csv(tmp_path / "nao_existe.csv") == []
