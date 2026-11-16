"""The Settings model card shows the configured model's own size (from Ollama's /api/tags), not the
size of the whole models folder (issue #160). The folder total is labelled separately, and either
number is left out when it cannot be known."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.backup as backup_mod
import anthill.inference.ollama as ollama_mod
from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User

_GB = 1_000_000_000


def _client(tmp_path, monkeypatch, model="qwen2.5:3b", compute="local"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="Solo", slug="s")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all(
        [
            u,
            OrgSettings(
                org_id=o.id,
                deployment_topology="solo",
                ollama_model=model,
                solo_compute=compute,
            ),
        ]
    )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


class _FakeBlob:
    def __init__(self, size_bytes):
        self._size = size_bytes

    def is_file(self):
        return True

    def stat(self):
        return type("Stat", (), {"st_size": self._size})()


class _FakeModelsDir:
    """A models folder holding one blob of the given size, with nothing written to disk."""

    def __init__(self, size_bytes):
        self._blob = _FakeBlob(size_bytes)

    def exists(self):
        return True

    def rglob(self, pattern):
        return [self._blob]


def _models_folder(monkeypatch, size_bytes):
    monkeypatch.setattr(backup_mod, "ollama_models_dir", lambda: _FakeModelsDir(size_bytes))


def _installed(monkeypatch, models):
    monkeypatch.setattr(
        ollama_mod.OllamaBackend,
        "installed_models_with_sizes",
        lambda self: [{"name": n, "size_bytes": b} for n, b in models],
    )


def test_card_shows_the_models_own_size_not_the_folder_total(tmp_path, monkeypatch):
    _installed(monkeypatch, [("bge-m3:latest", 1_200_000_000), ("qwen2.5:3b", 1_900_000_000)])
    _models_folder(monkeypatch, 4_632_888_224)
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "On this device &middot; 1.9 GB &middot; fully offline" in body
    assert "On this device &middot; 4.6 GB" not in body


def test_folder_total_is_labelled_separately_when_larger(tmp_path, monkeypatch):
    _installed(monkeypatch, [("qwen2.5:3b", 1_900_000_000)])
    _models_folder(monkeypatch, 4_632_888_224)
    c, _ = _client(tmp_path, monkeypatch)
    assert "Models on disk: 4.6 GB in total" in c.get("/personalize").text


def test_no_folder_line_when_it_adds_nothing(tmp_path, monkeypatch):
    _installed(monkeypatch, [("qwen2.5:3b", 1_900_000_000)])
    _models_folder(monkeypatch, 1_900_000_000)
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "1.9 GB" in body
    assert "Models on disk" not in body


def test_latest_suffix_does_not_hide_the_size(tmp_path, monkeypatch):
    _installed(monkeypatch, [("llama3:latest", 4_700_000_000)])
    c, _ = _client(tmp_path, monkeypatch, model="llama3")
    assert "On this device &middot; 4.7 GB &middot; fully offline" in c.get("/personalize").text


def test_size_is_omitted_when_ollama_is_unreachable(tmp_path, monkeypatch):
    _installed(monkeypatch, [])
    _models_folder(monkeypatch, 4_632_888_224)
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "On this device &middot; fully offline" in body
    assert "On this device &middot; 4.6 GB" not in body
    assert (
        "Models on disk" not in body
    )  # the folder total never stands in for an unknown model size


def test_size_is_omitted_when_the_model_is_not_installed(tmp_path, monkeypatch):
    _installed(monkeypatch, [("bge-m3:latest", 1_200_000_000)])
    _models_folder(monkeypatch, 4_632_888_224)
    c, _ = _client(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "On this device &middot; fully offline" in body
    assert "Models on disk" not in body


def test_sizes_helper_survives_a_failing_probe_and_folder(monkeypatch):
    import anthill.web.app as app_mod

    def boom(self):
        raise RuntimeError("probe")

    monkeypatch.setattr(ollama_mod.OllamaBackend, "installed_models_with_sizes", boom)
    monkeypatch.setattr(backup_mod, "ollama_models_dir", boom)

    class Cfg:
        ollama_model = "qwen2.5:3b"
        ollama_url = "http://localhost:11434"

    assert app_mod._local_model_sizes_gb(Cfg()) == (0.0, 0.0)
    assert app_mod._local_model_sizes_gb(None) == (0.0, 0.0)


@pytest.mark.parametrize("compute", ["cloud"])
def test_cloud_card_never_shows_a_local_size(tmp_path, monkeypatch, compute):
    _installed(monkeypatch, [("qwen2.5:3b", 1_900_000_000)])
    _models_folder(monkeypatch, 4_632_888_224)
    c, _ = _client(tmp_path, monkeypatch, compute=compute)
    body = c.get("/personalize").text
    assert "Models on disk" not in body
    assert "Runs on your private cloud GPU" in body


def test_a_tiny_installed_model_still_shows_a_size(tmp_path, monkeypatch):
    # 0 means "unknown" to the card, so a real model under 50 MB must not round down to it.
    _installed(monkeypatch, [("qwen2.5:3b", 30_000_000)])
    c, _ = _client(tmp_path, monkeypatch)
    assert "On this device &middot; 0.1 GB" in c.get("/personalize").text
