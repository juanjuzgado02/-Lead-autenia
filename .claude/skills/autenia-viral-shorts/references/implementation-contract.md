# Contrato de implementación

Define fases, configuración, estados, seguridad y criterios de aceptación. Si algo
aquí choca con el código actual, gana este documento y se actualiza
`repository-audit.md`.

---

## 1. Fases y criterios de aceptación

Cada fase es un corte vertical entregable. **No empieces una fase sin que la anterior
cumpla sus criterios.**

### Fase 0 — Base segura (sin coste de API)

| Entregable | Criterio de aceptación |
|---|---|
| `core_config.py` nuevo en la raíz | Arranque falla con mensaje claro si falta una variable requerida. No importa nada de `cloud/`. Ningún valor secreto aparece en logs |
| `CLAUDE.md` actualizado | La sección de claves refleja el modelo server-side. Sin contradicción con la skill |
| Rutas públicas cerradas | `GET /gallery`, `/video/{id}`, `/api/saasshorts/gallery`, `/api/saasshorts/actor-gallery` devuelven 404/403 con llamada directa. Test que lo demuestra |
| Galería fuera del dashboard | Sin navegación ni componentes de galería. `npm run build` y `npm run lint` limpios |
| Claves fuera del frontend | Cero `localStorage` de claves en `App.jsx`. Cabeceras `X-*-Key` retiradas o ignoradas. Test de resolución de secretos que no los expone |
| Autenticación delante de la UI | Ninguna interfaz desplegada accesible sin credencial. CORS restringido |

### Fase 1 — Almacén y estados

| Entregable | Criterio |
|---|---|
| SQLite propio (SQLAlchemy async + aiosqlite) | Independiente de `cloud/`. Esquema versionado. Contenido y versión persisten antes de cualquier gasto |
| Máquina de estados (§3) | Transiciones ilegales rechazadas con test |
| Registro de coste | Coste estimado y real por proveedor, contenido y versión. Un intento fallido cuenta como coste consumido |

### Fase 2 — Motor editorial (coste ínfimo)

| Entregable | Criterio |
|---|---|
| Recolección de candidatos | Persiste título, URL canónica, editor, fecha de publicación, fecha de consulta y fragmentos factuales |
| Filtros duros | Descarta duplicados, política/polémica y piezas sin relación demostrable con Autenia. Test por cada exclusión del brief §6 |
| Puntuación barata | Actualidad, dolor, encaje, hook, evidencia, potencial visual, conversión, coste. Sin llamadas caras |
| Guion fundamentado | Separa hecho de opinión y conserva fuentes |
| Preflight | Verifica duración, afirmaciones, derechos de uso, coste estimado y caché **antes** de renderizar |

### Fase 3 — Render faceless

| Entregable | Criterio |
|---|---|
| `video_mode = "faceless"` | Salta actor, talking head y lip-sync. **No requiere `FAL_KEY`** |
| Composición | 1080×1920, H.264, AAC, 30 fps verificado con `ffprobe` |
| Duración | Dentro de 20–45s, por defecto 25–35s. Máximo duro 55s |
| Prompt desrigidizado | Sin exigencia de aspecto europeo, sin CTA "link in bio" fijo, sin número fijo de escenas |
| Hook funcional | `viral_hook_text` visible en el primer segundo del render de humo |
| Reutilización | Regenerar por feedback no rehace guion, voz ni imágenes no afectados. Test de caché por hash |

### Fase 4 — Aprobación por Telegram

| Entregable | Criterio |
|---|---|
| Envío de revisión | Vídeo o enlace privado temporal + título + caption + fuentes + coste + versión |
| Botones | `Aprobar`, `Pedir cambios`, `Rechazar`, `Regenerar` |
| Feedback por texto | Solo se interpreta si hay una revisión esperando instrucciones |
| Seguridad (§4) | Secreto de webhook verificado, `chat_id` en lista blanca, callback ligado a contenido+versión |
| Dry-run respetado | Aprobar con `AUTENIA_PUBLISH_DRY_RUN=true` **nunca** publica |

### Fase 5 — Publicación

| Entregable | Criterio |
|---|---|
| Solo versión aprobada | Publicar una versión mutada tras aprobación es imposible por diseño |
| Idempotencia | Clave por (contenido, versión, plataforma). Reintento no duplica. Test explícito |
| Aislamiento por plataforma | Fallo en una no oculta ni revierte los éxitos de las otras |
| Registro | Petición y respuesta saneadas, id externo y estado por plataforma |
| Pruebas simuladas | 2xx, 4xx, 5xx y timeout cubiertos sin envío real |

### Fase 6 — Despliegue

| Entregable | Criterio |
|---|---|
| Reparto de carga | VPS: coordinador, BD, Telegram, cola, publicación. Trabajo pesado (Whisper/YOLO/render) fuera si el VPS no da |
| Auditoría previa | CPU, RAM, disco y arquitectura verificados antes de desplegar |
| Compose limpio | Sin contenedores sin consumidor real. `docker compose config` válido |
| Producción | Healthchecks, proxy HTTPS, copias de seguridad, límites de disco. Puertos de desarrollo no expuestos |

---

## 2. Configuración

Todas las variables se leen desde `.env` del servidor o el gestor de secretos. **Nunca
desde el navegador. Nunca impresas en logs.**

### Requeridas

| Variable | Fase | Notas |
|---|---|---|
| `GEMINI_API_KEY` | 2 | Guion, investigación, títulos. Tier gratuito suficiente |
| `ELEVENLABS_API_KEY` | 3 | Voz en español |
| `AUTENIA_DB_PATH` | 1 | Ruta del SQLite. Volumen persistente privado |
| `AUTENIA_PUBLISH_DRY_RUN` | 5 | **Por defecto `true`.** Cambiar solo con autorización explícita |

### Requeridas por fase

| Variable | Fase | Notas |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 4 | Bot nuevo de @BotFather. No reutilices el de `cloud/alerts.py` |
| `TELEGRAM_CHAT_ID` | 4 | Lista blanca. Un solo chat autorizado |
| `TELEGRAM_WEBHOOK_SECRET` | 4 | Verificación de firma del webhook |
| `UPLOAD_POST_API_KEY` | 5 | 10 subidas/mes gratis |
| `FAL_KEY` | opcional | **Solo** para experimentos de avatar. Faceless no la necesita |

### Límites

| Variable | Por defecto | Notas |
|---|---|---|
| `AUTENIA_MAX_COST_PER_VIDEO` | `0.50` (€) | Parada dura antes de llamar a proveedor de vídeo |
| `AUTENIA_MAX_COST_PER_MONTH` | `25.00` (€) | Parada dura acumulada |
| `AUTENIA_MAX_DURATION_S` | `55` | Máximo interno |
| `AUTENIA_TZ` | `Europe/Madrid` | No programar sin horarios confirmados |

**Prohibido:** `BILLING_ENABLED`. Debe permanecer sin definir.

---

## 3. Máquina de estados

Un **contenido** tiene N **versiones**. El estado vive en la versión; el contenido
apunta a la versión vigente.

```
                  ┌──────────────┐
                  │  candidato   │  idea puntuada, sin gasto
                  └──────┬───────┘
                         │ supera umbrales del preflight
                  ┌──────▼───────┐
                  │   guion      │  texto + fuentes, sin gasto de vídeo
                  └──────┬───────┘
                         │ preflight OK (duración, derechos, coste)
                  ┌──────▼───────┐
          ┌───────│ renderizando │  ÚNICO estado que gasta
          │       └──────┬───────┘
          │ error        │ render OK
    ┌─────▼─────┐ ┌──────▼───────┐
    │  fallido  │ │ en_revision  │──────┐ enviado a Telegram
    └───────────┘ └──────┬───────┘      │
                         │ Aprobar      │ Pedir cambios / Regenerar
                  ┌──────▼───────┐      │
                  │  aprobado    │      └──> nueva versión en `guion`
                  └──────┬───────┘           (la anterior queda `descartado`)
                         │ publicar (si dry_run=false)
                  ┌──────▼───────┐
                  │  publicado   │  terminal, inmutable
                  └──────────────┘

    Rechazar desde en_revision ──> `descartado` (terminal)
```

### Reglas

- **Inmutabilidad tras aprobación.** Una versión en `aprobado` o `publicado` no
  admite mutación. Todo cambio crea versión nueva.
- **`renderizando` es el único estado que gasta.** Comprueba límites de coste al
  entrar, nunca después.
- **Una versión activa por contenido.** Rechaza entrar en `renderizando` si otra
  versión del mismo contenido ya está ahí (evita gasto duplicado por doble clic).
- **`fallido` conserva el coste consumido.** Un intento fallido cuenta.
- **Regenerar preserva lo reutilizable.** Guion, voz, imágenes y segmentos no
  afectados por el feedback se heredan por hash.
- **Transiciones ilegales lanzan error**, no se ignoran en silencio.

### Concurrencia

- Un solo trabajador de render. No paralelices generación de vídeo en el MVP.
- Callbacks de Telegram idempotentes: el mismo `callback_id` procesado dos veces
  produce un solo efecto.
- Publicación con clave idempotente `(contenido_id, version_id, plataforma)`.

---

## 4. Seguridad

**Secretos**
- Solo en `.env` del servidor o gestor de secretos. Nunca en el repo, el navegador,
  logs, mensajes de Telegram ni respuestas de API.
- Al registrar peticiones y respuestas, sanea: enmascara cabeceras `Authorization`,
  `xi-api-key`, `Key ...` y cualquier campo que contenga `key`, `token` o `secret`.
- Si una clave real estuvo en `localStorage`, **rótala**. No la migres.

**Webhook de Telegram**
- Verifica `X-Telegram-Bot-Api-Secret-Token` contra `TELEGRAM_WEBHOOK_SECRET`.
- Rechaza cualquier `chat_id` que no sea el autorizado, sin responder detalles.
- Valida que el `callback_data` corresponde a un contenido y versión existentes y en
  estado compatible. Un callback de una versión ya resuelta se ignora.
- Enlaces de vídeo: temporales y firmados. Nunca URL pública permanente.

**Superficie**
- Autenticación delante de toda interfaz desplegada.
- CORS restringido a los orígenes propios.
- Volúmenes persistentes privados, no servidos por el proxy.
- Sin puertos de desarrollo expuestos.

**Contenido**
- Ningún dato de cliente sin autorización escrita (brief §6).
- Derechos de uso verificados en el preflight, no después de renderizar.

---

## 5. Verificación

Ejecuta de lo específico a lo general:

```bash
pytest tests/ -k <lo_que_tocaste>     # primero lo tuyo
pytest tests/                          # 19 archivos existentes, no los rompas
cd dashboard && npm run build && npm run lint   # lint con --max-warnings 0
docker compose config                  # antes de desplegar
ffprobe -v error -show_streams <render_de_humo.mp4>
```

Cobertura obligatoria de la lógica nueva:

- Resolución de secretos sin exponerlos.
- Rutas de galería denegadas.
- Cada exclusión editorial del brief §6.
- Cálculo de coste, límites y caché por hash.
- Transiciones de estado legales e ilegales.
- Callbacks de Telegram: firma, `chat_id`, idempotencia.
- Publicación simulada: 2xx, 4xx, 5xx, timeout.

---

## 6. Definición de terminado

Una fase está terminada cuando **todos** sus criterios se cumplen y el resumen final
declara:

- Qué corte vertical quedó operativo.
- Archivos y contratos modificados.
- Comandos de desarrollo y despliegue.
- Variables requeridas, **sin sus valores**.
- Pruebas ejecutadas y su resultado real.
- Qué no se pudo ejecutar y por qué.
- Coste esperado por vídeo y por 30 vídeos.
- Pasos manuales pendientes (Telegram, redes).
- Cómo volver a dry-run o detener el programador.

**No presentes una fase como terminada si solo compila.** Si un criterio no se
cumplió, dilo explícitamente en vez de omitirlo.
