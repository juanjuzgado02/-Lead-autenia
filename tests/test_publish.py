"""Publishing is the one step that cannot be undone, so it is tested hardest.

Four things must hold, and each one has burned somebody before:

* dry run never opens a socket — the default must be safe, not merely quiet;
* one network refusing must not hide two networks accepting;
* a timeout must be reported as ambiguous, not as a clean failure a human
  would retry blindly into a double post;
* the idempotency key must be stable across processes, or a restart mid-publish
  cannot tell "already posted" from "not posted yet".
"""

import httpx
import pytest

from core_config import ConfigError

from autenia import publish


@pytest.fixture(autouse=True)
def every_network(monkeypatch):
    """Test against all three unless a test says otherwise.

    Without this the suite reads the deployment's own ``.env``: the day Autenia
    narrowed publishing to YouTube, two tests about partial failure started
    failing for a reason that had nothing to do with what they check.
    """
    monkeypatch.delenv("AUTENIA_REDES", raising=False)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "short.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 512)
    return str(path)


@pytest.fixture
def live(monkeypatch):
    """Turn dry run off and give publishing the configuration it demands."""
    monkeypatch.setenv("AUTENIA_PUBLISH_DRY_RUN", "false")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.setenv("UPLOAD_POST_USER", "autenia")


def _transport(handler):
    """Route every request through a handler instead of the network."""
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    return Client


# -- the default ----------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_is_the_default_and_touches_no_network(video, monkeypatch):
    monkeypatch.delenv("AUTENIA_PUBLISH_DRY_RUN", raising=False)

    def explode(request):  # pragma: no cover - reaching this is the failure
        raise AssertionError(f"dry run sent a request to {request.url}")

    monkeypatch.setattr(httpx, "AsyncClient", _transport(explode))
    results = await publish.publish(video, version_id="v1", title="t",
                                    caption="c")

    assert [r.platform for r in results] == list(publish.PLATFORMS)
    assert all(r.dry_run and r.ok for r in results)


@pytest.mark.parametrize("value", ["", "sí", "TRUE-ish", "off?", "  "])
@pytest.mark.asyncio
async def test_a_malformed_switch_stays_dry(video, monkeypatch, value):
    """A typo in .env must not be the thing that publishes to the company."""
    monkeypatch.setenv("AUTENIA_PUBLISH_DRY_RUN", value)
    results = await publish.publish(video, version_id="v1", title="t", caption="c")
    assert all(r.dry_run for r in results)


@pytest.mark.parametrize("value", ["false", "0", "no"])
@pytest.mark.asyncio
async def test_an_unambiguous_false_does_turn_it_off(video, monkeypatch, value):
    """The safety net must not be so wide that it cannot be lowered."""
    monkeypatch.setenv("AUTENIA_PUBLISH_DRY_RUN", value)
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.setenv("UPLOAD_POST_USER", "autenia")
    monkeypatch.setattr(httpx, "AsyncClient", _transport(
        lambda r: httpx.Response(200, json={"id": "1"})))
    monkeypatch.setattr(publish.asyncio, "sleep", _noop)

    results = await publish.publish(video, version_id="v1", title="t",
                                    caption="c", platforms=("tiktok",))
    assert not results[0].dry_run and results[0].ok


@pytest.mark.asyncio
async def test_dry_run_still_builds_a_well_formed_payload(video, monkeypatch):
    monkeypatch.setenv("AUTENIA_PUBLISH_DRY_RUN", "true")
    results = await publish.publish(video, version_id="v1", title="Título",
                                    caption="Cuerpo", platforms=("instagram",))
    assert results[0].payload["media_type"] == "REELS"
    assert results[0].payload["instagram_title"] == "Cuerpo"


# -- partial failure ------------------------------------------------------

@pytest.mark.asyncio
async def test_one_refusal_does_not_hide_two_successes(video, monkeypatch, live):
    def handler(request):
        body = request.content.decode("utf-8", "replace")
        if "instagram" in body:
            return httpx.Response(422, json={"error": "cuenta no conectada"})
        return httpx.Response(200, json={"id": "post-1"})

    monkeypatch.setattr(httpx, "AsyncClient", _transport(handler))
    monkeypatch.setattr(publish.asyncio, "sleep", _noop)

    results = {r.platform: r for r in await publish.publish(
        video, version_id="v1", title="t", caption="c")}

    assert results["tiktok"].ok and results["youtube"].ok
    assert not results["instagram"].ok
    assert "cuenta no conectada" in results["instagram"].detail


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
@pytest.mark.asyncio
async def test_error_statuses_are_results_not_exceptions(video, monkeypatch,
                                                         live, status):
    monkeypatch.setattr(httpx, "AsyncClient",
                        _transport(lambda r: httpx.Response(status, text="nope")))
    monkeypatch.setattr(publish.asyncio, "sleep", _noop)

    results = await publish.publish(video, version_id="v1", title="t",
                                    caption="c", platforms=("tiktok",))
    assert not results[0].ok
    assert str(status) in results[0].detail or "nope" in results[0].detail


@pytest.mark.asyncio
async def test_a_timeout_is_reported_as_ambiguous(video, monkeypatch, live):
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _transport(handler))
    monkeypatch.setattr(publish.asyncio, "sleep", _noop)

    results = await publish.publish(video, version_id="v1", title="t",
                                    caption="c", platforms=("tiktok",))
    assert not results[0].ok
    # "sin respuesta" is the operator-facing distinction between "it refused"
    # and "we do not know" — the second must never read as the first.
    assert "sin respuesta" in results[0].detail


@pytest.mark.asyncio
async def test_202_counts_as_accepted(video, monkeypatch, live):
    monkeypatch.setattr(httpx, "AsyncClient", _transport(
        lambda r: httpx.Response(202, json={"job_id": 77})))
    monkeypatch.setattr(publish.asyncio, "sleep", _noop)

    results = await publish.publish(video, version_id="v1", title="t",
                                    caption="c", platforms=("youtube",))
    assert results[0].ok and results[0].external_id == "77"


# -- refusals -------------------------------------------------------------

@pytest.mark.asyncio
async def test_refuses_a_video_that_is_not_there(tmp_path):
    with pytest.raises(publish.PublishError):
        await publish.publish(str(tmp_path / "missing.mp4"), version_id="v1",
                              title="t", caption="c")


@pytest.mark.asyncio
async def test_refuses_an_empty_caption(video):
    with pytest.raises(publish.PublishError):
        await publish.publish(video, version_id="v1", title="t", caption="   ")


@pytest.mark.asyncio
async def test_refuses_an_unknown_platform(video):
    with pytest.raises(publish.PublishError):
        await publish.publish(video, version_id="v1", title="t", caption="c",
                              platforms=("linkedin",))


@pytest.mark.asyncio
async def test_missing_profile_fails_before_uploading(video, monkeypatch):
    """Named plainly, and before a single byte of video is sent."""
    monkeypatch.setenv("AUTENIA_PUBLISH_DRY_RUN", "false")
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test-key")
    monkeypatch.delenv("UPLOAD_POST_USER", raising=False)

    def explode(request):  # pragma: no cover - reaching this is the failure
        raise AssertionError("uploaded before checking the configuration")

    monkeypatch.setattr(httpx, "AsyncClient", _transport(explode))
    with pytest.raises(ConfigError, match="UPLOAD_POST_USER"):
        await publish.publish(video, version_id="v1", title="t", caption="c")


# -- limits and keys ------------------------------------------------------

@pytest.mark.parametrize("platform,limit", sorted(publish.TITLE_LIMIT.items()))
def test_titles_never_exceed_the_platform_limit(platform, limit):
    payload = publish._payload(platform, user="u", title="palabra " * 60,
                               body="cuerpo " * 900)
    assert len(payload["title"]) <= limit
    for key in ("tiktok_title", "instagram_title", "youtube_description"):
        if key in payload:
            assert len(payload[key]) <= publish.BODY_LIMIT[platform]


def test_the_key_is_stable_and_distinct_per_platform():
    assert publish.key_for("v1", "tiktok") == publish.key_for("v1", "tiktok")
    assert publish.key_for("v1", "tiktok") != publish.key_for("v1", "youtube")
    assert publish.key_for("v1", "tiktok") != publish.key_for("v2", "tiktok")


def test_no_api_key_survives_into_a_persisted_payload(video):
    payload = publish._payload("tiktok", user="autenia", title="t", body="c")
    assert "Authorization" not in payload
    assert not any("Apikey" in str(v) for v in payload.values())


async def _noop(*args, **kwargs):
    """Skip the anti-burst pause so the suite stays fast."""


# -- which networks, and how the video lands -------------------------------

def test_without_the_variable_every_network_is_a_destination(monkeypatch):
    monkeypatch.delenv("AUTENIA_REDES", raising=False)
    assert publish.configured() == publish.PLATFORMS


def test_the_operator_can_narrow_it_to_one(monkeypatch):
    """A free plan counts calls, and an unconnected network spends one to fail."""
    monkeypatch.setenv("AUTENIA_REDES", "youtube")
    assert publish.configured() == ("youtube",)


@pytest.mark.parametrize("escrito", ["youtube, tiktok", "YouTube;TikTok",
                                     " youtube , tiktok "])
def test_a_list_is_read_however_it_is_written(monkeypatch, escrito):
    monkeypatch.setenv("AUTENIA_REDES", escrito)
    assert publish.configured() == ("youtube", "tiktok")


def test_a_typo_publishes_less_never_more(monkeypatch):
    """The two ways of being wrong are not symmetrical."""
    monkeypatch.setenv("AUTENIA_REDES", "youtub")
    assert publish.configured() == ()


@pytest.mark.asyncio
async def test_publishing_nowhere_is_refused_rather_than_silently_fine(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTENIA_REDES", "youtub")
    monkeypatch.setenv("AUTENIA_PUBLISH_DRY_RUN", "true")
    video = tmp_path / "short.mp4"
    video.write_bytes(b"0" * 2048)
    with pytest.raises(publish.PublishError):
        await publish.publish(str(video), version_id="v1", title="t", caption="c")


@pytest.mark.parametrize("valor,esperado", [
    ("unlisted", "unlisted"), ("private", "private"), ("public", "public"),
    ("PUBLICO", "public"), ("", "public"),
])
def test_youtube_privacy_falls_back_to_public(monkeypatch, valor, esperado):
    monkeypatch.setenv("AUTENIA_YOUTUBE_PRIVACY", valor)
    assert publish.youtube_privacy() == esperado
    assert publish._payload("youtube", user="autenia", title="t",
                            body="c")["privacyStatus"] == esperado


# -- everything YouTube accepts in the same call ---------------------------

def test_the_description_carries_the_link_and_the_tags():
    """The narration says "en la web"; the description has to say which web."""
    text = publish.youtube_description("La factura electrónica será obligatoria.")
    assert "La factura electrónica será obligatoria." in text
    assert "auteniaai.com" in text
    assert "#pymes" in text


def test_the_description_is_configurable_without_touching_code(monkeypatch):
    monkeypatch.setenv("AUTENIA_WEB", "https://ejemplo.es/")
    monkeypatch.setenv("AUTENIA_YOUTUBE_HASHTAGS", "#uno #dos")
    text = publish.youtube_description("cuerpo")
    assert "https://ejemplo.es/" in text and "#uno #dos" in text
    assert "auteniaai" not in text


def test_an_empty_caption_still_yields_a_usable_description():
    text = publish.youtube_description("")
    assert text.startswith("👉") and "#" in text


def test_the_upload_declares_spanish_and_a_findable_category():
    data = publish._payload("youtube", user="autenia", title="t", body="c")
    assert data["defaultLanguage"] == "es"
    assert data["defaultAudioLanguage"] == "es-ES"
    assert data["categoryId"] == "28"        # no "People & Blogs"


def test_the_upload_declares_the_video_is_generated():
    """Photoreal footage and a synthetic voice: YouTube wants that said."""
    data = publish._payload("youtube", user="autenia", title="t", body="c")
    assert data["containsSyntheticMedia"] == "true"


def test_a_long_description_is_cut_to_youtubes_limit():
    data = publish._payload("youtube", user="autenia", title="t",
                            body="palabra " * 2000)
    assert len(data["youtube_description"]) <= publish.BODY_LIMIT["youtube"]


def test_the_other_networks_do_not_get_youtube_fields():
    for platform in ("tiktok", "instagram"):
        data = publish._payload(platform, user="autenia", title="t", body="c")
        assert "categoryId" not in data
        assert "containsSyntheticMedia" not in data


def test_the_description_keeps_its_line_breaks():
    """The link and the tags are their own paragraphs, not a tail on the first."""
    data = publish._payload("youtube", user="autenia", title="t",
                            body="La factura electrónica será obligatoria.")
    assert data["youtube_description"].count("\n") >= 2
    assert data["youtube_description"].splitlines()[0].endswith("obligatoria.")


def test_a_title_still_loses_its_line_breaks():
    """A newline in a title is a rejected upload."""
    assert "\n" not in publish._clip("dos\nlíneas", 100)


# -- Short, y lo que se enseña antes de publicar ---------------------------

@pytest.mark.parametrize("duracion,alto,esperado", [
    (26.0, 1920, True),      # lo que produce este sistema
    (55.0, 1920, True),      # el máximo que permite el preflight
    (181.0, 1920, False),    # pasa de tres minutos
    (26.0, 608, False),      # apaisado: YouTube no lo trata como Short
    (None, 1920, False),
])
def test_what_youtube_will_file_as_a_short(duracion, alto, esperado):
    """No se declara en ningún campo: se decide mirando el fichero."""
    assert publish.is_short(duracion, 1080, alto) is esperado


def test_the_preview_is_built_by_the_code_that_sends(monkeypatch):
    """Aprobar un resumen de lo que se va a publicar no vale de nada."""
    monkeypatch.setenv("UPLOAD_POST_USER", "autenia")
    visto = publish.preview("youtube", title="Un título", caption="Un cuerpo")
    enviado = publish._payload("youtube", user="autenia", title="Un título",
                               body="Un cuerpo")
    assert visto == enviado
