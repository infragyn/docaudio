"""Gera o corpus sintetico de teste e as referencias esperadas.

Cobre os casos dificeis do plano enquanto o corpus de documentos reais
(tests/corpus/reais) nao estiver completo. Para inspecionar os arquivos:

    python tests/corpus/sinteticos.py pasta_destino
"""

import json
import sys
from pathlib import Path

import docx
import pymupdf
from docx.shared import Pt

A4 = (595, 842)
MARGIN = 60


# ---- DOCX -------------------------------------------------------------------


def docx_com_estilos(folder: Path) -> dict:
    doc = docx.Document()
    doc.add_paragraph("Apostila de Redes", style="Title")
    doc.add_heading("Sumário", level=1)
    doc.add_paragraph("Introdução ........................ 3")
    doc.add_paragraph("Protocolos ........................ 5")
    doc.add_heading("Introdução", level=1)
    doc.add_paragraph("Redes de computadores conectam dispositivos para troca de dados.")
    doc.add_paragraph("https://www.exemplo.com.br/redes")
    doc.add_heading("Histórico", level=2)
    doc.add_paragraph("A ARPANET surgiu em 1969 como projeto de pesquisa.")
    doc.add_heading("Protocolos", level=1)
    doc.add_paragraph("Os principais protocolos são:")
    for item in ("TCP, orientado a conexão.", "UDP, sem conexão.", "IP, responsável pelo endereçamento."):
        doc.add_paragraph(item, style="List Bullet")
    table = doc.add_table(rows=3, cols=2)
    for row, values in zip(table.rows, [("Protocolo", "Camada"), ("TCP", "Transporte"), ("IP", "Rede")]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    doc.add_heading("Referências", level=1)
    doc.add_paragraph("TANENBAUM, Andrew. Redes de Computadores. 5. ed. 2011.")
    path = folder / "docx_com_estilos.docx"
    doc.save(path)
    return {
        "arquivo": path.name,
        "titulo": "Apostila de Redes",
        "capitulos": ["Sumário", "Introdução", "Protocolos", "Referências"],
        "contem": ["ARPANET surgiu em 1969", "TCP, orientado a conexão.", "Histórico"],
        "nao_contem": ["........", "https://", "TANENBAUM"],
    }


def _styled_paragraph(doc, text: str, size: float, bold: bool = False) -> None:
    run = doc.add_paragraph().add_run(text)
    run.font.size = Pt(size)
    run.bold = bold


def docx_sem_estilos(folder: Path) -> dict:
    doc = docx.Document()
    _styled_paragraph(doc, "Material produzido para estudo individual, sem estilos de título.", 11)
    _styled_paragraph(doc, "CAPÍTULO 1 – Fundamentos", 16, bold=True)
    _styled_paragraph(doc, "A lógica de programação organiza o raciocínio em passos.", 11)
    _styled_paragraph(doc, "Conceitos iniciais", 11, bold=True)
    _styled_paragraph(doc, "Um algoritmo é uma sequência finita de instruções.", 11)
    _styled_paragraph(doc, "CAPÍTULO 2 – Prática", 16, bold=True)
    _styled_paragraph(doc, "Os exercícios a seguir consolidam o conteúdo.", 11)
    path = folder / "docx_sem_estilos.docx"
    doc.save(path)
    return {
        "arquivo": path.name,
        "capitulos": ["Início", "CAPÍTULO 1 – Fundamentos", "CAPÍTULO 2 – Prática"],
        "contem": ["Conceitos iniciais", "sequência finita de instruções"],
        "nao_contem": [],
    }


# ---- PDF --------------------------------------------------------------------


def _wrap(text: str, width: float, fontname: str, size: float) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and pymupdf.get_text_length(candidate, fontname=fontname, fontsize=size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class PdfWriter:
    """Escreve paragrafos em sequencia, com quebra de pagina automatica."""

    def __init__(self, header: str | None = None, footer: str | None = None) -> None:
        self.doc = pymupdf.open()
        self.header = header
        self.footer = footer
        self.page = None
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=A4[0], height=A4[1])
        self.y = MARGIN + 30
        number = self.doc.page_count
        if self.header:
            self.page.insert_text((MARGIN, 40), self.header, fontsize=9, fontname="helv")
        if self.footer:
            self.page.insert_text((A4[0] / 2 - 30, A4[1] - 30), self.footer.format(n=number), fontsize=9, fontname="helv")

    def lines(self, lines: list[str], size: float = 11, bold: bool = False, x: float = MARGIN, gap: float = 8) -> None:
        fontname = "hebo" if bold else "helv"
        height = size * 1.25
        if self.y + height * len(lines) > A4[1] - MARGIN - 40:
            self.new_page()
        for line in lines:
            self.y += height
            self.page.insert_text((x, self.y), line, fontsize=size, fontname=fontname)
        self.y += gap

    def paragraph(self, text: str, size: float = 11, bold: bool = False, width: float = A4[0] - 2 * MARGIN) -> None:
        self.lines(_wrap(text, width, "hebo" if bold else "helv", size), size, bold)

    def save(self, path: Path) -> None:
        self.doc.save(path)
        self.doc.close()


LOREM = (
    "Este parágrafo descreve o conteúdo do curso com frases completas para ocupar várias linhas "
    "e permitir verificar a junção correta das linhas de um mesmo parágrafo durante a leitura."
)


def pdf_apostila(folder: Path) -> dict:
    pdf = PdfWriter(header="Apostila de Python — Curso Básico", footer="Página {n} de 3")
    pdf.paragraph("Apostila de Python", size=24, bold=True)
    pdf.paragraph("1 Introdução", size=18, bold=True)
    pdf.lines(["Python é uma linguagem de progra-", "mação de alto nível, criada por Guido", "van Rossum."])
    pdf.paragraph(LOREM)
    pdf.paragraph("1.1 Instalação", size=14, bold=True)
    pdf.paragraph(LOREM)
    pdf.page.insert_text((MARGIN, A4[1] - 75), "1 Disponível no site oficial da linguagem.", fontsize=8, fontname="helv")
    pdf.new_page()
    pdf.paragraph("2 Variáveis", size=18, bold=True)
    pdf.paragraph("Variáveis armazenam valores que podem mudar durante a execução.")
    pdf.paragraph(LOREM)
    pdf.new_page()
    pdf.paragraph("3 Funções", size=18, bold=True)
    pdf.paragraph("Funções agrupam instruções reutilizáveis.")
    path = folder / "pdf_apostila.pdf"
    pdf.save(path)
    return {
        "arquivo": path.name,
        "capitulos": ["1 Introdução", "2 Variáveis", "3 Funções"],
        "contem": ["linguagem de programação de alto nível", "1.1 Instalação", "reutilizáveis"],
        "nao_contem": ["Curso Básico", "Página", "progra-", "site oficial"],
    }


def pdf_duas_colunas(folder: Path) -> dict:
    pdf = PdfWriter()
    pdf.paragraph("Estudo em colunas", size=18, bold=True)
    top = pdf.y
    column_width = (A4[0] - 2 * MARGIN - 20) / 2
    left = ["Esquerda primeiro: " + LOREM, "Esquerda segundo: " + LOREM]
    right = ["Direita primeiro: " + LOREM, "Direita segundo: " + LOREM]
    for x, texts in ((MARGIN, left), (MARGIN + column_width + 20, right)):
        pdf.y = top
        for text in texts:
            pdf.lines(_wrap(text, column_width, "helv", 11), x=x, gap=10)
    pdf.y += 10
    pdf.paragraph("Conclusão", size=14, bold=True)
    pdf.paragraph("Texto final em largura total depois das colunas.")
    path = folder / "pdf_duas_colunas.pdf"
    pdf.save(path)
    return {
        "arquivo": path.name,
        "capitulos": ["Estudo em colunas", "Conclusão"],
        "ordem": ["Esquerda primeiro", "Esquerda segundo", "Direita primeiro", "Direita segundo", "Texto final"],
        "contem": [],
        "nao_contem": [],
    }


def pdf_com_sumario(folder: Path) -> dict:
    pdf = PdfWriter()
    pdf.paragraph("Capítulo Um")
    pdf.paragraph("Conteúdo do primeiro capítulo, escrito no mesmo tamanho do corpo do texto.")
    pdf.new_page()
    pdf.paragraph("Capítulo Dois")
    pdf.paragraph("Conteúdo do segundo capítulo.")
    pdf.paragraph("Seção 2.1")
    pdf.paragraph("Detalhes da seção.")
    pdf.doc.set_toc([[1, "Capítulo Um", 1], [1, "Capítulo Dois", 2], [2, "Seção 2.1", 2]])
    path = folder / "pdf_com_sumario.pdf"
    pdf.save(path)
    return {
        "arquivo": path.name,
        "capitulos": ["Capítulo Um", "Capítulo Dois"],
        "contem": ["Seção 2.1", "Detalhes da seção."],
        "nao_contem": [],
    }


def pdf_digitalizado(folder: Path) -> dict:
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(MARGIN, MARGIN, 400, 300), pixmap=pix)
    path = folder / "pdf_digitalizado.pdf"
    doc.save(path)
    doc.close()
    return {"arquivo": path.name, "capitulos": [], "ocr": True, "contem": [], "nao_contem": []}


def pdf_digitalizado_texto(folder: Path) -> dict:
    """Apostila de duas paginas convertida em imagem (como um scanner faria)."""
    source = PdfWriter(header="Apostila de Química — Digitalizada", footer="Página {n} de 2")
    source.paragraph("1 Introdução à Química", size=18, bold=True)
    source.paragraph(
        "A química estuda a composição, a estrutura e as propriedades da matéria, "
        "além das transformações que ela sofre."
    )
    source.paragraph("1.1 Átomos", size=14, bold=True)
    source.paragraph("Os átomos são formados por prótons, nêutrons e elétrons.")
    source.new_page()
    source.paragraph("2 Ligações Químicas", size=18, bold=True)
    source.paragraph("As ligações iônicas ocorrem pela transferência de elétrons entre os átomos.")
    scan = pymupdf.open()
    for page in source.doc:
        pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        scan.new_page(width=A4[0], height=A4[1]).insert_image(pymupdf.Rect(0, 0, *A4), pixmap=pix)
    source.doc.close()
    path = folder / "pdf_digitalizado_texto.pdf"
    scan.save(path)
    scan.close()
    return {
        "arquivo": path.name,
        "capitulos": ["1 Introdução à Química", "2 Ligações Químicas"],
        "contem": ["composição, a estrutura e as propriedades da matéria", "1.1 Átomos", "transferência de elétrons"],
        "nao_contem": ["Digitalizada", "Página"],
        "ocr": True,
        "requer_ocr": True,
    }


GENERATORS = [
    docx_com_estilos,
    docx_sem_estilos,
    pdf_apostila,
    pdf_duas_colunas,
    pdf_com_sumario,
    pdf_digitalizado,
    pdf_digitalizado_texto,
]


def generate(folder: Path) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    return [generator(folder) for generator in GENERATORS]


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus_sintetico")
    references = generate(target)
    (target / "esperado.json").write_text(json.dumps(references, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(references)} documentos gerados em {target}")
