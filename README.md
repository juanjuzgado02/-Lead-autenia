# Autenia Viral Shorts

Herramienta interna de [Autenia](https://auteniaai.com/). Busca una noticia
relevante sobre IA y automatización, escribe un guion fundamentado, te lo manda
por Telegram para que lo apruebes, y solo entonces lo convierte en un vídeo
vertical y lo publica en TikTok, Instagram Reels y YouTube Shorts.

No es un producto. No tiene clientes, ni panel web, ni registro. Hay un usuario:
tú.

## Cómo se usa

```bash
pip install -r requirements.txt
cp .env.example .env           # y rellena lo que necesite la fase que vayas a usar

python autenia_bot.py cycle    # busca tema y te manda el guion por Telegram
python autenia_bot.py listen   # se queda escuchando tus botones
python autenia_bot.py both     # las dos cosas
```

O en Docker, que ya trae ffmpeg dentro:

```bash
docker compose up --build
```

Hace falta `ffmpeg` en el sistema si lo ejecutas fuera del contenedor
(`winget install ffmpeg` en Windows, `apt install ffmpeg` en el servidor).

## El ciclo

1. **Busca** noticias recientes con búsqueda real de Google, y guarda de cada
   una su URL canónica, el medio, la fecha y los hechos concretos.
2. **Filtra y puntúa**: descarta política, polémica y lo que no tenga que ver
   con Autenia; puntúa actualidad, dolor del cliente, encaje, gancho, evidencia,
   potencial visual, conversión y coste.
3. **Escribe** un guion que separa hechos de opinión y se queda con las fuentes.
4. **Comprueba** duración, afirmaciones, derechos de uso y coste estimado antes
   de gastar un céntimo.
5. **Te pregunta** por Telegram: guion, fuentes, coste y cuatro botones —
   `Aprobar`, `Pedir cambios`, `Rechazar`, `Regenerar`.
6. **Renderiza** solo lo aprobado: narración por segmentos, material propio de
   `data/library`, subtítulos quemados, 1080×1920 a 30 fps.
7. **Publica** en las tres redes por separado, para que un fallo en una no tape
   el éxito en las otras.

Si ningún tema del día supera el listón, **no produce nada** y te lo dice. Un día
sin vídeo es un resultado válido; rellenar el calendario con relleno no lo es.

## Lo que necesita de ti

El código no puede resolver esto:

- Las claves: Gemini, el bot de Telegram y Upload-Post. ElevenLabs solo si
  quieres su voz en vez de la de Gemini, que es gratis y ya está verificada en
  español.
- Las tres cuentas sociales conectadas dentro de Upload-Post.
- **Material visual propio** en `data/library`: capturas de agentes, cuadros de
  mando, automatizaciones reales. Sin ellas el vídeo cae en tarjetas de texto,
  que es honesto pero no es lo que diferencia a Autenia.
- Un servidor encendido 24/7, si quieres que funcione con el portátil cerrado.

## Seguridad

- **Nada se publica sin que tú lo apruebes.** Además, `AUTENIA_PUBLISH_DRY_RUN`
  viene en `true` y solo se apaga escribiendo un valor que signifique "falso"
  sin ambigüedad: una errata deja el sistema en simulación, nunca al aire.
- **Un solo chat de Telegram manda.** Cualquier otro se ignora sin contestar.
- **Las claves viven en el `.env` del servidor** y en ningún otro sitio. No hay
  navegador donde pudieran acabar.
- Ningún secreto se escribe en un log: `core_config.sanitize()` los tapa antes.

## Origen

Esto empezó como un fork de [OpenShorts](https://github.com/mutonby/openshorts),
que recortaba vídeos largos de YouTube en clips. De aquello queda muy poco, a
propósito: aquel producto partía de un vídeo ajeno y buscaba los mejores
momentos; este parte de una noticia y escribe uno nuevo. El 30 de julio de 2026
se eliminó todo lo que servía solo al producto original — el panel de React, la
facturación, Whisper, YOLO, MediaPipe, yt-dlp, el renderizador de Remotion y el
servidor FastAPI. Sigue en el historial de git por si alguna decisión hay que
revisarla.

Se conserva la licencia MIT del original en `LICENSE`.
