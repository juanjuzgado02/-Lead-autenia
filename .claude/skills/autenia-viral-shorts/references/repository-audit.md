# Auditoría del repositorio

**Estado auditado:** rama `chore/recorte-para-autenia` (2026-07-30), sobre `dev`
**Remoto:** `origin` → `https://github.com/juanjuzgado02/-Lead-autenia.git`

Si `HEAD` ha avanzado, reaudita solo los flujos afectados y actualiza este archivo.

> **El repositorio se recortó el 2026-07-30.** La auditoría anterior describía el
> fork de OpenShorts entero —`app.py` de 3.797 líneas, `main.py`, `saasshorts.py`,
> `cloud/`, el panel de React, Remotion— con referencias a líneas concretas. Nada
> de eso existe ya en el árbol de trabajo. Si buscas una de aquellas rutas, está
> en el historial de git, no en disco.

---

## 1. Qué es hoy este repositorio

Un solo producto: la herramienta interna que publica los shorts de Autenia. Nació
como fork de OpenShorts, un SaaS que recortaba vídeos largos de YouTube para
usuarios externos que traían sus claves desde el navegador. Esa diferencia de
propósito generaba casi toda la deuda de la auditoría anterior, y se resolvió
borrando el producto ajeno en vez de adaptarlo pieza a pieza.

La distinción que importa: **OpenShorts partía del vídeo de otro y buscaba los
mejores momentos; esto parte de una noticia y escribe uno nuevo.** Por eso no hay
ingesta, ni transcripción, ni seguimiento de caras, ni recorte: no hay vídeo de
origen que analizar.

### Mapa de módulos

3.954 líneas de Python en total, ninguna con más de 400.

| Archivo | Líneas | Rol |
|---|---:|---|
| `core_config.py` | 383 | Toda la configuración y los secretos. Máscara, saneado, validación de arranque |
| `autenia/gemini.py` | 384 | Llamadas al modelo: juicio, guion, TTS |
| `autenia/render.py` | 374 | Guion → máster 9:16. Solo ffmpeg |
| `autenia/cycle.py` | 342 | Orquestación del ciclo diario y sus transiciones |
| `autenia/store.py` | 338 | SQLite con SQLAlchemy async |
| `autenia/telegram.py` | 303 | Long polling, un chat autorizado, callbacks idempotentes |
| `autenia/editorial.py` | 272 | Filtros duros y puntuación |
| `autenia/sources.py` | 269 | Búsqueda con grounding, URLs canónicas, atribución |
| `autenia/publish.py` | 222 | Upload-Post, una llamada por red, dry-run por defecto |
| `autenia/assets.py` | 208 | Biblioteca de material propio y medida de cobertura |
| `autenia/preflight.py` | 167 | La última puerta antes de gastar |
| `autenia/models.py` | 158 | Tablas `contents`, `versions`, `cost_entries` |
| `autenia/ffmpeg.py` | 142 | Elección de codificador y normalización a −14 LUFS |
| `autenia/states.py` | 124 | La máquina de estados |
| `autenia/voice.py` | 121 | Narración agnóstica de proveedor |
| `autenia/urls.py` | 79 | Guardia SSRF |
| `autenia_bot.py` | 62 | Punto de entrada: `listen`, `cycle`, `both` |
| `tests/` | 12 archivos | 200 pruebas, ~3 s |

Dependencias: `httpx`, `sqlalchemy[asyncio]`, `aiosqlite`, `Pillow`,
`python-dotenv`. Nada más. Más ffmpeg y ffprobe, que son binarios del sistema.

---

## 2. Qué se eliminó, y por qué mirarlo antes de reconstruir

Todo esto está en el historial de git a partir del commit de recorte:

| Eliminado | Por qué no aplica |
|---|---|
| `dashboard/` (135 MB) | Panel, landing, precios, galerías UGC y SEO de un producto que no se vende. La revisión es por Telegram |
| `cloud/` (16 archivos) | Facturación, auth, OAuth, cuotas, correos. Licencia comercial aparte. Multiempresa |
| `main.py`, `saasshorts.py`, `reframe_v2.py`, `scene_detection.py`, `clip_selection.py`, `transcribe_backends.py`, `quality_probe.py`, `gemini_worker.py`, `thumbnail.py` | El pipeline de recortar vídeo ajeno |
| `app.py` (3.797 líneas) | Servidor FastAPI del producto SaaS. Se rescató de él la integración con Upload-Post → `autenia/publish.py` |
| `editor.py`, `edit_builder.py`, `subtitles.py`, `hooks.py` | Efectos, subtítulos y hooks del otro pipeline. `render.py` hace lo suyo con Pillow y drawtext |
| `translate.py` | Doblaje a 30 idiomas. Autenia produce solo en español |
| `s3_uploader.py` | Copia y galerías en S3, orientado a la galería pública |
| `remotion/`, `render-service/` | Renderizador alternativo en Node sin consumidor |
| `alembic/` | Migraciones de la base Postgres de `cloud/` |
| 18 archivos de `tests/` | Cubrían lo anterior |

**Se conservó `LICENSE`** (MIT de OpenShorts): es obligación de la licencia, no
un descuido.

Dos módulos sobrevivieron por mérito propio y se movieron dentro del paquete:

- `ffmpeg_utils.py` → `autenia/ffmpeg.py`. Su `audio_encode_args()` normaliza a
  −14 LUFS, el nivel al que TikTok, Reels y Shorts igualan la reproducción. Ahora
  lo usa el mux final de `render.py`.
- `security_utils.py` → `autenia/urls.py`. Su `assert_public_url` ahora protege
  `sources.py`, que sigue redirecciones que Google le da y que podrían apuntar al
  endpoint de metadatos del propio VPS.

---

## 3. Secretos: ✅ resuelto

`core_config.py` es el único sitio donde se resuelve configuración. Lee del `.env`
del servidor, enmascara con `mask()`, sanea payloads con `sanitize()` y se niega a
arrancar con `BILLING_ENABLED`. No hay navegador, así que no existe la categoría
"clave que el navegador guarda".

Una clave que alguna vez estuvo en `localStorage` de una build antigua debe
**rotarse en el proveedor**, no migrarse.

---

## 4. Superficie pública: ✅ cerrada por eliminación

La auditoría anterior pedía cerrar cuatro rutas de galería con 404 y restringir
CORS. Ahora **no hay servidor HTTP**: ni rutas, ni CORS, ni puertos publicados. El
long polling de Telegram sale hacia fuera; nada entra.

Consecuencias que conviene no reintroducir por descuido:

- `allowed_origins` se eliminó de `core_config.py`. Una lista de orígenes sin API
  que proteger se lee como protección que no existe.
- `AUTENIA_INTERNAL_MODE` sigue, pero **declara, no protege**. Si alguna vez algo
  vuelve a servir HTTP, tendrá que comprobarlo — y probablemente no debería existir.
- La autenticación que quedaba pendiente **ya no hace falta**. No se protege una
  interfaz que no existe. El control de acceso es el `chat_id` autorizado.

---

## 5. El modo faceless: ✅ construido de cero

La auditoría anterior lo señalaba como el hallazgo central: la skill recomendaba
faceless y el código solo tenía `lowcost` y `premium`, ambos generando actor con
IA y ambos exigiendo `FAL_KEY`.

Se construyó como pipeline nuevo en `autenia/render.py`, no adaptando aquel. La
narración se sintetiza **segmento a segmento**: cuesta lo mismo y da dos cosas —
duración exacta por escena, así el corte visual cae donde termina la frase y no
donde lo estimó un cálculo; y reutilización, así el feedback sobre una escena
resintetiza solo esa.

`FAL_KEY` es opcional de verdad: el camino faceless nunca lo toca.

**Cuando una escena no tiene material propio, sale una tarjeta tipográfica.** Es
una decisión, no un hueco pendiente: un clip de stock sin relación, o el
pantallazo de un producto que no existe, serían peores que texto honesto. Se
arregla con material real en `data/library`.

---

## 6. Deuda estructural pendiente

- **`render.py` y `cycle.py` son los dos únicos módulos sin pruebas.** Son también
  los que gastan dinero y los que orquestan estados. Es la deuda más cara del
  repositorio ahora mismo.
- **La idempotencia de publicación está a medias.** `publish.key_for(version_id,
  platform)` calcula la clave, pero nada la persiste ni la consulta. Un reintento
  tras una caída a mitad de publicación no puede distinguir "ya subido" de "no
  subido". Falta la tabla y la comprobación previa.
- **No hay programador diario.** El ciclo solo corre lanzado a mano
  (`autenia_bot.py cycle`). Es deliberado: automatizar un flujo que aún no se ha
  probado entero solo multiplica los fallos.
- **Sin prueba de extremo a extremo con ffmpeg real.** Ninguna prueba compone un
  vídeo; solo el render manual del 2026-07-29 lo hizo.
- **Una versión quedó colgada en `renderizando`** en `data/autenia.db`
  (`8ebbb931-…`, del ciclo sobre absentismo laboral). El ordenador se apagó a
  mitad. `renderizando` no es terminal, así que bloquearía el siguiente ciclo:
  ciérrala antes de programar nada.

---

## 7. Orden de ataque recomendado

1. ✅ `core_config.py`, validación al arranque, secretos fuera del navegador.
2. ✅ Cerrar la superficie pública (por eliminación).
3. ✅ Motor editorial: fuentes, filtros, juicio, guion, preflight.
4. ✅ Almacén SQLite propio, estados y bot de Telegram.
5. ✅ Modo faceless y render 9:16.
6. ✅ Recorte del repositorio y extracción de `publish.py`.
7. **Siguiente:** pruebas de `render.py` y `cycle.py`; cerrar la versión colgada.
8. Persistir claves idempotentes y respuestas saneadas de publicación.
9. Programador diario, solo cuando el ciclo entero funcione a mano.
10. VPS.

---

## 8. Entorno de desarrollo (verificado 2026-07-30)

La máquina del usuario es Windows 10. `python` es 3.14 (el proyecto declara 3.11,
y las pruebas pasan igualmente). **No hay `ffmpeg` ni `ffprobe` en el `PATH`**, y
**Docker Desktop estaba parado** el 2026-07-30.

Consecuencias prácticas:

- Las 200 pruebas de `tests/` **corren en el host tal cual**: las dependencias
  (`sqlalchemy`, `httpx`, `Pillow`, `aiosqlite`, `python-dotenv`) están instaladas
  y ninguna prueba necesita ffmpeg. Tardan ~3 s. Ya no hace falta el venv ligero
  que describía la auditoría anterior, porque ya no hay dependencias pesadas de
  las que aislarse.
- **Cualquier verificación que ejecute ffmpeg** (render de humo, comprobar
  1080×1920 a 30 fps con `ffprobe`, oír la normalización de audio) exige levantar
  Docker o instalar ffmpeg en el host. No la des por hecha: dila como pendiente.
- El render del 2026-07-29 que produjo `data/videos/8ebbb931-…/short.mp4` se hizo
  con Docker levantado. Es la única prueba real de que la cadena completa compone.
