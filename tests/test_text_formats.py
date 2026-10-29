"""Leitura de TXT, HTML, EPUB e PPTX."""

import zipfile

import pytest

from docaudio.core import documents
from docaudio.core.loader import load_document
from docaudio.core.model import BlockType, DocumentKind
from docaudio.extractors.base import ExtractionError
from docaudio.storage.settings import Settings

SETTINGS = Settings(restore_accents=False)


def chapters(path):
    document = load_document(path, SETTINGS)
    return document, [(c.title, [(b.type, b.text) for b in c.blocks]) for c in document.chapters]


def test_formatos_novos_sao_aceitos():
    for name in ("a.epub", "b.PPTX", "c.txt", "d.html", "e.htm", "f.xhtml"):
        assert documents.is_supported(documents.Path(name))
    assert not documents.is_supported(documents.Path("g.odt"))


def test_txt_com_titulos_e_acentos_em_cp1252(tmp_path):
    path = tmp_path / "apostila.txt"
    path.write_bytes(
        "CAPÍTULO 1 INTRODUÇÃO\n\nPrimeiro parágrafo\nque continua na linha seguinte.\n\n"
        "1.1 Conceitos\n\nTexto dos conceitos.\n\nCapítulo 2 Prática\n\nExercícios do capítulo.".encode("cp1252")
    )
    document, result = chapters(path)
    assert document.kind == DocumentKind.TXT
    assert [title for title, _ in result] == ["CAPÍTULO 1 INTRODUÇÃO", "Capítulo 2 Prática"]
    first = result[0][1]
    assert (BlockType.PARAGRAPH, "Primeiro parágrafo que continua na linha seguinte.") in first
    assert (BlockType.HEADING, "1.1 Conceitos") in first


HTML = """<html><head><title>Guia de Redes</title><style>p{color:red}</style></head><body>
<nav>Menu Início Contato</nav>
<h1>Redes</h1><p>O protocolo <b>TCP</b> garante entrega.</p>
<ul><li>Camada de enlace</li><li>Camada de rede</li></ul>
<table><tr><th>Porta</th><th>Serviço</th></tr><tr><td>22</td><td>SSH</td></tr></table>
<pre>ping 8.8.8.8
traceroute host</pre>
<h1>Segurança</h1><p>Use TLS.<br>Sempre.</p><script>alert(1)</script>
</body></html>"""


def test_html_titulos_listas_tabelas_e_codigo(tmp_path):
    path = tmp_path / "guia.html"
    path.write_text(HTML, encoding="utf-8")
    document, result = chapters(path)
    assert document.title == "Guia de Redes"
    assert [title for title, _ in result] == ["Redes", "Segurança"]
    redes = result[0][1]
    assert (BlockType.PARAGRAPH, "O protocolo TCP garante entrega.") in redes
    assert (BlockType.LIST, "Camada de enlace") in redes
    assert (BlockType.TABLE, "Porta; Serviço\n22; SSH") in redes
    assert (BlockType.CODE, "ping 8.8.8.8") in redes
    assert result[1][1] == [(BlockType.PARAGRAPH, "Use TLS. Sempre.")]
    assert not any("Menu" in text or "alert" in text for _, blocks in result for _, text in blocks)


def make_epub(path):
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        book.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<metadata><dc:title>Livro de Teste</dc:title></metadata><manifest>"
            '<item id="c2" href="texto/cap2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c1" href="texto/cap1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="css" href="estilo.css" media-type="text/css"/>'
            '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>',
        )
        book.writestr("OEBPS/texto/cap1.xhtml", "<html><body><h1>Um</h1><p>Primeiro capítulo.</p></body></html>")
        book.writestr("OEBPS/texto/cap2.xhtml", "<html><body><h1>Dois</h1><p>Segundo capítulo.</p></body></html>")


def test_epub_segue_a_ordem_do_spine(tmp_path):
    path = tmp_path / "livro.epub"
    make_epub(path)
    document, result = chapters(path)
    assert document.title == "Livro de Teste"
    assert result == [
        ("Um", [(BlockType.PARAGRAPH, "Primeiro capítulo.")]),
        ("Dois", [(BlockType.PARAGRAPH, "Segundo capítulo.")]),
    ]


def test_epub_invalido_vira_erro_legivel(tmp_path):
    path = tmp_path / "quebrado.epub"
    path.write_bytes(b"nao e zip")
    with pytest.raises(ExtractionError, match="EPUB"):
        load_document(path, SETTINGS)


P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' \
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def shape(text, placeholder=""):
    ph = f'<p:nvSpPr><p:nvPr><p:ph type="{placeholder}"/></p:nvPr></p:nvSpPr>' if placeholder else ""
    paragraphs = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in text)
    return f"<p:sp>{ph}<p:txBody>{paragraphs}</p:txBody></p:sp>"


def make_pptx(path):
    with zipfile.ZipFile(path, "w") as deck:
        deck.writestr(
            "ppt/presentation.xml",
            f'<p:presentation {P}><p:sldIdLst><p:sldId r:id="rId2"/><p:sldId r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        deck.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships {REL}><Relationship Id="rId1" Target="slides/slide2.xml"/>'
            '<Relationship Id="rId2" Target="slides/slide1.xml"/></Relationships>',
        )
        deck.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld {P}><p:cSld><p:spTree>{shape(["Algoritmos"], "title")}'
            f'{shape(["Pilha segue LIFO", "Fila segue FIFO"])}</p:spTree></p:cSld></p:sld>',
        )
        deck.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            f'<Relationships {REL}><Relationship Id="rId9" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        )
        deck.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            f'<p:notes {P}><p:cSld><p:spTree>{shape(["Lembrar do exemplo da pilha de pratos."], "body")}'
            "</p:spTree></p:cSld></p:notes>",
        )
        deck.writestr(
            "ppt/slides/slide2.xml",
            f'<p:sld {P}><p:cSld><p:spTree>{shape(["Redes"], "title")}{shape(["TCP é orientado a conexão"])}'
            "</p:spTree></p:cSld></p:sld>",
        )


def test_pptx_um_capitulo_por_slide_com_notas(tmp_path):
    path = tmp_path / "aula.pptx"
    make_pptx(path)
    document, result = chapters(path)
    assert document.kind == DocumentKind.PPTX
    assert result == [
        ("Algoritmos", [
            (BlockType.LIST, "Pilha segue LIFO"),
            (BlockType.LIST, "Fila segue FIFO"),
            (BlockType.PARAGRAPH, "Lembrar do exemplo da pilha de pratos."),
        ]),
        ("Redes", [(BlockType.LIST, "TCP é orientado a conexão")]),
    ]
