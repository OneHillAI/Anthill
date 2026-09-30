"""Guards for the web app module - catch import/wiring regressions cheaply.

The chat stream NameError'd on every message because `json` was never imported
at module scope (only `json.dumps` was used inside the SSE generator). This guards
against that class of regression without needing a live model.
"""

import anthill.web.app as app_mod


def test_app_module_has_json_imported():
    # The SSE chat stream uses json.dumps; the module-level import must exist.
    assert getattr(app_mod, "json", None) is not None


def test_app_exposes_expected_routes():
    paths = {r.path for r in app_mod.app.routes}
    for p in [
        "/",
        "/login",
        "/setup",
        "/chat/{conv_id}/stream",
        "/snippets/save",
        "/push/key",
        "/sw.js",
        "/help/ask",
        "/skills",
        "/memory",
        "/files/{name}",
        "/connectors/mcp",
    ]:
        assert p in paths, f"missing route {p}"


def test_help_bot_reads_user_docs():
    # The help bot grounds answers in the user-facing guides (HTML stripped).
    ctx = app_mod._help_context()
    assert ctx, "help context should not be empty"
    low = ctx.lower()
    assert "cache" in low and "install" in low  # how-it-works + setup content
    assert "<h2" not in ctx and "<p>" not in ctx  # HTML tags stripped
