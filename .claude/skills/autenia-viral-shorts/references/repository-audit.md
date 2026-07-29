# Auditoría del repositorio

**Commit auditado:** `4fcc1cd` (2026-07-28), rama `dev`
**Remoto:** `origin` → `https://github.com/juanjuzgado02/-Lead-autenia.git`

Si `HEAD` ha avanzado, reaudita solo los flujos afectados y actualiza este archivo.
Todas las referencias a líneas se verificaron sobre este commit.

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

## 3. Secretos: hoy viven en el navegador

Modelo actual (correcto para SaaS, incorrecto para herramienta interna): el usuario
pega las claves en Ajustes, se guardan en `localStorage` y viajan en cabeceras por
petición.

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

**Conflicto documental que hay que resolver antes de tocar nada:** `CLAUDE.md`
documenta explícitamente *"API keys are stored encrypted in the browser and sent via
headers only when needed. Never stored server-side."* El paso 1 de la skill invierte
esto. Es la decisión correcta para un despliegue monousuario con `.env`, pero
`CLAUDE.md` debe actualizarse en el mismo commit o toda sesión futura recibirá
órdenes contradictorias.

**Higiene:** si alguna clave real estuvo alguna vez en `localStorage` de un navegador
sincronizado, rótala. No la migres automáticamente.

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

## 6. Superficie pública que hay que cerrar

Rutas servidas sin autenticación:

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

1. `core_config.py` + validación al arranque + `CLAUDE.md` actualizado (§3).
2. Cerrar rutas públicas y quitar galería del dashboard (§6).
3. Sacar claves del frontend y sus cabeceras (§3).
4. Almacén SQLite propio de contenidos/versiones (§2).
5. Modo `faceless` (§4) y desrigidizar el prompt (§5).
6. Bot de Telegram con aprobación (§2).
7. Publicación idempotente en dry-run.

Los pasos 1–3 no cuestan nada en APIs y reducen superficie de riesgo. Hazlos antes
de gastar en generación.
