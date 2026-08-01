"""Renderiza el mismo guion con varios montajes, para poder elegir uno.

El formato no se decide leyendo código: se decide viendo los vídeos seguidos y
señalando el que se prefiere. Lo caro de un vídeo —el metraje, las fotografías,
la locución— no depende del montaje, así que se paga una vez y se monta tantas
veces como formatos haya que juzgar. Tres muestras cuestan lo que una.

    python -m tools.muestras                     # el último guion publicado
    python -m tools.muestras --formatos rapido   # sólo uno
    python -m tools.muestras --guion guion.json  # uno escrito a mano

Sale en ``data/muestras/<formato>.mp4``. Ninguna muestra se publica: esto no
toca el ciclo, ni la base de datos, ni las redes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autenia import formats, render  # noqa: E402
from autenia.store import init_db, session  # noqa: E402

SALIDA = os.path.join("data", "muestras")
LIBRARY_DIR = os.environ.get("AUTENIA_LIBRARY_DIR", "data/library")


async def _ultimo_guion() -> tuple[str, dict]:
    """El guion publicado más reciente, para no pagar una investigación nueva."""
    from sqlalchemy import select  # noqa: PLC0415

    from autenia.models import Version  # noqa: PLC0415

    await init_db()
    async with session() as sess:
        rows = await sess.execute(
            select(Version).where(Version.script.is_not(None))
            .order_by(Version.created_at.desc()).limit(20))
        for version in rows.scalars():
            script = json.loads(version.script)
            if script.get("hook") and script.get("escenas"):
                return version.id, script
    raise SystemExit("no hay ningún guion en la base de datos todavía")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formatos", default=",".join(formats.PRESETS),
                        help="separados por comas")
    parser.add_argument("--guion", help="fichero JSON con un guion")
    parser.add_argument("--salida", default=SALIDA)
    parser.add_argument("--solo-comparativa", action="store_true",
                        help="junta las muestras que ya existen, sin renderizar")
    args = parser.parse_args()

    elegidos = [formats.get(name.strip())
                for name in args.formatos.split(",") if name.strip()]

    if args.solo_comparativa:
        _comparativa([f.name for f in elegidos], args.salida)
        return

    if args.guion:
        with open(args.guion, encoding="utf-8") as handle:
            script = json.load(handle)
        origen = os.path.basename(args.guion)
    else:
        origen, script = await _ultimo_guion()

    os.makedirs(args.salida, exist_ok=True)
    print(f"guion: {origen}")
    print(f"hook:  {script.get('hook', '')[:70]}")
    print(f"montajes: {', '.join(f.name for f in elegidos)}\n")

    # Lo que cuesta dinero, una sola vez. El formato que se pasa aquí sólo
    # decide cuántos encuadres se generan por escena, así que se prepara con el
    # más exigente y los demás usan un subconjunto de lo mismo.
    exigente = max(elegidos, key=lambda f: (f.new_clips, -f.max_shot_s))
    trabajo = os.path.join(args.salida, "_material")
    print("preparando metraje, fotos y locución (esto es lo único que se paga)…")
    segmentos = await render.prepare(
        script, workdir=trabajo, fmt=exigente,
        library_dir=LIBRARY_DIR if os.path.isdir(LIBRARY_DIR) else None)

    con_video = sum(1 for s in segmentos if s.clip_path or (
        s.asset_path and s.asset_path.endswith(".mp4")))
    print(f"  {len(segmentos)} segmentos, {con_video} con metraje real, "
          f"{sum(s.duration_s for s in segmentos):.1f} s de locución\n")

    for fmt in elegidos:
        destino = os.path.join(args.salida, f"{fmt.name}.mp4")
        print(f"montando «{fmt.label}»…")
        hecho = render.compose(
            segmentos, destino, os.path.join(args.salida, f"_montaje_{fmt.name}"),
            fmt=fmt)
        planos = sum(len(s.shots(fmt)) for s in segmentos)
        print(f"  {destino}  {hecho.duration_s:.1f} s, {planos} planos, "
              f"un corte cada {hecho.duration_s / planos:.1f} s\n")

    if len(elegidos) > 1:
        _comparativa([f.name for f in elegidos], args.salida)

    print("Míralos seguidos y dime cuál. Se fija con AUTENIA_FORMATO=<nombre> "
          "en el .env.")


def _comparativa(nombres: list[str], salida: str) -> str:
    """Todas las muestras en un solo fichero, cada una con su nombre delante.

    Elegir formato es comparar, y comparar cinco ficheros sueltos en el
    explorador es exactamente donde se abandona la comparación. Uno solo se ve
    en el móvil de una sentada.
    """
    from autenia import ffmpeg as ff  # noqa: PLC0415
    from autenia import render as rr  # noqa: PLC0415

    trozos = []
    for nombre in nombres:
        muestra = os.path.join(salida, f"{nombre}.mp4")
        if not os.path.isfile(muestra):
            continue
        rotulo = os.path.join(salida, f"_rotulo_{nombre}.mp4")
        tarjeta = rr.card(formats.get(nombre).label,
                          os.path.join(salida, f"_rotulo_{nombre}.png"),
                          kind="hook")
        # Silencio deliberado bajo el rótulo: sin pista de audio, concat deja
        # fuera el audio de todo lo que venga después. Y sin normalizar: medir
        # la sonoridad de un silencio da NaN y tumba el encode.
        rr._run([rr._ffmpeg(), "-y", "-loop", "1", "-t", "1.4", "-i", tarjeta,
                 "-f", "lavfi", "-t", "1.4", "-i", "anullsrc=r=48000:cl=stereo",
                 "-vf", f"scale={rr.WIDTH}:{rr.HEIGHT},fps={rr.FPS},"
                        f"format=yuv420p",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                 "-shortest", rotulo])
        trozos += [rotulo, muestra]

    listado = os.path.join(salida, "_comparativa.txt")
    with open(listado, "w", encoding="utf-8") as handle:
        for trozo in trozos:
            handle.write(f"file '{os.path.abspath(trozo)}'\n")

    destino = os.path.join(salida, "comparativa.mp4")
    # Recodifica en vez de copiar: los rótulos y las muestras no comparten
    # parámetros de flujo, y un concat por copia con eso dentro sale roto a la
    # mitad, que es justo donde nadie mira.
    rr._run([rr._ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", listado,
             "-c:v", "libx264", "-preset", "medium", "-crf", "21",
             *ff.audio_encode_args(), "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", destino])
    print(f"\ncomparativa: {destino}")
    return destino


if __name__ == "__main__":
    asyncio.run(main())
