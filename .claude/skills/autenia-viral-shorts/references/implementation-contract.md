# Contrato de implementación

Define fases, configuración, estados, seguridad y criterios de aceptación. Si algo
aquí choca con el código actual, gana este documento y se actualiza
`repository-audit.md`.

---

## 1. Fases y criterios de aceptación

Cada fase es un corte vertical entregable. **No empieces una fase sin que la anterior
cumpla sus criterios.**

### Fase 0 — Base segura (sin coste de API) — ✅ hecha

| Entregable | Criterio de aceptación | Estado |
|---|---|---|
| `core_config.py` en la raíz | Arranque falla con mensaje claro si falta una variable requerida. Ningún valor secreto aparece en logs | ✅ |
| `CLAUDE.md` actualizado | La sección de claves refleja el modelo server-side. Sin contradicción con la skill | ✅ |
| Superficie pública cerrada | Ninguna ruta pública alcanzable | ✅ **por eliminación** (2026-07-30): no hay servidor HTTP |
| Claves fuera del navegador | Ninguna clave viaja al cliente ni se guarda en él | ✅ no hay cliente |
| Autenticación delante de la UI | Ninguna interfaz desplegada accesible sin credencial | ✅ **ya no aplica**: no hay UI. El control de acceso es el `chat_id` autorizado de Telegram |

Las tres últimas filas se cerraron borrando el panel de React y `app.py`, no
configurándolos. Es más fuerte: una puerta que no existe no se puede dejar abierta
por un despliegue mal configurado. Si alguna vez vuelve una superficie HTTP,
vuelven también estos criterios y, con ellos, **un usuario y una credencial** —
nunca registro, roles ni organizaciones.

### Fase 1 — Almacén y estados — ✅ hecha (2026-07-29)

| Entregable | Criterio | Estado |
|---|---|---|
| SQLite propio (SQLAlchemy async + aiosqlite) | Independiente de `cloud/`. Esquema versionado. Contenido y versión persisten antes de cualquier gasto | ✅ `autenia/models.py`, `autenia/store.py` |
| Máquina de estados (§3) | Transiciones ilegales rechazadas con test | ✅ `autenia/states.py`; una prueba recorre el grafo y demuestra que no hay ruta a `publicado` sin pasar por `en_revision` |
| Registro de coste | Coste estimado y real por proveedor, contenido y versión. Un intento fallido cuenta como coste consumido | ✅ Céntimos enteros, no float |

Detalles que conviene no re-descubrir: el gasto duplicado se bloquea con un
índice único parcial además del chequeo en código, y el presupuesto mensual se
comprueba **al entrar** en `renderizando`.

### Fase 2 — Motor editorial (coste ínfimo) — ✅ núcleo hecho (2026-07-29)

| Entregable | Criterio | Estado |
|---|---|---|
| Recolección de candidatos | Persiste título, URL canónica, editor, fecha de publicación, fecha de consulta y fragmentos factuales | ✅ `autenia/sources.py`, probado con búsqueda real |
| Filtros duros | Descarta duplicados, política/polémica y piezas sin relación demostrable con Autenia. Test por cada exclusión del brief §6 | ✅ `autenia/editorial.py`, una prueba por exclusión |
| Puntuación barata | Actualidad, dolor, encaje, hook, evidencia, potencial visual, conversión, coste. Sin llamadas caras | ✅ Señales deterministas gratis; las de juicio, en **una sola** llamada para todo el lote |
| Guion fundamentado | Separa hecho de opinión y conserva fuentes | ✅ `autenia/gemini.py` con esquema JSON forzado por la API |
| Preflight | Verifica duración, afirmaciones, derechos de uso, coste estimado y caché **antes** de renderizar | ✅ `autenia/preflight.py`; devuelve todos los problemas juntos, no el primero |

Sobre la recolección, medido el 2026-07-29:

- **Búsqueda y salida estructurada son incompatibles.** `google_search` con
  `responseMimeType: application/json` devuelve 400 (`Tool use with a response
  mime type ... is unsupported`). Hacen falta dos llamadas: búsqueda en prosa y
  extracción estructurada después.
- **Las URLs de grounding son redirecciones** de
  `vertexaisearch.cloud.google.com`, no direcciones canónicas. Hay que
  resolverlas siguiendo el redirect; una fuente que no se puede citar por su URL
  real no es una fuente.
- **El modelo se inventa URLs si le dejas.** La extracción solo puede usar las
  URLs que ya resolvimos; cualquier otra se descarta.
- **Busca el problema, no la tecnología.** Los ángulos tecnológicos ("agentes de
  IA para empresas") devolvieron autopromoción de una consultora, regulación del
  NIST y un lanzamiento de Oracle: todo puntuado cerca de cero, correctamente.
  Cambiados a ángulos de dolor y evidencia ("estudio horas perdidas en tareas
  administrativas"), la misma búsqueda encontró un dato usable —introducir un
  pedido a mano consume 20-30 minutos— que puntuó 1,00 en dolor, visual y
  conversión. **Si el sistema da días en blanco seguidos, revisa los ángulos
  antes de tocar el umbral.** Bajar el listón es lo que el brief prohíbe.

Sobre el guion, aprendido al probarlo contra la API real:

- **Pide palabras, no segundos.** El modelo estima fatal la duración hablada:
  con "30 segundos" escribió 140 palabras (54 s). Con un presupuesto de palabras
  explícito, 68 palabras → 27,2 s reales. La conversión 2,6 palabras/segundo
  falla un 4%.
- **Toda cifra es un hecho.** El modelo marcaba como "opinion" las cifras
  derivadas ("la mitad de ocho son cuatro"). El preflight lo cazó; el prompt
  ahora lo prohíbe explícitamente.
- **El hook se repetía como escena 1**, y el vídeo habría dicho la misma frase
  dos veces. El preflight no ve esto: hay que pedirlo en el prompt.
- **Los planos visuales se van a animaciones y metáforas** ("un reloj girando",
  "un empleado frustrado") si no se exige captura de pantalla concreta.

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

### Fase 6 — Ciclo diario automático

| Entregable | Criterio |
|---|---|
| Programador | Un disparo al día en `AUTENIA_TZ`. No arranca sin `AUTENIA_PUBLISH_TIME` ni con `AUTENIA_SCHEDULER_ENABLED=false` |
| Exclusión mutua | Si hay un ciclo en revisión o renderizando, el nuevo se salta y se registra el motivo. Test explícito |
| Día sin candidato | Ningún candidato supera los umbrales → avisa por Telegram y termina **sin producir**. No rellena |
| Límite mensual | Comprobado antes de empezar el ciclo, no a mitad |
| Registro | Cada ejecución (lanzada, saltada, fallida) queda con su motivo |
| Parada | `AUTENIA_SCHEDULER_ENABLED=false` detiene el ciclo y se dice en el resumen |

### Fase 7 — Despliegue

| Entregable | Criterio |
|---|---|
| Reparto de carga | Todo en el VPS. Desde el recorte del 2026-07-30 la pila son cinco paquetes de Python más ffmpeg: ya no hay Whisper, YOLO ni torch que justifiquen partirla |
| Auditoría previa | CPU, RAM, disco y arquitectura verificados antes de desplegar. El render con ffmpeg sigue siendo lo más caro |
| Compose limpio | Un solo servicio (`bot`). Sin contenedores sin consumidor real. `docker compose config` válido |
| Producción | Healthchecks, copias de seguridad, límites de disco. Sin proxy HTTPS: el long polling no abre puertos |

---

## 2. Configuración

Todas las variables se leen desde `.env` del servidor o el gestor de secretos. **Nunca
desde el navegador. Nunca impresas en logs.**

### Requeridas

| Variable | Fase | Notas |
|---|---|---|
| `GEMINI_API_KEY` | 2 | Guion, investigación, títulos **y voz por defecto**. Tier gratuito suficiente |
| `ELEVENLABS_API_KEY` | opcional | Solo si `AUTENIA_VOICE_PROVIDER=elevenlabs`. La voz por defecto es Gemini TTS, verificada en español y gratuita |
| `AUTENIA_DB_PATH` | 1 | Ruta del SQLite. Volumen persistente privado |
| `AUTENIA_PUBLISH_DRY_RUN` | 5 | **Por defecto `true`.** Cambiar solo con autorización explícita |

### Requeridas por fase

| Variable | Fase | Notas |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 4 | Bot nuevo de @BotFather |
| `TELEGRAM_CHAT_ID` | 4 | Lista blanca. Un solo chat autorizado |
| `TELEGRAM_WEBHOOK_SECRET` | 4 | **Solo con webhook.** El MVP usa long polling y no la necesita |
| `UPLOAD_POST_API_KEY` | 5 | 10 subidas/mes gratis |
| `UPLOAD_POST_USER` | 5 | El perfil de Upload-Post con las tres cuentas conectadas. Sin él la subida falla con un mensaje que no explica por qué |
| `FAL_KEY` | opcional | **Solo** para experimentos de avatar. Faceless no la necesita |

### Modo

| Variable | Por defecto | Notas |
|---|---|---|
| `AUTENIA_INTERNAL_MODE` | `true` | Declara despliegue privado monousuario. Desde el recorte del 2026-07-30 **no protege nada**: no hay superficie HTTP que cerrar. `AUTENIA_ALLOWED_ORIGINS` y `VITE_AUTENIA_INTERNAL_MODE` se eliminaron con el panel |

### Límites

| Variable | Por defecto | Notas |
|---|---|---|
| `AUTENIA_MAX_COST_PER_VIDEO` | `0.50` (€) | Parada dura antes de llamar a proveedor de vídeo |
| `AUTENIA_MAX_COST_PER_MONTH` | `25.00` (€) | Parada dura acumulada |
| `AUTENIA_MAX_DURATION_S` | `55` | Máximo interno |
| `AUTENIA_TZ` | `Europe/Madrid` | No programar sin horarios confirmados |

### Ciclo diario (fase 6)

| Variable | Por defecto | Notas |
|---|---|---|
| `AUTENIA_SCHEDULER_ENABLED` | `false` | Interruptor de parada. Actívalo solo con el ciclo manual ya probado |
| `AUTENIA_PUBLISH_TIME` | sin definir | Hora local del disparo diario. Sin ella no se programa nada |
| `AUTENIA_AUTO_APPROVE_AFTER` | `0` | Aprobaciones seguidas sin cambios antes de publicar sin botón. `0` = siempre humano |

**Prohibido:** `BILLING_ENABLED`. Debe permanecer sin definir.

---

## 3. Máquina de estados

Un **contenido** tiene N **versiones**. El estado vive en la versión; el contenido
apunta a la versión vigente.

**La revisión ocurre sobre el guion, antes de renderizar** (decidido por el
usuario el 2026-07-29). Un tema rechazado no cuesta nada, y las correcciones
llegan como texto editable en vez de como quejas sobre un vídeo ya hecho.
Aprobar es aprobar las palabras: renderizar y publicar van solos después.

```
                  ┌──────────────┐
                  │  candidato   │  idea puntuada, sin gasto
                  └──────┬───────┘
                         │ supera el umbral editorial
                  ┌──────▼───────┐
                  │   guion      │  texto + fuentes, sin gasto
                  └──────┬───────┘
                         │ preflight OK (duración, afirmaciones, coste)
                  ┌──────▼───────┐
                  │ en_revision  │──────┐ guion enviado a Telegram
                  └──────┬───────┘      │
                         │ Aprobar      │ Pedir cambios (texto del usuario)
                  ┌──────▼───────┐      │
                  │  aprobado    │      └──> nueva versión en `guion`
                  └──────┬───────┘           (la anterior queda `descartado`)
                         │
                  ┌──────▼───────┐
          ┌───────│ renderizando │  ÚNICO estado que gasta
          │       └──────┬───────┘
          │ error        │ render OK y publicar (si dry_run=false)
    ┌─────▼─────┐ ┌──────▼───────┐
    │  fallido  │ │  publicado   │  terminal
    └───────────┘ └──────────────┘

    Rechazar desde en_revision ──> `descartado` (terminal)
```

**El vídeo se publica sin que nadie lo haya visto.** Es una decisión consciente
del usuario: aprueba el guion y el resto va solo. Si algún día quiere una
ventana de veto sobre el vídeo terminado, se añade como paso opcional; no la
introduzcas por tu cuenta.

### Reglas

- **Inmutabilidad del guion tras aprobación.** Desde `aprobado` en adelante las
  palabras no cambian; toda reescritura crea versión nueva que vuelve a
  revisión. El render sí adjunta su salida a la versión aprobada: registrar el
  vídeo producido no es modificar lo aprobado.
- **`renderizando` es el único estado que gasta.** Comprueba límites de coste al
  entrar, nunca después.
- **Una versión activa por contenido.** Rechaza entrar en `renderizando` si otra
  versión del mismo contenido ya está ahí (evita gasto duplicado por doble clic).
- **`fallido` conserva el coste consumido.** Un intento fallido cuenta.
- **Regenerar preserva lo reutilizable.** Guion, voz, imágenes y segmentos no
  afectados por el feedback se heredan por hash.
- **Transiciones ilegales lanzan error**, no se ignoran en silencio.
- **La autoaprobación no salta estados.** Con `AUTENIA_AUTO_APPROVE_AFTER > 0` y la
  racha cumplida, la versión sigue pasando por `en_revision` y por la ventana de
  veto; solo el origen de la transición a `aprobado` cambia (temporizador en vez de
  callback). Regístralo como aprobación automática, nunca como humana. Los frenos
  duros —exclusiones del brief §6, límites de coste, derechos de uso— se evalúan
  igual y siguen pudiendo mandar la versión a `descartado`.

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
pytest tests/                          # 200 pruebas en ~3 s, no las rompas
docker compose config                  # antes de desplegar
ffprobe -v error -show_streams <render_de_humo.mp4>   # exige ffmpeg o Docker
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
