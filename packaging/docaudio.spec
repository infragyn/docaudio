# -*- mode: python ; coding: utf-8 -*-
# Gera dist/DocAudio/DocAudio.exe:
#   pyinstaller packaging/docaudio.spec --noconfirm

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

# Piper precisa dos dados do espeak-ng; onnxruntime das suas DLLs.
# Modelos de hebraico/arabe e dicionarios de outros idiomas nao sao usados.
KEEP_DICTS = {"pt_dict", "en_dict", "es_dict"}
datas = [
    (src, dest)
    for src, dest in collect_data_files(
        "piper", excludes=["train/**", "templates/**", "img/**", "hebrew/**", "tashkeel/**"]
    )
    if not (Path(src).name.endswith("_dict") and Path(src).name not in KEEP_DICTS)
]
binaries = collect_dynamic_libs("onnxruntime")
# Kokoro: configuracao do modelo e espeak-ng proprio (biblioteca e dados de idioma).
datas += collect_data_files("kokoro_onnx") + collect_data_files("espeakng_loader")
binaries += collect_dynamic_libs("espeakng_loader")
# Traducao offline: DLLs do CTranslate2 (os modelos ficam em dados/traducao, baixados sob demanda).
binaries += collect_dynamic_libs("ctranslate2")
# Aba Player: decodificadores do PyAV (ficam em av.libs, ao lado do pacote).
binaries += collect_dynamic_libs("av")

# Voz padrao, binarios (ffmpeg.exe), dicionarios e dados de OCR vao junto do executavel.
for sub in ("voices", "bin", "pronunciation", "tessdata", "tessdata_best"):
    # Inclui subpastas (ex.: pronunciation/temas) mantendo a mesma estrutura.
    for file in sorted((ROOT / "resources" / sub).rglob("*")):
        if file.is_file() and file.name != ".gitkeep":
            relative = file.parent.relative_to(ROOT / "resources")
            datas.append((str(file), f"resources/{relative.as_posix()}"))

# Processo auxiliar da clonagem: roda com o Python de .venv-clonagem, entao vai como arquivo.
# Sem esse ambiente na maquina, a trava desativa a clonagem e o script nunca e usado.
datas.append((str(ROOT / "src" / "docaudio" / "speech" / "clone_worker.py"), "."))

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["piper.voice", "comtypes.client", "comtypes.stream"],
    # pymupdf.layout (analise de layout por IA, ~50 MB) e opcional para o PyMuPDF e nao e usado.
    excludes=["tkinter", "torch", "flask", "piper.train", "piper.http_server", "pymupdf.layout", "pymupdf4llm"],
    noarchive=False,
)
# OpenGL por software e desnecessario para uma interface de widgets.
a.binaries = [b for b in a.binaries if Path(b[0]).name.lower() != "opengl32sw.dll"]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DocAudio",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DocAudio", upx=False)
