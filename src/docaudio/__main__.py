"""Linha de comando do DocAudio. Sem argumentos, abre a interface."""

import argparse
import logging
import sys
from pathlib import Path

from docaudio import APP_NAME, __version__
from docaudio.core import dependencies, logs, paths
from docaudio.speech.base import SpeechError
from docaudio.speech.registry import VoiceRegistry
from docaudio.storage.settings import Settings

TEST_PHRASE = "Olá! Este é o DocAudio. A voz offline está funcionando corretamente."

log = logging.getLogger("docaudio")


def cmd_verificar(args: argparse.Namespace, settings: Settings) -> int:
    print(f"{APP_NAME} {__version__}")
    print(f"Dados do usuário: {paths.data_dir()}")
    print(f"Saída: {settings.resolved_output_dir()}")
    missing_required = False
    for status in dependencies.check_all(settings):
        mark = "OK " if status.ok else ("ERRO" if status.required else "AVISO")
        print(f"[{mark}] {status.name}: {status.detail}")
        if status.hint:
            print(f"       {status.hint}")
        missing_required |= status.required and not status.ok
    return 1 if missing_required else 0


def cmd_vozes(args: argparse.Namespace, settings: Settings) -> int:
    voices = VoiceRegistry().list_voices()
    if not voices:
        print("Nenhuma voz instalada.")
        return 1
    for voice in voices:
        active = "*" if voice.id == settings.voice else " "
        print(f"{active} {voice.id:<34} {voice.display_name}")
    return 0


def cmd_falar(args: argparse.Namespace, settings: Settings) -> int:
    out = Path(args.saida) if args.saida else settings.resolved_output_dir() / "teste_voz.wav"
    voice = args.voz or settings.voice
    speed = args.velocidade or settings.speed
    try:
        VoiceRegistry().synthesize_to_wav(args.texto or TEST_PHRASE, voice, speed, out)
    except SpeechError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        log.error("%s", exc)
        return 1
    print(f"Áudio gerado: {out}")
    log.info("Audio gerado pela linha de comando: %s", out)
    return 0


def cmd_ler(args: argparse.Namespace, settings: Settings) -> int:
    import json

    from docaudio.core.loader import estimated_seconds, format_duration, load_document
    from docaudio.extractors.base import ExtractionError

    try:
        document = load_document(Path(args.arquivo), settings)
    except ExtractionError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        log.error("%s", exc)
        return 1

    if args.referencia:
        reference = {
            "arquivo": document.source.name,
            "titulo": document.title,
            "capitulos": [c.title for c in document.chapters],
            "contem": [],
            "nao_contem": [],
        }
        print(json.dumps(reference, indent=2, ensure_ascii=False))
        return 0

    print(f"{document.title} ({document.kind.value}, {document.page_count or '-'} página(s))")
    for warning in document.warnings:
        print(f"  aviso: {warning}")
    for chapter in document.chapters:
        duration = format_duration(estimated_seconds(chapter.char_count(), settings.speed))
        print(f"\n[{chapter.number}] {chapter.title}  ({chapter.char_count()} caracteres, ≈ {duration})")
        for block in chapter.blocks:
            mark = "+" if block.include else "-"
            text = block.text.replace("\n", " / ")
            if not args.completo and len(text) > 100:
                text = text[:100] + "…"
            print(f"  {mark} {block.type.value:<10} {text}")
    return 0


def cmd_converter(args: argparse.Namespace, settings: Settings) -> int:
    from docaudio.audio.ffmpeg import Cancelled
    from docaudio.core.conversion import ConversionError, ConversionJob
    from docaudio.core.loader import load_document
    from docaudio.extractors.base import ExtractionError

    if args.saida:
        settings.output_dir = args.saida
    if args.formato:
        settings.output_format = args.formato
    if args.voz:
        settings.voice = args.voz
    if args.velocidade:
        settings.speed = args.velocidade
    status = 0
    for name in args.arquivos:
        try:
            document = load_document(Path(name), settings)
            if args.capitulos:
                wanted = {int(n) for n in args.capitulos.split(",")}
                for chapter in document.chapters:
                    chapter.include = chapter.number in wanted
            last = [-1]

            def progress(p) -> None:
                percent = int(p.fraction * 100)
                if percent != last[0]:
                    last[0] = percent
                    print(f"\r  {percent:3d}%  {p.message[:70]:<70}", end="", flush=True)

            # --voz vale para tudo; sem ela, cada documento usa a voz do seu idioma.
            voiced = settings if args.voz else settings.for_language(document.language)
            result = ConversionJob(document, voiced, VoiceRegistry(), progress=progress).run()
            print()
            reused = f" ({result.reused_chapters} reaproveitado(s))" if result.reused_chapters else ""
            print(f"{document.source.name}: {len(result.files)} arquivo(s){reused} em {result.folder}")
        except (ExtractionError, ConversionError) as exc:
            print(f"\nErro: {exc}", file=sys.stderr)
            log.error("%s", exc)
            status = 1
        except (Cancelled, KeyboardInterrupt):
            print("\nInterrompido; rode o mesmo comando para retomar.", file=sys.stderr)
            return 130
    return status


def cmd_clonar_voz(args: argparse.Namespace, settings: Settings) -> int:
    from docaudio.core.dependencies import find_ffmpeg
    from docaudio.speech import clone_provider
    from docaudio.speech.base import SpeechError

    clone_provider.set_enabled(settings.clone_enabled)
    try:
        if args.acao != "listar":
            support = clone_provider.check_support(probe=True)
            if not support.available:
                print(f"Clonagem indisponível: {support.reason}", file=sys.stderr)
                return 1
        if args.acao == "preparar":
            print(f"Baixando o modelo de clonagem (cerca de {clone_provider.MODEL_SIZE_GB} GB)…")
            clone_provider.prepare_model()
            print(f"Modelo pronto em {clone_provider.model_dir()}")
        elif args.acao == "criar":
            if not (args.nome and args.arquivo):
                print("Uso: docaudio clonar-voz criar \"Nome\" gravacao.wav --autorizo", file=sys.stderr)
                return 2
            voice = clone_provider.create_voice(
                args.nome, Path(args.arquivo), args.autorizo, find_ffmpeg(settings.ffmpeg_path)
            )
            print(f"Voz criada: {voice.id} ({voice.label})")
        else:
            for voice in clone_provider.ClonedVoiceProvider().list_voices():
                print(f"{voice.id:<30} {voice.label}")
    except (SpeechError, ValueError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        log.error("%s", exc)
        return 1
    return 0


def cmd_baixar_voz(args: argparse.Namespace, settings: Settings) -> int:
    from docaudio.speech.voice_download import download_voice

    dest = Path(args.destino) if args.destino else paths.user_voices_dir()
    last_percent = -1

    def progress(name: str, done: int, total: int) -> None:
        nonlocal last_percent
        percent = done * 100 // total if total else 0
        if percent != last_percent and percent % 10 == 0:
            print(f"  {name}: {percent}%")
            last_percent = percent

    try:
        model = download_voice(args.voz, dest, progress)
    except (OSError, ValueError) as exc:
        print(f"Erro ao baixar voz: {exc}", file=sys.stderr)
        return 1
    print(f"Voz instalada em {model}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docaudio", description="Converte documentos em áudio.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="comando")

    sub.add_parser("verificar", help="verifica dependências locais").set_defaults(func=cmd_verificar)
    sub.add_parser("vozes", help="lista vozes instaladas").set_defaults(func=cmd_vozes)

    falar = sub.add_parser("falar", help="narra um texto em WAV (padrão: frase de teste)")
    falar.add_argument("texto", nargs="?", help="texto a narrar")
    falar.add_argument("-o", "--saida", help="arquivo WAV de saída")
    falar.add_argument("--voz", help="identificador da voz, ex.: pt_BR-faber-medium")
    falar.add_argument("--velocidade", type=float, help="1.0 = normal")
    falar.set_defaults(func=cmd_falar)

    ler = sub.add_parser("ler", help="mostra capítulos e blocos extraídos de um documento")
    ler.add_argument("arquivo")
    ler.add_argument("--completo", action="store_true", help="não abrevia os textos")
    ler.add_argument("--referencia", action="store_true", help="imprime o esqueleto de .esperado.json")
    ler.set_defaults(func=cmd_ler)

    converter = sub.add_parser("converter", help="converte documentos em áudio (retoma conversões interrompidas)")
    converter.add_argument("arquivos", nargs="+")
    converter.add_argument("--saida", help="pasta de saída")
    converter.add_argument("--formato", choices=["mp3", "m4a", "m4b", "wav"])
    converter.add_argument("--capitulos", help="números dos capítulos, ex.: 1,3,4")
    converter.add_argument("--voz")
    converter.add_argument("--velocidade", type=float)
    converter.set_defaults(func=cmd_converter)

    clonar = sub.add_parser("clonar-voz", help="cria vozes a partir de uma gravação sua (Chatterbox)")
    clonar.add_argument("acao", choices=["preparar", "criar", "listar"])
    clonar.add_argument("nome", nargs="?")
    clonar.add_argument("arquivo", nargs="?", help="gravação de 10 a 30 s (WAV, MP3, M4A…)")
    clonar.add_argument(
        "--autorizo", action="store_true", help="confirma que a voz é sua ou de quem autorizou o uso"
    )
    clonar.set_defaults(func=cmd_clonar_voz)

    baixar = sub.add_parser("baixar-voz", help="baixa uma voz Piper do repositório oficial")
    baixar.add_argument("voz", nargs="?", default=Settings().voice)
    baixar.add_argument("--destino", help="pasta de destino (padrão: vozes do usuário)")
    baixar.set_defaults(func=cmd_baixar_voz)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from docaudio.app import main as gui_main

        return gui_main()

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logs.setup_logging()
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 2
    try:
        settings = Settings.load()
        from docaudio.speech import clone_provider
        from docaudio.text_processing import pronunciation

        clone_provider.set_enabled(settings.clone_enabled)
        pronunciation.set_themes(settings.pronunciation_themes)
        return args.func(args, settings)
    except Exception:
        # No executavel sem console, o log e o unico lugar onde o erro aparece.
        log.exception("Erro inesperado no comando '%s'", args.comando)
        raise


if __name__ == "__main__":
    sys.exit(main())
