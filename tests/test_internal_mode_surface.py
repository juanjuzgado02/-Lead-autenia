"""The public surface stays closed in Autenia's internal deployment.

Importing ``app`` pulls in the whole render stack (torch, mediapipe, whisper),
which is not available outside the container — so these assert over the parsed
source instead. That still fails loudly if a guard is deleted or if a new
gallery route is added without one, which is the regression that matters.

``deny_if_internal`` itself is behaviour-tested below against both modes.
"""

import ast
import os

import pytest

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")

# Every route that would expose generated videos or actor images to anyone who
# knows the URL. All four must refuse in internal mode.
PUBLIC_GALLERY_ROUTES = [
    "/api/saasshorts/gallery",
    "/gallery",
    "/video/{video_id}",
    "/api/saasshorts/actor-gallery",
]

GUARD = "deny_if_internal"


@pytest.fixture(scope="module")
def app_tree():
    with open(APP_PATH, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _route_path(decorator):
    """The URL a @app.get(...) / @app.post(...) decorator registers, or None."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute) or func.attr not in ("get", "post", "put", "delete"):
        return None
    if not isinstance(func.value, ast.Name) or func.value.id != "app":
        return None
    if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
        return None
    return decorator.args[0].value


def _handlers_by_route(tree):
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            path = _route_path(decorator)
            if path:
                found[path] = node
    return found


def _calls(node):
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


@pytest.mark.parametrize("route", PUBLIC_GALLERY_ROUTES)
def test_gallery_route_is_guarded(app_tree, route):
    handlers = _handlers_by_route(app_tree)
    assert route in handlers, f"route {route} disappeared — update this test with it"
    assert GUARD in _calls(handlers[route]), (
        f"{route} no longer calls {GUARD}(): it would serve Autenia's unapproved "
        f"drafts to anyone who requests the URL directly"
    )


def test_guard_runs_before_any_data_is_read(app_tree):
    """The guard must be the first statement, not a check after the S3 lookup."""
    handlers = _handlers_by_route(app_tree)
    for route in PUBLIC_GALLERY_ROUTES:
        body = [stmt for stmt in handlers[route].body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
        first = body[0]
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), route
        assert first.value.func.id == GUARD, (
            f"{route} does work before calling {GUARD}()"
        )


def test_gallery_upload_is_gated_on_internal_mode(app_tree):
    """Opt-in sharing must additionally be impossible in internal mode."""
    with open(APP_PATH, encoding="utf-8") as handle:
        source = handle.read()
    assert "if req.share_to_gallery and not autenia.internal_mode:" in source, (
        "the gallery upload path is no longer gated on internal mode"
    )


# -- the guard's own definition -------------------------------------------

def test_guard_keys_off_internal_mode_and_raises_404(app_tree):
    """404 rather than 403: a direct call should learn nothing about what exists."""
    guard = next(
        (node for node in ast.walk(app_tree)
         if isinstance(node, ast.FunctionDef) and node.name == GUARD),
        None,
    )
    assert guard is not None, f"{GUARD}() is gone"

    source = ast.dump(guard)
    assert "internal_mode" in source, f"{GUARD}() no longer checks internal_mode"

    raises = [node for node in ast.walk(guard) if isinstance(node, ast.Raise)]
    assert raises, f"{GUARD}() does not raise"
    status = next(
        (kw.value.value for node in raises
         if isinstance(node.exc, ast.Call)
         for kw in node.exc.keywords
         if kw.arg == "status_code" and isinstance(kw.value, ast.Constant)),
        None,
    )
    assert status == 404, f"{GUARD}() raises {status}, which advertises the route exists"


def test_upstream_self_host_keeps_its_gallery(monkeypatch):
    """Turning internal mode off must restore the upstream OpenShorts behaviour."""
    import core_config

    monkeypatch.setenv("AUTENIA_INTERNAL_MODE", "false")
    assert core_config.settings.internal_mode is False
