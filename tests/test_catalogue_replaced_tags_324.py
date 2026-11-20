"""A catalogue row can name the older plain tags it replaces (#324): an install that chose the old tag keeps working,
and an exact build tag is handed on unchanged. Nothing about the shipped catalogue changes."""

import dataclasses

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import sizing
from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User

BUILD = "qwen3.5:4b-q4_K_M"
PLAIN = "qwen3.5:4b"


def _row(**kw):
    base = {
        "name": "Qwen3.5 4B",
        "ollama_tag": BUILD,
        "hf_id": "Qwen/Qwen3.5-4B",
        "params_b": 4,
        "family": "Qwen",
    }
    return {**base, **kw}


def _test_catalog():
    return (sizing._model_from_row(_row(replaces=[PLAIN])),)


# ── the row field ──────────────────────────────────────────────────────────────────────────────────────────


def test_a_row_reads_the_tags_it_replaces():
    m = sizing._model_from_row(_row(replaces=[PLAIN, "qwen3.5:4b-q8_0"]))
    assert m.replaces == (PLAIN, "qwen3.5:4b-q8_0")
    assert sizing.row_tags(m) == (BUILD, PLAIN, "qwen3.5:4b-q8_0")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "qwen3.5:4b",
        7,
        {"a": 1},
        [None, 5, "", "hf.co/x/y", "bad tag", "a;b"],
        [BUILD],
        [PLAIN, PLAIN],
    ],
)
def test_anything_that_is_not_a_valid_tag_is_ignored_and_the_row_stays(bad):
    m = sizing._model_from_row(_row(replaces=bad))
    assert m.ollama_tag == BUILD and sizing.is_safe_row(_row(replaces=bad))
    assert set(m.replaces) <= {PLAIN}  # at most the one valid, distinct, non-own tag survives


def test_an_exact_build_tag_passes_the_tag_check_and_is_handed_on_unchanged():
    assert sizing.is_safe_ollama_tag(BUILD) and sizing.is_safe_ollama_tag("gemma4:e2b-it-q4_K_M")
    assert sizing._model_from_row(_row()).ollama_tag == BUILD


def test_two_rows_never_claim_one_tag():
    a = sizing._model_from_row(_row(name="A", replaces=[PLAIN]))
    b = sizing._model_from_row(_row(name="B", ollama_tag="other:1b", replaces=[PLAIN, "x:1"]))
    c = sizing._model_from_row(_row(name="C", ollama_tag="x:1"))
    out = sizing._without_clashing_claims((a, b, c))
    assert out[0].replaces == (PLAIN,)  # the first claim wins
    assert out[1].replaces == ()  # B lost PLAIN to A, and "x:1" because it is C's own tag
    assert out[2].ollama_tag == "x:1"  # an own tag is never taken from a row
    assert sizing.row_for_tag(out, PLAIN) is out[0] and sizing.row_for_tag(out, "x:1") is out[2]


def test_the_loader_applies_the_claim_rule(tmp_path, monkeypatch):
    import json

    rows = [_row(name="A", replaces=[PLAIN]), _row(name="B", ollama_tag="o:1b", replaces=[PLAIN])]
    f = tmp_path / "cat.json"
    f.write_text(json.dumps({"generated": "2099-01-01", "models": rows}))
    monkeypatch.setattr(sizing, "_catalog_paths", lambda: [f])
    cat = sizing.load_catalog()
    assert [m.replaces for m in cat] == [(PLAIN,), ()]


# ── nothing changes for the shipped catalogue ──────────────────────────────────────────────────────────────


def test_no_shipped_row_replaces_a_tag_and_every_lookup_is_the_same_as_before():
    cat = sizing.load_catalog()
    assert all(m.replaces == () for m in cat)
    for m in cat:
        assert sizing.row_tags(m) == (m.ollama_tag,)
        assert sizing.row_for_tag(cat, m.ollama_tag) is next(
            c for c in cat if c.ollama_tag == m.ollama_tag
        )
        assert sizing.row_installed_tag(m, {m.ollama_tag}) == m.ollama_tag
        assert sizing.row_installed_tag(m, set()) == ""


@pytest.mark.parametrize("mem_gb", [8, 16, 24, 32, 64, 128])
def test_every_pick_and_suggestion_is_the_same_with_the_field_absent(mem_gb):
    cat = sizing.load_catalog()
    stripped = tuple(dataclasses.replace(m, replaces=()) for m in cat)
    assert sizing.recommend_by_family(
        mem_gb, kind="apple", catalog=cat
    ) == sizing.recommend_by_family(mem_gb, kind="apple", catalog=stripped)
    assert sizing.recommend(mem_gb, kind="apple", concurrency=1, catalog=cat) == sizing.recommend(
        mem_gb, kind="apple", concurrency=1, catalog=stripped
    )
    assert sizing.suggest_local_setup(mem_gb, "apple", catalog=cat) == sizing.suggest_local_setup(
        mem_gb, "apple", catalog=stripped
    )


# ── an install pinned to the old tag keeps working ─────────────────────────────────────────────────────────


def test_a_pinned_old_tag_finds_its_row_and_shows_as_installed():
    cat = _test_catalog()
    m = sizing.row_for_tag(cat, PLAIN)
    assert m is cat[0] and sizing.row_for_tag(cat, BUILD) is m
    assert sizing.row_installed_tag(m, {PLAIN}) == PLAIN
    assert sizing.row_installed_tag(m, {PLAIN + ":latest"}) == PLAIN
    assert sizing.row_installed_tag(m, {BUILD, PLAIN}) == BUILD  # the row's own tag first
    assert sizing.row_installed_tag(m, {"other:1b"}) == ""


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
    s.add(OrgSettings(org_id=o.id, deployment_topology="solo", local_model_chosen=False))
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def _wire(monkeypatch, app_mod, installed):
    cat = _test_catalog()
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))
    monkeypatch.setattr(sizing, "LOCAL_CATALOG", cat, raising=False)
    monkeypatch.setattr(sizing, "load_catalog", lambda: cat)
    monkeypatch.setattr(app_mod, "_installed_local_models", lambda cfg: list(installed))
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models", lambda self: list(installed)
    )
    pulled = []
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.append(tag))
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: None)
    return pulled


def test_the_setup_form_accepts_the_old_tag_and_does_not_pull_it_again(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path)
    pulled = _wire(monkeypatch, app_mod, [PLAIN])
    r = c.post("/setup/model", data={"choice": PLAIN}, follow_redirects=False)
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert (
        cfg.ollama_model == PLAIN and pulled == []
    )  # installed under the old tag: used, not downloaded again


def test_the_setup_form_pulls_the_exact_build_when_only_the_old_tag_is_chosen(
    tmp_path, monkeypatch
):
    c, app_mod = _client(tmp_path)
    pulled = _wire(monkeypatch, app_mod, [])
    c.post("/setup/model", data={"choice": PLAIN}, follow_redirects=False)
    assert pulled == [BUILD]  # a replaced plain tag is never handed to `ollama pull`
    c.post("/setup/model", data={"choice": BUILD}, follow_redirects=False)
    assert pulled == [BUILD, BUILD]  # an exact build tag goes through unchanged


def test_choosing_the_new_tag_while_the_old_one_is_installed_uses_the_installed_model(
    tmp_path, monkeypatch
):
    c, app_mod = _client(tmp_path)
    pulled = _wire(monkeypatch, app_mod, [PLAIN])
    c.post("/models/pull", data={"model_tag": BUILD}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == PLAIN and pulled == []  # never offered for download again


def test_the_picker_shows_the_row_as_installed_and_does_not_list_the_old_tag_again(
    tmp_path, monkeypatch
):
    _c, app_mod = _client(tmp_path)
    _wire(monkeypatch, app_mod, [PLAIN])
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models_with_sizes",
        lambda self: [{"name": PLAIN, "size_bytes": 3_000_000_000}],
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    view = app_mod._model_picker_view(cfg)
    shown = [m for fam in view["families"] for m in fam["models"]]
    assert [(m["tag"], m["installed"]) for m in shown] == [
        (BUILD, True)
    ]  # one entry, marked installed
    assert view["other_installed"] == []  # the old tag is not listed again as an unlisted install
