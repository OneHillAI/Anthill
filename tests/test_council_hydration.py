"""_council_builder.html must reflect the actually-saved model on reopen, not always render every
checkbox unchecked - founder report (2026-09-28): "every time I go into the model settings it has
[a different model] selected and ... chooses this instead of keeping my selected model." Traced to a
genuine gap: the builder had no hydration logic at all, so a Settings reopen never matched reality,
and a save from that mismatched state could silently swap the real choice out from under the user."""

import re

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _client(tmp_path, monkeypatch):
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
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def _fitting_tags(body: str) -> list[str]:
    """Every value="..." inside the local (fitting) council-row checkboxes, in DOM order."""
    local_section = body[body.index('id="council-local"') : body.index('id="council-cloud"')]
    return re.findall(r'class="csel csel-local"[^>]*value="([^"]+)"', local_section) or re.findall(
        r'value="([^"]+)"[^>]*class="csel csel-local"', local_section
    )


def test_council_builder_checks_the_actually_saved_model_on_reopen(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path, monkeypatch)
    tags = _fitting_tags(c.get("/personalize").text)
    assert len(tags) >= 2, "need at least 2 fitting models on this test machine to prove selection"
    saved_tag = tags[
        1
    ]  # deliberately NOT the first one, to prove this isn't just "first is checked"

    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.ollama_model = saved_tag
    s.commit()

    body = c.get("/personalize").text
    local_section = body[body.index('id="council-local"') : body.index('id="council-cloud"')]
    # exactly the saved tag's row is checked - not the first one, not none of them
    for tag in tags:
        row = local_section[
            local_section.index(f'value="{tag}"') - 5 : local_section.index(f'value="{tag}"') + 400
        ]
        is_checked = " checked" in row.split("onchange=")[0]
        assert is_checked == (tag == saved_tag), (
            f"tag={tag} checked={is_checked} expected={tag == saved_tag}"
        )


def test_council_builder_checks_a_saved_multi_model_council_in_lead_order(tmp_path, monkeypatch):
    import json

    c, app_mod = _client(tmp_path, monkeypatch)
    tags = _fitting_tags(c.get("/personalize").text)
    assert len(tags) >= 2
    lead, second = tags[1], tags[0]  # lead deliberately not first in DOM/catalog order

    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.org_council_members = json.dumps([{"model": lead}, {"model": second}])
    s.commit()

    body = c.get("/personalize").text
    assert f'CC_SAVED_ORDER = ["{lead}", "{second}"]' in body.replace("'", '"')


def test_settings_reopen_without_changes_does_not_clear_the_saved_model(tmp_path, monkeypatch):
    # The regression this whole fix targets: reopening "Change where it runs" and saving again (for
    # an unrelated reason, e.g. just flipping the region preference) must not wipe the real model.
    c, app_mod = _client(tmp_path, monkeypatch)
    tags = _fitting_tags(c.get("/personalize").text)
    saved_tag = tags[0]

    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.ollama_model = saved_tag
    s.commit()

    # Simulates the hydrated checkbox state being resubmitted unchanged.
    c.post(
        "/personalize/compute",
        data={"compute": "local", "council_models": [saved_tag]},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == saved_tag
