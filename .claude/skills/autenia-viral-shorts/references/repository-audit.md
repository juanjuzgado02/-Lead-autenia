# Auditoría del repositorio

**Commit auditado:** `4fcc1cd` (2026-07-28), rama `dev`
**Remoto:** `origin` → `https://github.com/juanjuzgado02/-Lead-autenia.git`

Si `HEAD` ha avanzado, reaudita solo los flujos afectados y actualiza este archivo.
Todas las referencias a líneas se verificaron sobre este commit.

> **Fase 0 ejecutada (2026-07-29).** Los §3 y §6 ya están resueltos; ver las notas
> ✅ en cada uno. Las referencias a líneas de `app.py` posteriores a la ~línea 90
> se han desplazado ~30 líneas por los cambios. El siguiente paso vivo es el §2
> (almacén propio) y luego el §4 (modo faceless).

---

## 1. Qué es hoy este repositorio

Un fork de OpenShorts: producto SaaS de generación de vídeo con tres herramientas
(Clip Generator, AI Shorts/UGC, YouTube Studio), pensado para usuarios externos que
traen sus propias claves desde el navegador. Autenia lo quiere como herramienta
**interna, monousuario y privada**. Casi toda la deuda listada aquí nace de esa
diferencia de propósito.

### Mapa de módulos

| Archivo | Líneas | Rol |
|---|---:|---|
| `app.py` | 3.797 | Servidor FastAPI: rutas, cola de trabajos, SEO HTML. Monolito. |
| `main.py` | 1.536 | Pipeline de clips: transcripción, escenas, corte, reencuadre 9:16 |
| `saasshorts.py` | 1.491 | Pipeline UGC: guion, actor, voz, talking head, b-roll, composición |
| `cloud/` | — | Billing, auth, cuotas, claves gestionadas. **Licencia comercial aparte** |
| `dashboard/src/App.jsx` | — | React SPA, estado global, almacenamiento de claves |
| `tests/` | 19 archivos | Cobertura unitaria de utilidades; nada de extremo a extremo |
| `remotion/`, `render-service/` | — | Servicio de render alternativo (`renderer` en Compose) |

---

## 2. Frontera de licencia: `cloud/`

`cloud/` tiene su propio `LICENSE` (OpenShorts Commercial License): se puede leer y
autoalojar para uso interno, pero **no ofrecer a terceros como servicio de pago**.

Está aislado tras una única bandera:

```python
app.py:56   BILLING_ENABLED = os.environ.get("BILLING_ENABLED", "").lower() in ("1","true","yes")
```

Con la bandera apagada (el estado que quiere Autenia) el paquete entero queda
dormido. Consecuencias prácticas que **cambian el plan de implementación**:

- **No hay configuración central utilizable.** `cloud/config.py` (282 líneas) es
  solo de modo cloud y no se carga sin la bandera. El núcleo lee `os.environ`
  disperso. → Hay que crear un `core_config.py` nuevo; no reutilizar el de `cloud/`.
- **No hay base de datos activa.** `cloud/database.py` monta SQLAlchemy async, pero
  es **solo-Postgres**: `:31` ejecuta `CREATE EXTENSION IF NOT EXISTS citext`, que
  falla en SQLite. Alembic existe pero solo cubre el esquema cloud.
  → El almacén de contenidos/versiones debe ser propio y separado.
- **El Telegram existente no sirve para aprobación.** `cloud/alerts.py:79
  send_telegram()` es un aviso unidireccional de texto plano con prefijo
  `"OPENSHORTS ✂️ - "` (`:76`), sin botones ni webhook.
  → El bot de aprobación es componente nuevo, fuera de `cloud/`.

**Regla:** no importes desde `cloud/` en código nuevo. No enciendas `BILLING_ENABLED`.

---

## 3. Secretos: ✅ resuelto en Fase 0

**Estado:** hecho. `core_config.py` resuelve todas las claves desde el `.env` del
servidor; con `AUTENIA_INTERNAL_MODE` (por defecto **on**) las cabeceras `X-*-Key`
se ignoran, el dashboard no lee ni escribe `localStorage` y purga al arrancar las
claves que dejara una build anterior (avisando de que hay que **rotarlas**, no
migrarlas). `CLAUDE.md` ya documenta el modelo correcto. Cubierto por
`tests/test_core_config.py`.

Lo que sigue aplicando: **si alguna clave real estuvo en `localStorage` de un
navegador sincronizado, rótala en el proveedor.** Borrarla del navegador solo
elimina la copia que controlamos.

<details><summary>Modelo anterior, para contexto</summary>

El usuario pegaba las claves en Ajustes, se guardaban en `localStorage` y viajaban
en cabeceras por petición.

```
dashboard/src/App.jsx:185   localStorage.getItem('gemini_key')
dashboard/src/App.jsx:188   localStorage.getItem('uploadPostKey_v3')
dashboard/src/App.jsx:194   localStorage.getItem('elevenLabsKey_v1')
dashboard/src/App.jsx:201   localStorage.getItem('falKey_v1')
```

Cabeceras que las reciben en el backend:

```
app.py:94    X-Gemini-Key        (+ :130 error si falta, :2713 dependencia)
app.py:2355  X-ElevenLabs-Key    (+ :3599, :3776)
app.py:3272  X-Fal-Key           (+ :3598)
```

</details>

---

## 4. El modo `faceless` no existe

Hallazgo central. La skill recomienda arrancar en faceless (capturas + voz +
subtítulos, sin actor) por coste y credibilidad. **No está implementado.**

```python
saasshorts.py:1391   video_mode = config.get("video_mode", "premium")
saasshorts.py:1393   if video_mode == "lowcost":
saasshorts.py:1465   if video_mode == "lowcost":
```

Solo hay `lowcost` y `premium`, y **ambos generan actor con IA**. Además fal.ai es
obligatorio, no opcional:

```python
saasshorts.py:1332   fal_key = config["fal_key"]     # KeyError si falta
```

Implicación de coste: hoy no se puede generar un AI Short sin pagar fal.ai
(~$0,50–1,50/vídeo → $15–45/mes a ritmo diario). En faceless el coste marginal es
prácticamente cero (solo voz de ElevenLabs).

**Trabajo necesario:** una tercera rama de `video_mode` que salte generación de
actor, talking head y lip-sync, y componga capturas/pantallazos + voz + subtítulos
con FFmpeg. Las piezas de voz (`saasshorts.py:781 generate_voiceover`), subtítulos
ASS y hooks ya existen y se reutilizan.

**Vía sin bloqueo mientras tanto:** el Clip Generator (`main.py`) solo necesita
`GEMINI_API_KEY`; transcripción, escenas y reencuadre corren en local. Si hay
material largo (demos, charlas, reuniones), produce hoy sin construir nada.

---

## 5. Prompt rígido de guion y actor

`saasshorts.py:444-537` impone decisiones que contradicen el brief de Autenia:

| Línea | Problema |
|---|---|
| `:534` | *"Actors must look European, attractive but natural, slightly nerdy/tech vibe"* — sesgo de aspecto codificado; elimínalo |
| `:536-537` | Ejemplos que refuerzan el mismo sesgo |
| `:530` | *"CTA MUST always mention 'link in bio' / 'enlace está en la bio'"* — CTA fijo que ignora el cuestionario de Autenia |
| `:506,:510` | Narración y subtítulo de CTA cableados a "link in bio" |
| `:444` | Estructura de escenas y duraciones fijas (CTA en 21-25s) que chocan con la ventana 25–35s |

`viral_hook_text` **sí es funcional** — se define en el esquema de `main.py:72` y lo
consumen `hooks.py`, `gemini_worker.py` y la composición. No es un campo decorativo;
verifica que sigue surtiendo efecto tras refactorizar.

---

## 6. Superficie pública: ✅ cerrada en Fase 0

**Estado:** hecho. Las cuatro rutas llaman a `deny_if_internal()` como primera
sentencia y devuelven 404 (no 403: una llamada directa no debe aprender qué
existe). La subida a galería está además condicionada a `not internal_mode`, y la
navegación y la vista `ugc-gallery` desaparecen del dashboard. CORS ya no es `*`:
usa `AUTENIA_ALLOWED_ORIGINS`. Cubierto por `tests/test_internal_mode_surface.py`,
que verifica el cableado sobre el AST de `app.py` — importar `app` arrastra torch
y mediapipe, así que no es viable fuera del contenedor.

**Sigue pendiente:** autenticación delante de la UI desplegada. Hoy el dashboard
no pide credencial; en local no importa, pero es bloqueante antes del VPS.

Rutas afectadas (líneas del commit auditado):

```
app.py:3315   GET /api/saasshorts/gallery         (JSON, todos los vídeos)
app.py:3418   GET /gallery                        (HTML SEO + JSON-LD)
app.py:3489   GET /video/{video_id}               (HTML SEO por vídeo)
app.py:3571   GET /api/saasshorts/actor-gallery   (JSON, avatares)
```

**Ya resuelto parcialmente:** la *escritura* es opt-in y por defecto no publica:

```python
app.py:3591   share_to_gallery: bool = False
app.py:3716   if req.share_to_gallery:
```

El trabajo pendiente son las cuatro rutas de **lectura**, que deben denegar por
defecto aunque se llamen a mano. Además hay que retirar navegación y componentes de
galería del dashboard.

---

## 7. Deuda estructural

- **`app.py` con 3.797 líneas** mezcla rutas, cola de trabajos, lógica de negocio y
  plantillas HTML SEO embebidas (`:3463`, `:3554`). Toda lógica nueva va en módulos
  propios; deja adaptadores finos en las rutas.
- **Sin pruebas de extremo a extremo.** Los 19 archivos de `tests/` cubren
  utilidades (reframe, subtítulos, hooks, ffmpeg, selección de clips). Nada cubre
  el flujo completo ni la publicación. Toda la lógica nueva (estados, idempotencia,
  autorización de chat) necesita pruebas propias.
- **Compose con tres servicios:** `backend`, `frontend`, `renderer`. Audita si
  `renderer` (Remotion) tiene consumidor real en el MVP faceless; si no, fuera.
- **Restos de código muerto:** `app.py:3106-3134` es una ruta de galería de clips
  comentada. Bórrala si estorba, no la revivas.

---

## 8. Orden de ataque recomendado

1. ✅ `core_config.py` + validación al arranque + `CLAUDE.md` actualizado (§3).
2. ✅ Cerrar rutas públicas y quitar galería del dashboard (§6).
3. ✅ Sacar claves del frontend y sus cabeceras (§3).
4. **Siguiente:** almacén SQLite propio de contenidos/versiones (§2).
5. Modo `faceless` (§4) y desrigidizar el prompt (§5).
6. Bot de Telegram con aprobación (§2).
7. Publicación idempotente en dry-run.

Los pasos 1–3 no cuestan nada en APIs y reducen superficie de riesgo. Hazlos antes
de gastar en generación.

Fuera de esa lista pero bloqueante antes del VPS: **autenticación delante de la UI**.

---

## 9. Entorno de desarrollo (verificado 2026-07-29)

La máquina del usuario es Windows 10 y **no tiene el entorno Python del proyecto
instalado**: `python` es 3.14 (el proyecto declara 3.11), no hay `fastapi` ni
`pytest`, y **no hay `ffmpeg` en el `PATH`**. La pila real vive en Docker.

Consecuencias prácticas:

- Las pruebas de `tests/` corren con un venv ligero: solo necesitan `pytest`,
  `python-dotenv`, `pillow`, `numpy`, `opencv-python-headless`, `httpx`,
  `pydantic`, `sqlalchemy` y `fastapi`. **No requieren torch ni mediapipe.**
  207 pasan, 4 se saltan.
- **No escribas pruebas que importen `app.py`**: arrastra `main.py` → whisper,
  ultralytics, mediapipe. Verifica el cableado de rutas sobre el AST, como hace
  `tests/test_internal_mode_surface.py`.
- Cualquier verificación con `ffmpeg`/`ffprobe` (render de humo, comprobar
  1080×1920/30 fps) hay que hacerla **dentro del contenedor**, no en el host.

### Lint del dashboard: estaba roto de origen

`npm run lint` **no podía ejecutarse** en un checkout limpio: `eslint.config.js`
usa la API plana de ESLint 9 (`eslint/config`, `defineConfig`, `globalIgnores`)
mientras `package.json` fijaba `eslint@^8.57` y el script pasaba `--ext`, retirado
en la 9. Además faltaban `@eslint/js` y `globals` en `devDependencies` (solo
funcionaban como transitivas) y el config usaba `reactHooks.configs.flat.recommended`,
que solo existe en el plugin 6.x.

Corregido en Fase 0: eslint ^9, plugin de hooks ^5 con `configs['recommended-latest']`,
dependencias declaradas y `caughtErrors: 'none'` (la 9 cambió ese valor por defecto
y el código está escrito contra el de la 8).

**Quedan 17 problemas preexistentes** (12 errores, 5 avisos) en ficheros ajenos a
esta fase: variables sin usar en `seo/render.js`, `PricingPage.jsx` y
`SaaShortsTab.jsx`; `react-refresh/only-export-components` en `main.jsx`,
`AuthContext.jsx` y `WatermarkModal.jsx`; y avisos de `exhaustive-deps`. Nunca se
habían visto porque el lint no arrancaba. Limpiarlos es una pasada aparte: tocar
dependencias de hooks puede cambiar comportamiento en runtime y no debe mezclarse
con un corte vertical.
