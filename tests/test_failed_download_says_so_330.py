"""A failed model download says so in plain words and can be retried; it is never shown as finished (#330).
No real Ollama and no real download: the pull subprocess is faked."""

import io
import subprocess
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import sizing
from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User

CURRENT = "qwen2.5:3b"
TAG = "llama3.1:8b"
FAILED_TEXT = (
    "The download did not finish. Nothing was changed and your current model keeps working."
)
DISK_TEXT = (
    "There was not enough free disk space to finish the download. Free some space, then try again."
)


class _Proc:
    """A stand-in for `ollama pull`: prints `out`, then exits with `code`."""

    def __init__(self, out: bytes, code: int, on_read=None):
        self.stdout = io.BytesIO(out)
        self._code = code
        self._on_read = on_read

    def wait(self, timeout=None):
        return self._code

    def poll(self):
        return self._code

    def terminate(self):
        pass


def _client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add(User(org_id=o.id, email="a@a.com", role="admin", active=True))
    s.add(
        OrgSettings(
            org_id=o.id, deployment_topology="solo", local_model_chosen=True, ollama_model=CURRENT
        )
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


@pytest.fixture
def env(tmp_path, monkeypatch):
    c, app_mod, org_id = _client(tmp_path)
    monkeypatch.setattr(sizing, "local_hardware", lambda: (64.0, "apple"))
    monkeypatch.setattr("anthill.inference.ollama.find_ollama_bin", lambda *a, **k: "/fake/ollama")
    monkeypatch.setattr("anthill.inference.ollama.ensure_serving", lambda *a, **k: True)
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models", lambda self: [CURRENT]
    )
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: [CURRENT])
    app_mod._MODEL_PULL_FAILED.clear()

    def pull(proc_factory, tag=TAG):
        with monkeypatch.context() as m:
            m.setattr(subprocess, "Popen", lambda *a, **k: proc_factory())
            r = c.post("/models/pull", data={"model_tag": tag}, follow_redirects=False)
            assert r.status_code == 302
            for _ in range(100):  # the download runs in a thread; wait for it to end
                if not (app_mod._MODEL_PULL_STATE.get(org_id) or {}).get("pulls"):
                    break
                time.sleep(0.05)
        return r

    return c, app_mod, org_id, pull


def _status(c):
    return c.get("/models/pull-status").json()


def test_a_failed_download_is_reported_in_the_status_and_changes_nothing(env):
    c, app_mod, _org_id, pull = env
    pull(lambda: _Proc(b"pulling manifest\nError: something went wrong\n", 1))
    assert _status(c)["failed"] == {"tag": TAG, "reason": ""}
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert (
        cfg.ollama_model == CURRENT and cfg.local_model_pulling == ""
    )  # the previous model keeps serving


def test_the_models_page_says_it_in_plain_words_with_a_retry(env):
    c, _app_mod, _org, pull = env
    pull(lambda: _Proc(b"Error: pull model manifest: 412: requires a newer version of Ollama\n", 1))
    page = c.get("/models").text
    assert FAILED_TEXT in page and 'action="/models/pull"' in page and "Try again" in page
    assert f'name="model_tag" id="fail-tag" value="{TAG}"' in page
    # a visible failure, not the "finished" banner, and nothing from the tool's output or about versions
    assert 'id="fail-banner" class="alert alert-warn" style="display:block"' in page
    for word in ("412", "newer version", "Error: pull model", "requires a newer"):
        assert word not in page


def test_settings_shows_the_same_message(env):
    c, _app_mod, _org, pull = env
    pull(lambda: _Proc(b"Error: boom\n", 1))
    page = c.get("/personalize").text
    assert FAILED_TEXT in page and "Try again" in page


def test_a_full_disk_gets_its_own_plain_sentence(env):
    c, _app_mod, _org, pull = env
    pull(lambda: _Proc(b"pulling abc 10%\nError: write /x: no space left on device\n", 1))
    assert _status(c)["failed"] == {"tag": TAG, "reason": "disk"}
    page = c.get("/models").text
    assert DISK_TEXT in page and "no space left" not in page.lower()


def test_a_successful_download_records_no_failure(env):
    c, _app_mod, _org, pull = env
    pull(lambda: _Proc(b"pulling manifest\nsuccess\n", 0))
    assert _status(c)["failed"] is None
    assert 'id="fail-banner" class="alert alert-warn" style="display:none"' in c.get("/models").text


def test_a_new_download_clears_the_last_failure(env):
    c, _app_mod, _org, pull = env
    pull(lambda: _Proc(b"Error: boom\n", 1))
    assert _status(c)["failed"] is not None
    pull(lambda: _Proc(b"success\n", 0), tag="gemma2:9b")
    assert _status(c)["failed"] is None


def test_a_cancelled_download_is_not_a_failure(env):
    c, app_mod, org_id, pull = env

    class _Cancelled(_Proc):
        def __init__(self):
            super().__init__(b"", 1)
            for e in ((app_mod._MODEL_PULL_STATE.get(org_id) or {}).get("pulls") or {}).values():
                e["cancelled"] = True  # the user pressed Cancel while it was downloading

    pull(_Cancelled)
    assert _status(c)["failed"] is None


def test_both_pages_never_show_finished_when_the_status_says_failed():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "anthill" / "web" / "templates"
    for name in ("models.html", "personalize.html"):
        src = (root / name).read_text()
        failed_branch = src.index("wasPulling && s.failed")
        ready_branch = src.index("} else if (wasPulling) {")
        assert (
            failed_branch < ready_branch
        )  # the failed branch is checked before the "finished" one
