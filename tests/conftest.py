"""Shared pytest fixtures.

Hermetic default: pretend no local model backend exists (as in CI, where no Ollama runs). Several seams
can otherwise reach a dev's live Ollama and make the suite nondeterministic - serially, and far more
often under ``pytest -n`` where CPU contention widens the race:

- ``installed_models()`` - the runtime cross-check verifier builds its OWN ``OllamaBackend`` from
  settings to pick a different-family model, bypassing a test's mocked chat backend. Forcing the list
  empty makes the verifier a no-op.
- ``chat()`` / ``chat_with_tools()`` / ``chat_with_confidence()`` - the review gate
  (``anthill.wiki.review`` / ``_propose_scoped``), the agent, and the #278 escalation-confidence
  trigger build a real ``OllamaBackend`` from org settings and call it directly. Making these raise
  ``BackendError`` reproduces the CI "no local model" state, so the fail-safe (a queued review, or
  "no confidence signal") runs instead of a live model's nondeterministic output deciding the flags.
- Any other httpx call to the Ollama port - the review gate reaches the model over the CHAT path (an
  httpx POST to ``/api/chat``), which the method stubs above do not all cover. Blocking every httpx call
  to :11434 at the transport layer (``Client.send`` / ``AsyncClient.send``, which get/post/request all
  funnel through) closes that path too, so e.g. ``test_org_skill_queues_review`` deterministically sees
  the backend as down and queues its review.
- ``app._maybe_pull_embedding_model()`` - the server-startup hook that pulls Ollama's bge-m3 embedding
  model in the background when it isn't already there. Its OWN fallback behavior when the httpx-level
  block above makes it see "not available" is to shell out to a real ``ollama pull`` subprocess - not
  an httpx call, so the transport-layer block can't reach it. A REAL bug, caught live: on a dev machine
  with Ollama actually installed, any test that spins up a FastAPI ``TestClient`` fires the startup
  event, which used to kick off a genuine ~1.2GB download mid test-suite. Stubbed to a no-op directly
  (like ``installed_models`` above) rather than relying on the transport block.

- Any attempt to run the real ``ollama`` executable (``ollama pull`` / ``ollama serve``) - see
  ``ollama_spawn_guard`` below. The pull/serve helpers in the app run in daemon threads that can outlive the
  test that started them; once that test's patches are undone such a thread reaches the developer's real
  Ollama and, with it running, really downloads a model (observed: a ~2.4 GB vision model on a dev Mac,
  2026-10-03). CI has no Ollama, so this never shows there. Refused at the process-spawn seam for the whole
  session, and the run fails if anything tried.

A test that needs a working model or specific installed models overrides the relevant method with its
own ``monkeypatch`` (applied after this autouse fixture, so it wins); FastAPI ``TestClient`` traffic
(base_url ``http://testserver``) is untouched.
"""

import os
import subprocess
import threading

import httpx
import pytest

from anthill.inference.base import BackendError

_OLLAMA_PORT = "11434"
_real_send = httpx.Client.send
_real_asend = httpx.AsyncClient.send


def _blocked_send(self, request, *args, **kwargs):
    if _OLLAMA_PORT in str(request.url):
        raise httpx.ConnectError("hermetic tests: no local Ollama (see tests/conftest.py)")
    return _real_send(self, request, *args, **kwargs)


async def _blocked_asend(self, request, *args, **kwargs):
    if _OLLAMA_PORT in str(request.url):
        raise httpx.ConnectError("hermetic tests: no local Ollama (see tests/conftest.py)")
    return await _real_asend(self, request, *args, **kwargs)


_OLLAMA_SPAWN_ATTEMPTS: list[str] = []
_RealPopen = subprocess.Popen


def _runs_ollama(args) -> bool:
    """True when ``args`` (a Popen argv, or a shell command string) starts the ``ollama`` executable."""
    if isinstance(args, (list, tuple)):
        first = args[0] if args else ""
    else:
        first = os.fsdecode(args).split(None, 1)[0] if os.fsdecode(args).strip() else ""
    return os.path.basename(os.fsdecode(first)).lower().startswith("ollama")


class _NoOllamaPopen(_RealPopen):
    """Popen that refuses to start the real ``ollama`` (and records who asked); anything else is untouched.
    ``subprocess.run`` / ``check_output`` build a Popen, so this one seam covers all of them."""

    def __init__(self, args, *a, **kw):
        if _runs_ollama(args):
            shown = args if isinstance(args, str) else list(args)
            _OLLAMA_SPAWN_ATTEMPTS.append(
                f"{shown} while running {os.environ.get('PYTEST_CURRENT_TEST', '?')} "
                f"({threading.current_thread().name})"
            )
            # OSError: the app's own launch/pull code treats it as "could not start" and carries on.
            raise OSError("hermetic tests: refusing to run the real ollama (see tests/conftest.py)")
        super().__init__(args, *a, **kw)


@pytest.fixture(scope="session", autouse=True)
def ollama_spawn_guard():
    """No test may run the real ``ollama``. Installed once for the whole session and never undone: a
    background pull/serve thread started by one test can wake up long after that test's patches were
    reverted, and that late wake-up is what really pulled a model. Yields the list of attempts so the
    guard's own tests can check it; any attempt left in it at the end fails the run."""
    subprocess.Popen = _NoOllamaPopen
    yield _OLLAMA_SPAWN_ATTEMPTS
    assert not _OLLAMA_SPAWN_ATTEMPTS, (
        "tests tried to run the real `ollama` (refused). A test started a background model pull or "
        "server start without stubbing it - monkeypatch app._start_model_pull and "
        "app._maybe_autopull_vision as tests/test_setup_model_council.py does. A leftover thread may "
        "belong to an earlier test than the one named:\n  " + "\n  ".join(_OLLAMA_SPAWN_ATTEMPTS)
    )


@pytest.fixture(autouse=True)
def _no_page_index_warmup(monkeypatch):
    """The background page-vector warm-up (anthill/wiki/page_index.py) is off in every test: it would embed
    pages on a worker thread behind a test's back. A test that wants it sets ``page_index.ENABLED`` itself."""
    monkeypatch.setattr("anthill.wiki.page_index.ENABLED", False)


@pytest.fixture(autouse=True)
def _no_live_ollama(monkeypatch):
    def _unreachable(self, *args, **kwargs):
        raise BackendError("no local model in tests (hermetic default)")

    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models",
        lambda self: [],
    )
    # installed_models_with_sizes() (which installed_models() above is now backed by, and which the
    # model picker's "installed but not in the curated catalog" list - app.py's other_installed -
    # calls directly) is deliberately NOT given the same class-level override here: unlike chat() etc.
    # above, it already degrades to [] on its own whenever the httpx.Client.send block below fires (its
    # try/except catches the resulting ConnectError same as any other unreachable-engine case), so a
    # class-level stub would be redundant - and it would actively break a test that wants to verify
    # THIS method's own response-parsing by mocking httpx.get (monkeypatch replaces a stubbed method
    # entirely rather than wrapping it, so the httpx mock would never be reached). A test overriding
    # only installed_models should keep in mind other_installed reads installed_models_with_sizes
    # directly and needs its own override too - see tests/test_settings_model_picker.py for the
    # pattern.
    monkeypatch.setattr("anthill.inference.ollama.OllamaBackend.chat", _unreachable)
    monkeypatch.setattr("anthill.inference.ollama.OllamaBackend.chat_with_tools", _unreachable)
    monkeypatch.setattr("anthill.inference.ollama.OllamaBackend.chat_with_confidence", _unreachable)
    monkeypatch.setattr("anthill.web.app._maybe_pull_embedding_model", lambda: None)
    # Belt-and-suspenders: every httpx path (get/post/request/Client/AsyncClient) funnels through
    # Client.send, so blocking :11434 there also covers the review-gate CHAT POST that does not route
    # through OllamaBackend.chat.
    monkeypatch.setattr(httpx.Client, "send", _blocked_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_asend)


@pytest.fixture(autouse=True)
def _local_server_by_default(monkeypatch):
    """Tests run as a server that listens on loopback only, unless a test says otherwise. The app treats a
    server that does not say where it listens as reachable by others (sign-up by invitation, install-wide
    controls for the owner only), so the hermetic default names the loopback address, as the desktop app does."""
    monkeypatch.setenv("ANTHILL_HOST", "127.0.0.1")
    monkeypatch.setenv("ANTHILL_LOCAL_ONLY", "1")
