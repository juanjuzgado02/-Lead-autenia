---
name: autenia-viral-shorts
description: Adapta, implementa, audita, prueba y despliega el repositorio OpenShorts de Autenia como herramienta interna para descubrir temas, crear vídeos verticales diarios en español, revisarlos por Telegram y publicarlos con aprobación humana en Instagram Reels, TikTok y YouTube Shorts. Úsala al trabajar sobre juanjuzgado02/-Lead-autenia, especialmente su rama dev, o sobre una copia derivada para Autenia. Incluye estrategia editorial, privacidad, costes, secretos en .env, Docker, VPS, estados de aprobación, publicación idempotente y analítica. No la uses para construir un SaaS multiempresa o dar acceso a clientes; eso requiere otro repositorio, otra skill y revisar la licencia de cloud/.
---

# Autenia Viral Shorts

## Objetivo

Convierte la base existente en un sistema interno, privado y económico que produzca
un vídeo útil al día para dar visibilidad a Autenia y captar conversaciones
comerciales. Optimiza probabilidad de retención, compartidos y leads; nunca
prometas viralidad.

Trabaja de forma incremental sobre OpenShorts. No reconstruyas el producto desde
cero ni amplíes el alcance hacia un SaaS multiusuario.

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
5. Revisa la licencia raíz y `cloud/LICENSE`. Mantén `cloud/` fuera del trabajo
   salvo petición explícita y licencia compatible.
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

### 1. Asegurar y simplificar la base — ✅ hecho (2026-07-29), salvo autenticación

Ya existe `core_config.py` en la raíz: resuelve todas las claves desde el `.env`
del servidor, valida al arrancar, enmascara secretos (`mask`, `sanitize`) y se
niega a arrancar con `BILLING_ENABLED`. La bandera `AUTENIA_INTERNAL_MODE`
(por defecto **on**) ignora las cabeceras `X-*-Key`, cierra las cuatro rutas de
galería con 404, bloquea la subida a galería y restringe CORS. El dashboard ya no
guarda claves y purga las que dejara una build anterior. `CLAUDE.md` y
`.env.example` documentan el modelo correcto.

Al trabajar sobre esta base:

- **Respeta `AUTENIA_INTERNAL_MODE`.** Toda superficie pública nueva debe llamar a
  `deny_if_internal()` como primera sentencia, y toda clave nueva debe resolverse
  en `core_config.py`, nunca desde una cabecera.
- **Nunca importes desde `cloud/`** ni enciendas `BILLING_ENABLED`.
- **Nunca registres un secreto.** Pasa peticiones y respuestas por
  `core_config.sanitize()` antes de persistirlas o imprimirlas.
- Separa la lógica nueva de `app.py` (3.797 líneas) en módulos pequeños. Conserva
  adaptadores finos en las rutas existentes cuando ayuden a no romper el producto.

**Pendiente de esta fase:** autenticación delante de toda interfaz desplegada y
volúmenes persistentes privados. No es bloqueante en local; sí antes del VPS.

Para esa autenticación: **un único usuario, una única credencial**. Basta un token
compartido o HTTP basic sobre HTTPS, leído de `core_config.py`. No construyas
registro, roles, organizaciones ni recuperación de contraseña — eso es la
ampliación a SaaS que esta skill prohíbe.

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
6. Ejecuta un preflight de duración, afirmaciones, derechos de uso, coste estimado
   y recursos en caché.
7. Renderiza solo cuando el candidato supere los umbrales.

No copies titulares o vídeos de terceros como sustituto de una pieza propia.
Parafrasea, atribuye y usa capturas o clips únicamente cuando el derecho de uso lo
permita.

### 3. Renderizar el formato correcto

- Usa por defecto 25–35 segundos; permite 20–45 según la idea y aplica un máximo
  interno de 55 segundos. No alargues contenido para llegar a una cifra.
- Exporta 1080×1920, H.264, AAC y 30 fps salvo que el material fuente justifique
  otra cadencia.
- Muestra el hook en el primer segundo, entrega la idea central en los primeros tres
  y evita una intro corporativa.
- Usa subtítulos legibles, zonas seguras y cambios visuales con propósito. No
  satures la pantalla.
- Inserta logo y CTA discretos; evita marcas de agua de otras plataformas.
- Corrige el prompt rígido de `saasshorts.py:444-537`: elimina la exigencia de
  aspecto europeo (`:534`), el CTA fijo "link in bio" (`:530`), el número fijo de
  escenas y las duraciones contradictorias.
- `viral_hook_text` sí llega a la composición (`main.py:72` → `hooks.py`). Verifica
  que sigue surtiendo efecto tras tus cambios; no lo conviertas en dato decorativo.
- Reutiliza guion, voz, imágenes y segmentos sin cambios durante una iteración.
  Regenera solo lo afectado por el feedback.

### 4. Añadir revisión rápida por Telegram

- Persiste cada contenido y versión antes de enviar la revisión. Usa un almacén
  **propio y separado** de `cloud/` (SQLite vía SQLAlchemy async + aiosqlite).
  `cloud/database.py` es solo-Postgres (ejecuta `CREATE EXTENSION citext` en `:31`)
  y está dormido con `BILLING_ENABLED` off — no lo reutilices ni lo despiertes.
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
- El Telegram existente (`cloud/alerts.py`) es solo aviso unidireccional bajo
  licencia comercial. Construye el bot de aprobación como componente nuevo.

Usa la máquina de estados y las reglas de concurrencia de
`implementation-contract.md`.

### 5. Publicar con seguridad

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
- Ejecuta Whisper, YOLO o render pesado en el ordenador o en un servidor
  dimensionado; no asumas que un VPS pequeño soporta la pila completa.
- Audita CPU, RAM, disco y arquitectura antes del despliegue. Ajusta servicios de
  Compose y elimina del MVP los contenedores sin consumidor real — hoy hay tres
  (`backend`, `frontend`, `renderer`); `renderer` (Remotion) probablemente no
  participe en el MVP faceless.
- Usa imágenes de producción, healthchecks, proxy HTTPS, copias de seguridad y
  límites de disco. No expongas directamente los puertos de desarrollo.

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

- Compilación Python y pruebas unitarias (`pytest tests/`, 19 archivos existentes).
- Build y lint del dashboard (`npm run build`, `npm run lint` con `--max-warnings 0`).
- Pruebas de resolución de secretos sin exponerlos.
- Pruebas de galería denegada.
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
OAuth/perfiles sociales por cliente, roles, cuotas, facturación y auditoría; revisa
antes la licencia de `cloud/`.
