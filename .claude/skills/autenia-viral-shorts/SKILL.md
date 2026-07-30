---
name: autenia-viral-shorts
description: Implementa, audita, prueba y despliega la herramienta interna de Autenia para descubrir temas, crear vídeos verticales diarios en español, revisarlos por Telegram y publicarlos con aprobación humana en Instagram Reels, TikTok y YouTube Shorts. Úsala al trabajar sobre juanjuzgado02/-Lead-autenia, especialmente su rama dev, o sobre una copia derivada para Autenia. Incluye estrategia editorial, privacidad, costes, secretos en .env, Docker, VPS, estados de aprobación, publicación idempotente y analítica. No la uses para construir un SaaS multiempresa o dar acceso a clientes; eso es otro producto, otro repositorio y otra skill.
---

# Autenia Viral Shorts

## Objetivo

Convierte la base existente en un sistema interno, privado y económico que produzca
un vídeo útil al día para dar visibilidad a Autenia y captar conversaciones
comerciales. Optimiza probabilidad de retención, compartidos y leads; nunca
prometas viralidad.

Trabaja de forma incremental sobre lo que ya existe en `autenia/`. No amplíes el
alcance hacia un SaaS multiusuario.

**El repositorio se recortó el 2026-07-30.** Nació como fork de OpenShorts, que
recortaba vídeos largos de YouTube; todo lo que servía solo a aquel producto —el
panel de React, `cloud/` con su facturación, `app.py`, `main.py`, `saasshorts.py`,
Whisper, YOLO, MediaPipe, yt-dlp, Remotion— se eliminó, no se desactivó. Si una
instrucción menciona un archivo que no encuentras, esa es la razón: está en el
historial de git, no en el árbol. No lo restaures sin un motivo que nombre este
producto.

## Qué significa "se hace solo"

El usuario pide un short al día publicado sin intervención. El sistema terminado
hace todo esto sin que nadie lo toque:

```
el programador despierta → recoge candidatos → filtra y puntúa → elige uno
→ escribe guion con fuentes → preflight → envía el GUION a Telegram
                    ↓
   el usuario aprueba, o edita el texto     ← único paso humano, ~10 segundos
                    ↓
       renderiza 9:16 → publica en TikTok + Instagram + YouTube
```

**Se revisa el guion, no el vídeo.** Un tema rechazado no cuesta nada y las
correcciones llegan como texto. Aprobar es aprobar las palabras: a partir de ahí
va solo, y el vídeo se publica sin que nadie lo haya visto. Es decisión
consciente del usuario (2026-07-29); no metas una ventana de veto por tu cuenta.

Ese paso de aprobación es deliberado: el contenido lleva la marca de Autenia, y
una cifra inventada o un dato de cliente se publican una sola vez. **No lo
elimines.**

Si el usuario pide autonomía total, no discutas: propón autonomía **ganada**. El
sistema publica solo tras N aprobaciones seguidas sin cambios (por defecto 20), y
aun así conserva los frenos duros que no se desactivan nunca —exclusiones del
brief §6, límite de coste, solo material autorizado— más una ventana de veto:
avisa y publica si nadie responde en 30 minutos. Configúralo con
`AUTENIA_AUTO_APPROVE_AFTER` (0 = desactivado, el valor por defecto).

## Cómo trabajar con este usuario

Aprendido en sesión, no lo reaprendas por las malas:

- **Entrega, no expliques.** Prefiere ver trabajo terminado a leer un análisis de
  lo que se podría hacer. Resúmenes cortos; el detalle, en los archivos.
- **No preguntes lo que puedes asumir.** Adopta el valor por defecto del brief,
  decláralo como supuesto en el resumen final y sigue. Solo bloquea por lo que no
  puedes obtener tú: credenciales, cuentas sociales, material visual, specs del
  VPS.
- **Empieza por lo que no depende de él.** Siempre hay fase que avanzar sin
  credenciales; hazla mientras consigue las suyas en vez de esperar.
- **No repitas lo ya dicho.** Si una decisión está en el brief o en una sesión
  anterior, es una decisión, no un tema abierto.

## Cargar el contexto correcto

Lee siempre:

- [references/autenia-content-brief.md](references/autenia-content-brief.md) para
  marca, público, formatos, duración, contenidos y costes.
- [references/repository-audit.md](references/repository-audit.md) para
  arquitectura, deuda y rutas concretas del código revisado.
- [references/implementation-contract.md](references/implementation-contract.md)
  para fases, configuración, estados, seguridad y criterios de aceptación.

El logo de Autenia no está versionado en esta skill. Cuando una tarea necesite
el logo, pídelo al usuario o extráelo de https://auteniaai.com/. **No lo redibujes
a partir de memoria** ni generes un sustituto aproximado.

## Confirmar el alcance antes de editar

1. Lee `CLAUDE.md`, `README.md`, instrucciones del repositorio y cualquier `AGENTS.md`.
2. Ejecuta `git status --short`, identifica rama y `HEAD`, e inventaría los cambios
   ajenos. No sobrescribas trabajo del usuario.
3. Compara el código actual con el commit auditado en `repository-audit.md`. Si ha
   cambiado, reaudita únicamente los flujos afectados y actualiza las conclusiones.
4. Comprueba herramientas disponibles, variables ya definidas, Docker, FFmpeg, Node
   y Python. Nunca imprimas valores secretos.
5. El paquete `cloud/` (facturación multiempresa) ya no está en el repositorio.
   No lo restaures.
6. Presenta un plan por fases y empieza por el corte vertical mínimo. No conviertas
   toda la aplicación en una sola intervención.

No vuelvas a preguntar decisiones ya fijadas en el brief. Los bloqueadores reales
—lo único que el usuario tiene que aportar— son exactamente cinco:

1. Las cuatro claves: Gemini, ElevenLabs, bot de Telegram, Upload-Post.
2. Las cuentas de TikTok, Instagram y YouTube conectadas en Upload-Post.
3. Material visual propio: capturas o grabaciones de agentes, cuadros de mando y
   automatizaciones, aunque estén anonimizadas. **Sin esto el faceless degenera en
   stock genérico** y pierde lo que diferencia a Autenia. Es lo único que no se
   resuelve con dinero ni con código.
4. El logo en PNG con transparencia.
5. Un servidor encendido 24/7, con sus specs. Si el programador vive en el
   portátil, "se hace solo" solo mientras el portátil esté abierto.

Todo lo demás tiene valor por defecto. Úsalo y decláralo.

## Mantener estas decisiones de producto

- Opera como herramienta interna y monousuario de Autenia.
- Produce únicamente en español por ahora.
- Apunta a un vídeo diario, pero selecciona calidad antes que rellenar el calendario.
- Genera un máster 9:16 reutilizable en Instagram, TikTok y YouTube.
- Usa faceless con capturas, demostraciones, material autorizado, voz y subtítulos
  como formato predeterminado. **Este modo aún no existe en el código: hay que
  construirlo** (ver `repository-audit.md` §4).
- Usa actores distintos, no un personaje fijo, solo en experimentos de avatar donde
  aporten valor.
- Prioriza mensajes directos cualificados y el cuestionario breve de Autenia como
  conversión primaria. Trata guardados, compartidos, seguidores y comentarios como
  señales secundarias.
- Excluye política, polémica artificial, ataques, rumores, promesas engañosas y
  datos de clientes.
- Mantén la galería pública y toda subida pública desactivadas.
- Guarda todos los secretos solo en `.env` del servidor o en el gestor de secretos
  del despliegue.
- Exige aprobación humana antes de cualquier publicación.

## Implementar por cortes verticales

### 1. Asegurar y simplificar la base — ✅ hecho (2026-07-29 y 2026-07-30)

`core_config.py` resuelve todas las claves desde el `.env` del servidor, valida al
arrancar, enmascara secretos (`mask`, `sanitize`) y se niega a arrancar con
`BILLING_ENABLED`. `CLAUDE.md` y `.env.example` documentan el modelo correcto.

El 2026-07-30 se cerró la parte que faltaba, y por eliminación en vez de por
configuración: **ya no hay superficie HTTP**. Sin `app.py`, sin panel y sin
navegador no hay cabecera `X-*-Key` que ignorar, ni galería que devolver 404, ni
CORS que restringir — y por eso desapareció `allowed_origins`. `AUTENIA_INTERNAL_MODE`
sobrevive como declaración de intenciones, no como guardia.

Esto también cierra lo que quedaba pendiente: **la autenticación ya no hace falta**.
No se protege una interfaz que no existe. El único canal de entrada es Telegram, y
ahí manda un solo `chat_id`; cualquier otro se descarta sin contestar.

Al trabajar sobre esta base:

- **No añadas una superficie HTTP.** Si alguna vez hace falta, tendrá que
  comprobar `internal_mode` y llevar delante un único usuario y una única
  credencial leída de `core_config.py` — nunca registro, roles ni organizaciones.
- **Toda clave nueva se resuelve en `core_config.py`**, nunca desde una petición.
- **Nunca registres un secreto.** Pasa peticiones y respuestas por
  `core_config.sanitize()` antes de persistirlas o imprimirlas.
- **Nunca enciendas `BILLING_ENABLED`.** El paquete `cloud/` ya no está; el
  guardia de arranque se queda igualmente.

### 2. Motor editorial — ✅ núcleo hecho (2026-07-29)

Ya existen `autenia/editorial.py` (filtros duros y puntuación),
`autenia/gemini.py` (juicio, guion fundamentado y voz) y
`autenia/preflight.py` (la última puerta antes de gastar). Probado de extremo a
extremo contra la API real: candidato → filtros → juicio → guion → preflight →
voz, con 27 s de audio a coste cero.

`autenia/sources.py` busca con grounding de Google, resuelve las redirecciones a
URLs canónicas y descarta cualquier URL que el modelo no haya recibido de
nosotros. Probado de extremo a extremo: búsqueda real → candidato → guion → voz.

**Los ángulos de búsqueda apuntan al problema, no a la tecnología.** Es la
diferencia entre encontrar autopromoción de consultoras y encontrar un dato
usable. Si el sistema encadena días en blanco, revisa los ángulos antes de tocar
el umbral: bajar el listón es justo lo que el brief prohíbe.

La voz sale de `autenia/voice.py`, que abstrae el proveedor: Gemini TTS por
defecto (gratis, verificado en español) o ElevenLabs con
`AUTENIA_VOICE_PROVIDER=elevenlabs`. **No cablees un proveedor de voz**: es la
voz de la marca y la decisión se toma escuchando.

El flujo que implementan, para referencia:

1. Recoge candidatos recientes desde fuentes autorizadas.
2. Guarda título, URL canónica, editor, fecha de publicación, fecha de consulta y
   fragmentos factuales.
3. Descarta duplicados, asuntos políticos o polémicos y piezas sin relación
   demostrable con Autenia.
4. Puntúa con una operación barata: actualidad, dolor del cliente, encaje con
   servicios, fuerza del hook, evidencia, potencial visual, conversión y coste.
5. Selecciona una idea y crea un guion fundamentado. Diferencia hechos de opinión y
   conserva las fuentes.
6. Ejecuta un preflight de duración, afirmaciones, coste estimado y recursos en
   caché.
7. Renderiza solo cuando el candidato supere los umbrales.

No copies titulares ni vídeos ajenos como sustituto de una pieza propia:
parafrasea. Un dato afirmado lleva su fuente, y esa fuente solo la ves tú en
Telegram — nunca sale en el vídeo. Está para que el generador no invente cifras
que Autenia publicaría con su marca, no por cautela legal.

### 3. Renderizar el formato correcto — 🚧 en curso (2026-07-30)

Ya existe `autenia/render.py`: narra segmento a segmento con `autenia/voice.py`
(así cada escena dura exactamente lo que dura su frase, y el feedback sobre una
escena solo regenera esa), busca material propio con `autenia/assets.py`, quema
subtítulos dentro de la zona segura y compone 1080×1920 H.264/AAC a 30 fps.
`autenia/ffmpeg.py` normaliza el audio a −14 LUFS, que es el nivel al que TikTok,
Reels y Shorts igualan la reproducción: una narración floja no se queda floja, se
sube con su ruido de fondo y suena peor que las de al lado.

**El fondo se elige en tres niveles, y el orden no se negocia:** material propio
de `data/library`; si no hay, una fotografía generada con `autenia/images.py`; y
si eso falla, una tarjeta tipográfica. Las fotos llevan un travelling lento para
que no parezcan diapositivas.

Lo generado es un suplente mientras la biblioteca se llena, no un sustituto. Dos
reglas que lo mantienen honesto:

- **Una escena que pide "una pantalla de Autenia" se responde con el escritorio
  alrededor de la pantalla, nunca con una interfaz inventada.** Generar el
  producto y publicarlo como si existiera es lo que más avergonzaría a Autenia.
- **Ningún modelo de imagen escribe texto.** Todos los prompts prohíben letras,
  cifras y logotipos: el texto generado sale ilegible y se nota al instante. Las
  palabras reales las dibuja Pillow después.

Coste medido el 2026-07-30: unos 0,03 € por imagen, así que un short de cinco
escenas ronda 0,15 €. Por eso la caché en disco no es opcional.

**Pendiente:** pruebas de `cycle.py` y logo/CTA discretos.

- Usa por defecto 25–35 segundos; permite 20–45 según la idea y aplica un máximo
  interno de 55 segundos. No alargues contenido para llegar a una cifra.
- Exporta 1080×1920, H.264, AAC y 30 fps salvo que el material fuente justifique
  otra cadencia.
- Muestra el hook en el primer segundo, entrega la idea central en los primeros tres
  y evita una intro corporativa.
- Usa subtítulos legibles, zonas seguras y cambios visuales con propósito. No
  satures la pantalla.
- Inserta logo y CTA discretos; evita marcas de agua de otras plataformas.
- Reutiliza guion, voz, imágenes y segmentos sin cambios durante una iteración.
  Regenera solo lo afectado por el feedback.

### 4. Añadir revisión rápida por Telegram — ✅ hecho (2026-07-29)

Ya existen `autenia/store.py` y `autenia/states.py` (SQLite con SQLAlchemy async y
aiosqlite), `autenia/telegram.py` (long polling, un solo chat autorizado, callbacks
idempotentes atados a una versión) y `autenia_bot.py`. Las reglas de abajo siguen
valiendo para cualquier cambio.

- Persiste cada contenido y versión antes de enviar la revisión.
- Envía vídeo comprimido o enlace privado temporal, título, caption, fuentes, coste
  estimado y versión.
- Incluye botones `Aprobar`, `Pedir cambios`, `Rechazar` y `Regenerar`.
- Interpreta el siguiente mensaje de texto del chat autorizado como feedback solo
  cuando exista una revisión esperando instrucciones.
- Genera una nueva versión conservando la anterior y los recursos reutilizables.
- **Usa long polling, no webhook, en el MVP.** El webhook exige dominio público con
  certificado válido; el long polling funciona detrás de NAT, sin dominio y sin
  `TELEGRAM_WEBHOOK_SECRET`. Migra a webhook solo cuando exista dominio y el
  polling se quede corto. No conviertas el dominio en un bloqueador artificial.
- Verifica `chat_id` permitido y correspondencia entre callback, contenido y
  versión. Con webhook, además, la firma del secreto.
- Nunca publiques desde el callback de aprobación si el modo dry-run está activo.

Usa la máquina de estados y las reglas de concurrencia de
`implementation-contract.md`.

### 5. Publicar con seguridad — 🚧 medio hecho (2026-07-30)

Ya existe `autenia/publish.py`, extraído del `app.py` que se borró: una llamada a
Upload-Post **por red**, no una con las tres, porque la respuesta agregada del
proveedor hace que dos éxitos y un rechazo se lean igual que todo bien. Recorta
títulos y descripciones a los límites de cada red antes de subir, distingue un
timeout ("sin respuesta", ambiguo) de un rechazo, y `key_for(version_id, platform)`
da la clave idempotente. Probado con 2xx, 4xx, 5xx, timeout y fallo parcial.

**Pendiente:** persistir esas claves y las respuestas saneadas en el almacén. Hasta
que exista esa tabla, la clave se calcula pero no se consulta: un reintento tras
una caída a mitad de publicación no puede distinguir "ya subido" de "no subido".

- Publica únicamente una versión aprobada y sin mutaciones posteriores.
- Usa una clave idempotente por contenido, versión y plataforma.
- Persiste petición saneada, respuesta saneada, identificador externo y estado por
  plataforma.
- Mantén TikTok, Instagram y YouTube como destinos independientes: un fallo no debe
  ocultar dos éxitos.
- Empieza con `AUTENIA_PUBLISH_DRY_RUN=true` y prueba payloads sin envío real.
- No cambies a publicación real ni conectes cuentas externas sin autorización
  explícita del usuario.
- Programa en `Europe/Madrid` solo después de recibir horarios concretos.

### 6. Programar el ciclo diario

Es el corte que convierte la herramienta en "se hace solo". Hazlo **después** de
que el ciclo completo funcione lanzado a mano: automatizar un flujo que aún falla
solo multiplica los fallos.

- Un disparo al día en `AUTENIA_TZ`, con la hora en configuración. No programes
  hasta tener horario confirmado; hasta entonces, deja el ciclo solo manual.
- **Un ciclo activo como máximo.** Si el anterior sigue en revisión o renderizando,
  el nuevo no arranca: registra que se saltó y por qué. Nada de acumular vídeos sin
  aprobar ni de gastar dos veces.
- Comprueba el límite mensual de coste antes de empezar, no a mitad.
- Si el motor editorial no encuentra candidato que supere los umbrales, **no
  produzcas nada**. Avisa por Telegram y termina. El brief dice calidad antes que
  calendario: un día sin vídeo es un resultado válido, rellenar no.
- Registra cada ejecución —lanzada, saltada o fallida— con su motivo.
- Deja siempre una forma obvia de pararlo (`AUTENIA_SCHEDULER_ENABLED=false`) y
  dila en el resumen final.

### 7. Desplegar según recursos

- Desarrolla y prueba localmente.
- Mantén en el VPS el coordinador, base de datos, Telegram, cola y publicación para
  que el ordenador pueda apagarse.
- Usa APIs externas para generación pesada en el primer MVP.
- **La pila cabe en un VPS pequeño desde el 2026-07-30.** Al irse Whisper, YOLO,
  MediaPipe y torch, `requirements.txt` bajó de ~3 GB a cinco paquetes puros de
  Python; el render es ffmpeg, que un servidor modesto sí aguanta. Mide antes de
  prometerlo, pero ya no hay que partir el despliegue entre portátil y servidor.
- Compose tiene **un solo servicio** (`bot`), no tres. Los otros dos servían al
  panel y al renderizador de Remotion, que ya no existen. No añadas un servicio
  sin un consumidor real.
- Usa imágenes de producción, healthchecks, copias de seguridad y límites de
  disco. No hace falta proxy HTTPS: el long polling no abre ningún puerto.

## Controlar coste y calidad

- Estima coste antes de llamar a un proveedor de vídeo y detén el trabajo si supera
  el límite configurado.
- Trata segundos de vídeo generativo como principal unidad de coste; los tokens de
  texto suelen ser secundarios.
- Empieza en `faceless`; eleva a `lowcost_avatar` o `premium_avatar` solo por
  decisión consciente.
- Registra coste estimado y real por proveedor, contenido y versión.
- Considera un intento fallido como coste consumido.
- Evita búsquedas o regeneraciones pagadas duplicadas mediante hashes de entrada y
  caché.

## Verificar cada cambio

Ejecuta primero las pruebas más específicas y después:

- Compilación Python y pruebas unitarias (`pytest tests/`, 200 pruebas en ~3 s).
  Ya no hay frontend que construir ni lintar.
- Pruebas de resolución de secretos sin exponerlos.
- Pruebas de filtros editoriales, cálculo de coste y caché.
- Pruebas de estados, callbacks de Telegram y autorización de chat.
- Pruebas de idempotencia y publicación simulada con respuestas 2xx, 4xx, 5xx y
  timeout.
- Render corto de humo, inspección visual y `ffprobe`.
- `docker compose config` y healthchecks.

No presentes la tarea como terminada si solo compila. Informa qué se ejecutó, qué no
pudo ejecutarse, costes potenciales y riesgos pendientes.

## Entregar de forma útil

Resume al final:

- Qué corte vertical quedó operativo.
- Archivos y contratos modificados.
- Comandos de desarrollo y despliegue.
- Variables requeridas sin sus valores.
- Pruebas ejecutadas y resultados.
- Coste esperado por vídeo y por 30 vídeos.
- Pasos manuales pendientes para Telegram y redes.
- Cómo volver al modo dry-run o detener el programador.

Si el usuario pide que clientes entren con sus cuentas, detén la ampliación. Propón
una base separada con autenticación, organizaciones, aislamiento por tenant,
OAuth/perfiles sociales por cliente, roles, cuotas, facturación y auditoría. Este
repositorio ya no contiene nada de eso: el paquete `cloud/` que lo traía se
eliminó el 2026-07-30.
