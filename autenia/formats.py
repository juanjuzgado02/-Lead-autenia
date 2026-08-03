"""Cómo se monta el vídeo, separado de qué dice.

El guion, la voz y las imágenes son los mismos se elija el montaje que se elija.
Lo que cambia entre un formato y otro es el ritmo del corte, cómo aparecen las
palabras y cuánto se mueve la cámara — y eso es exactamente lo que hay que poder
comparar sin volver a pagar la investigación, el metraje ni la locución.

Por eso un formato es datos, no código: se renderiza el mismo guion con todos
los montajes, se ven seguidos y se elige. Proponer uno nuevo es añadir una
entrada a :data:`PRESETS`, no tocar el renderizador.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Format:
    """Un montaje concreto: cada cuánto corta, cómo escribe y cuánto empuja."""

    name: str
    label: str

    #: Lo máximo que un plano aguanta en pantalla antes de cortar. Por debajo de
    #: dos segundos el corte deja de ser ritmo y empieza a marear.
    max_shot_s: float = 3.5

    #: Si un clip de vídeo también se corta por dentro. El corte no salta en el
    #: tiempo —el metraje sigue avanzando— sino en el encuadre, así que se lee
    #: como dos cámaras sobre la misma acción y no como un plano repetido.
    cut_footage: bool = False

    #: Cuánto se acerca el encuadre en los planos alternos, sobre 1. Un salto
    #: seco de 6% es lo que hace que un corte se note como corte.
    punch: float = 0.0

    #: Deriva lenta sobre una foto fija, en tanto por uno del ancho.
    zoom: float = 0.08

    #: "placa"    — la frase entera, abajo, sobre una placa oscura
    #: "grupos"   — tres o cuatro palabras cada vez, sincronizadas con la voz
    #: "kinetico" — lo mismo pero grande y en el centro, sin placa
    #: "ninguno"  — sin subtítulo
    #:
    #: "ninguno" no es un montaje que se elija por gusto, es una salida. Un
    #: subtítulo que va por delante de lo que se está diciendo es peor que no
    #: tenerlo: el ojo lee antes de que la voz llegue y el vídeo se lee como
    #: roto. Mientras el reparto de la voz no sea de fiar, poder quitarlos
    #: convierte un vídeo tirado en un vídeo publicable sin pagar nada.
    caption: str = "placa"

    caption_size: int = 44
    caption_bottom: int = 210
    caption_width: int = 26
    plate_alpha: int = 115

    #: Palabras por grupo cuando el subtítulo va por grupos.
    caption_words: int = 4

    #: Si las cifras se pintan en el color de marca. Este canal vive de datos
    #: —un 7,7%, un plazo, un coste—, y una cifra en color es lo primero que
    #: encuentra el ojo cuando el vídeo se ve sin sonido.
    emphasis: bool = False

    #: Segundos que el gancho pasa sobre una tarjeta tipográfica antes de cortar
    #: al metraje. Se los come al principio de su propia escena, así que la voz
    #: no se desincroniza. 0 apaga la tarjeta.
    hook_card: float = 0.0

    #: Barra de progreso en el borde inferior. Enseña cuánto queda, que es la
    #: razón por la que la gente se queda.
    progress: bool = False

    #: El dominio en una esquina, todo el vídeo. Lo que se vende.
    brand: str = ""

    #: Cuántos clips nuevos como mucho paga un vídeo con este formato. Los que
    #: ya están en caché son gratis y no cuentan.
    new_clips: int = 4


#: El montaje sereno: planos largos, corte sólo al cambiar de escena, subtítulo
#: abajo. Es el que había, con más metraje pagado por vídeo.
CONTINUO = Format(
    name="continuo",
    label="Continuo — planos largos, subtítulo abajo",
    max_shot_s=4.5,
    cut_footage=False,
    punch=0.0,
    zoom=0.08,
    caption="placa",
    caption_size=44,
    caption_bottom=210,
    caption_width=26,
    new_clips=8,
)

#: El montaje de feed: corta cada dos segundos, también dentro del metraje, y
#: las palabras entran al ritmo de la voz. Es el que más se parece a lo que
#: funciona en TikTok, y el que peor envejece si el guion no aguanta el ritmo.
RAPIDO = Format(
    name="rapido",
    label="Rápido — corte cada 2 s, subtítulo por grupos",
    max_shot_s=2.0,
    cut_footage=True,
    punch=0.07,
    zoom=0.12,
    caption="grupos",
    caption_size=52,
    caption_bottom=430,
    caption_width=18,
    caption_words=4,
    plate_alpha=130,
    new_clips=8,
)

#: El montaje ruidoso: el metraje es fondo y la tipografía es el protagonista,
#: grande y en el centro. Se lee sin sonido, que es como se ve la mitad del feed.
KINETICO = Format(
    name="kinetico",
    label="Kinético — tipografía grande al centro, sin placa",
    max_shot_s=2.6,
    cut_footage=True,
    punch=0.05,
    zoom=0.10,
    caption="kinetico",
    caption_size=86,
    caption_bottom=780,
    caption_width=12,
    caption_words=3,
    plate_alpha=0,
    new_clips=8,
)


#: El montaje de anuncio: abre con el gancho escrito a pantalla completa antes
#: de enseñar nada, pinta las cifras en color y lleva barra de progreso. Es el
#: que más se parece a lo que vende una empresa, y el que peor perdona un gancho
#: flojo: si la frase no aguanta a pantalla completa, se ve enseguida.
TITULAR = Format(
    name="titular",
    label="Titular — abre con el gancho escrito, cifras en color",
    max_shot_s=2.6,
    cut_footage=True,
    punch=0.06,
    zoom=0.10,
    caption="grupos",
    caption_size=50,
    caption_bottom=240,
    caption_width=20,
    caption_words=5,
    emphasis=True,
    hook_card=1.2,
    progress=True,
    new_clips=8,
)

#: El montaje de marca: metraje corriendo, cifras en color y el dominio en la
#: esquina de principio a fin. Nadie recuerda un vídeo bueno de una cuenta que
#: no sabe nombrar, y el sitio es lo único que se vende aquí.
MARCADO = Format(
    name="marcado",
    label="Marcado — cifras en color y el dominio en pantalla",
    max_shot_s=2.2,
    cut_footage=True,
    punch=0.07,
    zoom=0.12,
    caption="grupos",
    caption_size=54,
    caption_bottom=420,
    caption_width=17,
    caption_words=4,
    plate_alpha=125,
    emphasis=True,
    progress=True,
    brand="auteniaai.com",
    new_clips=8,
)


PRESETS: dict[str, Format] = {
    fmt.name: fmt
    for fmt in (CONTINUO, RAPIDO, KINETICO, TITULAR, MARCADO)
}

#: El que se usa cuando nadie pide otro.
DEFAULT = CONTINUO


def get(name: str | None) -> Format:
    """El formato pedido, o el de por defecto si no se pide ninguno."""
    if not name:
        return DEFAULT
    try:
        return PRESETS[name.strip().lower()]
    except KeyError:
        raise ValueError(
            f"formato desconocido: {name!r}; hay "
            f"{', '.join(sorted(PRESETS))}") from None


def sin_subtitulos(fmt: Format) -> Format:
    """El mismo montaje, mudo de texto. No toca nada más.

    Un formato es inmutable a propósito —es datos— así que esto devuelve otro,
    igual salvo el subtítulo. Todo lo demás (el ritmo del corte, la cámara, la
    tarjeta del gancho, la marca) se conserva, porque quitar el subtítulo es
    quitar el subtítulo y no cambiar de montaje.
    """
    return replace(fmt, caption="ninguno")


def subtitulos_activos() -> bool:
    """Si el ciclo diario pone subtítulos. Apagarlos es una decisión de Juan."""
    return (os.environ.get("AUTENIA_SUBTITULOS", "on").strip().lower()
            not in ("0", "false", "no", "off"))


def current() -> Format:
    """El montaje que usa el ciclo diario.

    Se elige viendo muestras, no leyendo código, así que vive en una variable de
    entorno: se cambia sin tocar nada y sin desplegar. Un nombre que no existe
    cae al de por defecto en vez de tumbar el ciclo — quedarse sin vídeo por una
    errata en el montaje sería el peor cambio posible por el menor motivo.

    ``AUTENIA_SUBTITULOS=off`` los quita de cualquiera de los cinco, sin tener
    que duplicar los cinco presets para tener las dos variantes.
    """
    name = os.environ.get("AUTENIA_FORMATO", "").strip().lower()
    fmt = PRESETS.get(name, DEFAULT)
    return fmt if subtitulos_activos() else sin_subtitulos(fmt)
