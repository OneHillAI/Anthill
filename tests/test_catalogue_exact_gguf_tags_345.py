"""The ambiguous catalogue rows name the exact GGUF build, and installs under the old plain tag keep working (#345)."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import sizing
from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User

SEED = Path(sizing.__file__).with_name("model_catalog.json")
SWITCH = {  # old plain tag -> exact build tag
    "qwen3.5:0.8b": "qwen3.5:0.8b-q8_0",
    "qwen3.5:2b": "qwen3.5:2b-q4_K_M",
    "qwen3.5:4b": "qwen3.5:4b-q4_K_M",
    "qwen3.5:9b": "qwen3.5:9b-q4_K_M",
    "qwen3.5:27b": "qwen3.5:27b-q4_K_M",
    "qwen3.6": "qwen3.6:35b-a3b-q4_K_M",
    "gemma4:e2b": "gemma4:e2b-it-q4_K_M",
    "gemma4:e4b": "gemma4:e4b-it-q4_K_M",
    "gemma4:12b": "gemma4:12b-it-q4_K_M",
    "gemma4:26b": "gemma4:26b-a4b-it-q4_K_M",
    "gemma4:31b": "gemma4:31b-it-q4_K_M",
    "nemotron-3-nano:30b": "nemotron-3-nano:30b-a3b-q4_K_M",
    "nemotron-3-nano:4b": "nemotron-3-nano:4b-q4_K_M",
}
UNTOUCHED = {  # the rows whose plain tag is the same file as an exact tag, and the one-tag rows
    "gemma3:27b", "gemma3:4b", "gemma3:1b", "llama3.3:70b", "llama4:scout", "llama4:maverick",
    "phi4:14b", "granite4.2:30b", "granite4.2:8b", "granite4.2:3b", "mistral-small3.2:24b",
    "deepseek-r1:32b", "deepseek-v3.1", "nemotron-3-super:120b", "glm-4.7-flash",
    "gpt-oss:20b", "gpt-oss:120b",
}  # fmt: skip


def _rows():
    return json.loads(SEED.read_text(encoding="utf-8"))["models"]


def test_the_13_rows_name_the_exact_build_and_replace_the_plain_tag():
    got = {r["replaces"][0]: r["ollama_tag"] for r in _rows() if r.get("replaces")}
    assert got == SWITCH
    assert all(len(r["replaces"]) == 1 for r in _rows() if r.get("replaces"))


def test_the_other_rows_are_not_touched():
    for r in _rows():
        if r["ollama_tag"] in UNTOUCHED:
            assert "replaces" not in r
        if r["ollama_tag"].endswith(":cloud"):
            assert "replaces" not in r


def test_every_exact_tag_is_safe_and_no_row_needs_a_newer_engine():
    for r in _rows():
        assert sizing.is_safe_row(r)
        assert "min_engine_version" not in r  # every exact tag was accepted by 0.30.10 and 0.40.16
    for exact in SWITCH.values():
        assert sizing.is_safe_ollama_tag(exact)
        assert sizing.runs_on_bundled_engine(
            next(m for m in sizing.load_catalog() if m.ollama_tag == exact)
        )


def test_every_old_plain_tag_still_resolves_to_its_row_and_is_installed():
    cat = sizing.load_catalog()
    for plain, exact in SWITCH.items():
        row = sizing.row_for_tag(cat, plain)
        assert row is not None and row.ollama_tag == exact
        assert sizing.row_installed_tag(row, {plain}) == plain
        assert sizing.row_installed_tag(row, {plain + ":latest"}) == plain


def test_the_need_of_a_row_is_kept():
    needs = {tuple(sizing.row_tags(m))[-1]: m.need_gb for m in sizing.load_catalog() if m.need_gb}
    assert (
        needs["qwen3.5:9b"] == 9.8
        and needs["gemma4:e4b"] == 9.8
        and needs["nemotron-3-nano:4b"] == 4.8
    )
    assert "qwen3.5:27b" not in needs and "gemma4:31b" not in needs  # above 10 GB: still no need


# ── an install pinned to an old plain tag, on the pages ─────────────────────────────────────────────────────


def _client(tmp_path, model):
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
            org_id=o.id, deployment_topology="solo", local_model_chosen=True, ollama_model=model
        )
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


@pytest.fixture
def old_installs(monkeypatch):
    import anthill.inference.ollama as ollama_mod

    installed = [
        {"name": "qwen3.5:4b", "size_bytes": 3_300_000_000},
        {"name": "nemotron-3-nano:4b", "size_bytes": 2_800_000_000},
    ]
    monkeypatch.setattr(
        ollama_mod.OllamaBackend, "installed_models_with_sizes", lambda s: installed
    )
    monkeypatch.setattr(
        ollama_mod.OllamaBackend, "installed_models", lambda s: [m["name"] for m in installed]
    )
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))


def test_settings_marks_the_active_old_tag_model_and_uninstalls_the_installed_tag(
    tmp_path, monkeypatch, old_installs
):
    c, app_mod = _client(tmp_path, "qwen3.5:4b")  # chosen before the row named the exact build
    monkeypatch.setattr(
        app_mod, "_installed_local_models", lambda cfg: ["qwen3.5:4b", "nemotron-3-nano:4b"]
    )
    body = c.get("/personalize").text
    # the active model is never offered for removal; the other installed one is, by the tag that is on disk
    assert "mcUninstall(this, 'qwen3.5:4b')" not in body
    assert "mcUninstall(this, 'qwen3.5:4b-q4_K_M')" not in body
    assert "mcUninstall(this, 'nemotron-3-nano:4b')" in body
    assert "mcUninstall(this, 'nemotron-3-nano:4b-q4_K_M')" not in body
    # and the saved model is pre-selected under the row's exact tag, not shown as an unknown model
    assert '"qwen3.5:4b-q4_K_M"' in body


def test_the_models_page_does_not_call_an_old_tag_model_custom(tmp_path, monkeypatch, old_installs):
    c, app_mod = _client(tmp_path, "qwen3.5:4b")
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: ["qwen3.5:4b"])
    body = c.get("/models").text
    # the "pull a model by tag" box is pre-filled only for a model outside the catalogue; this one is a catalogue row
    box = body.split('name="model_tag" placeholder=', 1)[1].split(">", 1)[0]
    assert 'value=""' in box
