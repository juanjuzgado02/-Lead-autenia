"""The review conversation.

Anyone can find a bot by username and message it, so most of these tests are
about refusing: refusing other chats, refusing stale callbacks, refusing to read
idle chatter as instructions.
"""

import pytest

from autenia import telegram
from autenia.telegram import Action, parse_update, review_text

AUTHORISED = "123456789"
STRANGER = "987654321"


@pytest.fixture(autouse=True)
def chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:abcdefghijklmnop")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", AUTHORISED)


def callback(data, chat_id=AUTHORISED, callback_id="cb1"):
    return {"callback_query": {
        "id": callback_id, "data": data,
        "message": {"message_id": 7, "chat": {"id": int(chat_id)}},
    }}


def message(text, chat_id=AUTHORISED):
    return {"message": {"message_id": 8, "chat": {"id": int(chat_id)}, "text": text}}


# -- authorisation ---------------------------------------------------------

def test_a_button_from_the_authorised_chat_is_accepted():
    action = parse_update(callback("aprobar:v1"))
    assert action == Action(kind="aprobar", version_id="v1", callback_id="cb1")


def test_a_button_from_any_other_chat_is_ignored():
    """Replying at all would confirm the bot is worth probing."""
    assert parse_update(callback("aprobar:v1", chat_id=STRANGER)) is None


def test_text_from_any_other_chat_is_ignored():
    assert parse_update(message("aprobar esto", chat_id=STRANGER),
                        awaiting={"v1"}) is None


def test_nothing_is_authorised_when_no_chat_is_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert parse_update(callback("aprobar:v1")) is None


# -- callback integrity ----------------------------------------------------

def test_the_callback_carries_the_version_it_refers_to():
    """A tap on yesterday's message must not approve today's script."""
    action = parse_update(callback("aprobar:version-de-ayer"))
    assert action.version_id == "version-de-ayer"


def test_an_unknown_action_is_ignored():
    assert parse_update(callback("borrar_todo:v1")) is None


def test_malformed_callback_data_is_ignored():
    assert parse_update(callback("aprobar")) is None
    assert parse_update(callback("")) is None


@pytest.mark.parametrize("kind", ["aprobar", "cambios", "rechazar", "regenerar"])
def test_every_button_parses(kind):
    assert parse_update(callback(f"{kind}:v1")).kind == kind


# -- free text -------------------------------------------------------------

def test_text_is_feedback_only_while_something_waits():
    action = parse_update(message("el hook es flojo"), awaiting={"v1"})
    assert action == Action(kind="texto", version_id="v1", text="el hook es flojo")


def test_idle_chatter_is_not_filed_as_instructions():
    """Nothing is waiting, so 'gracias' must not become the next brief.

    Se contesta —callar del todo hacía que el bot pareciera roto— pero con un
    tipo distinto: "ocioso" no toca ninguna versión ni encarga nada, que es lo
    que esta prueba defiende desde el principio.
    """
    for update in (message("gracias!"), message("gracias!")):
        action = parse_update(update, awaiting=set())
        assert action.kind == "ocioso"
        assert action.version_id is None


def test_an_empty_message_is_ignored():
    assert parse_update(message("   "), awaiting={"v1"}) is None


def test_a_photo_without_text_is_ignored():
    update = {"message": {"chat": {"id": int(AUTHORISED)}, "photo": [{"file_id": "x"}]}}
    assert parse_update(update, awaiting={"v1"}) is None


def test_an_edited_message_still_counts_as_feedback():
    update = {"edited_message": {"chat": {"id": int(AUTHORISED)},
                                 "text": "mejor así"}}
    action = parse_update(update, awaiting={"v1"})
    assert action.kind == "texto"


def test_updates_without_a_message_or_callback_are_ignored():
    assert parse_update({"update_id": 1}) is None
    assert parse_update({}) is None


# -- what the operator reads -----------------------------------------------

SCRIPT = {
    "titulo": "Automatiza tus pedidos",
    "hook": "¿Cansado de meter pedidos a mano?",
    "escenas": [
        {"narracion": "Cada pedido te consume 30 minutos.",
         "visual": "Captura de un formulario de ERP",
         "tipo": "hecho", "fuente": "https://ejemplo.es/informe"},
        {"narracion": "Es tiempo que no recuperas.",
         "visual": "Cuadro de mando", "tipo": "opinion"},
    ],
    "cta": "En la web hay un cuestionario de un minuto.",
}


def test_the_review_shows_what_the_operator_is_vouching_for():
    text = review_text(SCRIPT, version_number=2, estimated_seconds=28,
                       estimated_cents=0)
    assert "Automatiza tus pedidos" in text
    assert "¿Cansado de meter pedidos a mano?" in text
    assert "https://ejemplo.es/informe" in text   # the source of the claim
    assert "versión 2" in text
    assert "28s" in text


def test_facts_and_opinions_are_distinguishable_at_a_glance():
    text = review_text(SCRIPT)
    assert "📊" in text   # the sourced claim
    assert "💬" in text   # the opinion


def test_html_in_a_script_cannot_break_the_message():
    """An unescaped < would make Telegram reject the whole send."""
    text = review_text({**SCRIPT, "titulo": "Ahorra <b>4h</b> & más"})
    assert "&lt;b&gt;" in text
    assert "&amp;" in text


def test_low_coverage_is_flagged_as_something_to_improve():
    text = review_text(SCRIPT, coverage=0.2)
    assert "20%" in text
    assert "capturas" in text


def test_full_coverage_is_reported_without_a_nag():
    text = review_text(SCRIPT, coverage=1.0)
    assert "100%" in text
    assert "capturas mejorarían" not in text


def test_an_enormous_script_is_truncated_not_rejected():
    """Telegram refuses anything over 4096 characters outright."""
    huge = {**SCRIPT, "escenas": [
        {"narracion": "frase muy larga " * 40, "visual": "algo", "tipo": "opinion"}
        for _ in range(40)
    ]}
    assert len(review_text(huge)) <= telegram.MAX_MESSAGE


# -- the keyboard ----------------------------------------------------------

def test_every_button_is_bound_to_the_version():
    keyboard = telegram._keyboard("v-abc")
    data = [button["callback_data"]
            for row in keyboard["inline_keyboard"] for button in row]
    assert data == ["aprobar:v-abc", "cambios:v-abc",
                    "regenerar:v-abc", "rechazar:v-abc"]


# -- asking for a script by hand -------------------------------------------

@pytest.mark.parametrize("escrito", [
    "/guion la factura electrónica obligatoria",
    "/guión la factura electrónica obligatoria",
    "/tema la factura electrónica obligatoria",
    "tema: la factura electrónica obligatoria",
    "guion: la factura electrónica obligatoria",
    "/guion@autenia_bot la factura electrónica obligatoria",
    "  /guion   la factura electrónica obligatoria  ",
])
def test_a_brief_is_recognised_however_it_is_typed(escrito):
    """This gets typed on a phone; the parser meets the operator halfway."""
    action = parse_update(message(escrito), awaiting=[])
    assert action.kind == "tema"
    assert action.text == "la factura electrónica obligatoria"


def test_a_brief_is_honoured_even_while_a_script_waits():
    """A direct request outranks the review queue: it is not feedback."""
    action = parse_update(message("/guion los albaranes"), awaiting=["v1"])
    assert action == Action(kind="tema", version_id=None, text="los albaranes")


def test_a_bare_command_asks_what_about():
    """Answering nothing to a command reads as a broken bot."""
    action = parse_update(message("/guion"), awaiting=[])
    assert action == Action(kind="tema", version_id=None, text="")


def test_a_word_that_merely_starts_like_the_command_is_not_one():
    assert telegram.brief_of("/guionada de prueba") is None
    assert telegram.brief_of("temazo: esto no es un comando") is None
    assert telegram.brief_of("cambia el hook, por favor") is None


def test_a_brief_from_any_other_chat_is_ignored():
    assert parse_update(message("/guion lo que sea", chat_id=STRANGER),
                        awaiting=[]) is None


def test_feedback_lands_on_the_newest_script_in_review():
    """`awaiting` arrives oldest first, so the last one is the live one."""
    action = parse_update(message("acorta el hook"), awaiting=["viejo", "nuevo"])
    assert action.version_id == "nuevo"


# -- the second gate -------------------------------------------------------

@pytest.mark.parametrize("kind", ["publicar", "descartar", "defectuoso"])
def test_the_video_buttons_are_bound_to_their_version(kind):
    action = parse_update(callback(f"{kind}:v-video"))
    assert action == Action(kind=kind, version_id="v-video", callback_id="cb1")


def test_the_video_keyboard_offers_the_three_real_choices():
    """Sale, no sale, o casi — y "casi" es el caso normal, no la excepción.

    Sigue sin haber "regenerar": el dinero está gastado, y volver a empezar es
    justo lo que "Defectuoso" existe para no hacer. Lo que compra un arreglo es
    la capa que falla, no el vídeo entero.
    """
    keyboard = telegram._video_keyboard("v-abc")
    data = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert data == ["publicar:v-abc", "descartar:v-abc", "defectuoso:v-abc"]
    assert not any("regenerar" in d for d in data)


def test_a_video_button_from_any_other_chat_is_ignored():
    assert parse_update(callback("publicar:v1", chat_id=STRANGER)) is None


def test_idle_text_is_answered_not_swallowed():
    """Callar es correcto por dentro y roto por fuera: el operador espera algo."""
    action = parse_update(message("salen tres brazos"), awaiting=[])
    assert action == Action(kind="ocioso", version_id=None,
                            text="salen tres brazos")


def test_idle_text_from_any_other_chat_is_still_ignored():
    """Contestar a un desconocido confirma que el bot existe."""
    assert parse_update(message("hola", chat_id=STRANGER), awaiting=[]) is None


# -- el bot no se queda sordo mientras trabaja -----------------------------

@pytest.mark.asyncio
async def test_a_long_action_does_not_stop_the_bot_from_listening(monkeypatch):
    """Aprobar renderiza, y renderizar tarda minutos.

    Esperando la acción dentro del bucle, el bot dejaba de preguntar a Telegram
    todo ese rato: nada de lo que pulsara el operador hacía nada visible, así
    que volvía a pulsar, y al terminar el render se procesaban todas las
    pulsaciones de golpe contra una versión ya resuelta. Parecía que hacían
    falta tres toques; lo que hacía falta era un bot que no estuviera ocupado.
    """
    import asyncio

    trabajando = asyncio.Event()
    suelta = asyncio.Event()
    consultas = []
    parar = asyncio.Event()

    async def falso_call(method, params, timeout=None):
        consultas.append(method)
        if method != "getUpdates":
            return {}
        if len(consultas) == 1:
            return [{"update_id": 1, **callback("aprobar:v1")}]
        if len(consultas) >= 4:
            parar.set()
        return []

    async def handler(action):
        trabajando.set()
        await suelta.wait()          # una acción que no termina nunca

    monkeypatch.setattr(telegram, "_call", falso_call)

    bucle = asyncio.create_task(
        telegram.poll(handler, awaiting=set, stop=parar))
    await asyncio.wait_for(trabajando.wait(), timeout=2)
    await asyncio.wait_for(bucle, timeout=5)

    # Siguió preguntando con la acción todavía en marcha.
    assert len(consultas) >= 4
    assert not suelta.is_set()
    suelta.set()


@pytest.mark.asyncio
async def test_an_action_that_explodes_is_reported_not_swallowed(monkeypatch):
    import asyncio

    avisos = []
    parar = asyncio.Event()
    consultas = []

    async def falso_call(method, params, timeout=None):
        consultas.append(method)
        if len(consultas) == 1:
            return [{"update_id": 1, **callback("aprobar:v1")}]
        if len(consultas) >= 3:
            parar.set()
        return []

    async def handler(action):
        raise RuntimeError("se rompió")

    monkeypatch.setattr(telegram, "_call", falso_call)
    monkeypatch.setattr(telegram, "send_message",
                        lambda texto: avisos.append(texto) or asyncio.sleep(0))

    await asyncio.wait_for(
        telegram.poll(handler, awaiting=set, stop=parar), timeout=5)
    await asyncio.sleep(0)  # que la tarea de la acción llegue a terminar

    assert avisos and "se rompió" in avisos[0]
