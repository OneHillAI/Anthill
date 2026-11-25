"""The dashboard's shortcut to the model settings says what it does, not that something is waiting (a solid "Update your
model" button implied an update when there was none). Model-free."""

from bs4 import BeautifulSoup

from tests.test_dashboard_org_setup import _client


def _row(client):
    soup = BeautifulSoup(client.get("/").text, "html.parser")
    return soup, soup.select_one("a.qa-row[href='/personalize#model']")


def test_the_shortcut_to_the_model_settings_says_change_not_update(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, topology="solo", local_model_chosen=True)
    soup, row = _row(client)
    assert row is not None and row.get_text(" ", strip=True) == "Change your model"
    assert "Update your" not in soup.get_text()


def test_the_shortcut_is_a_quiet_row_not_the_filled_button_that_looks_like_a_to_do(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch, topology="solo", local_model_chosen=True)
    _soup, row = _row(client)
    assert "feature" not in row["class"] and row["class"] == ["qa-row"]
    assert (
        ".qa-row.feature" not in client.get("/").text
    )  # the filled style is gone, not just unused


def test_a_council_says_change_your_council(tmp_path, monkeypatch):
    import json

    import anthill.web.app as app_mod
    from anthill.web.db import OrgSettings

    client = _client(tmp_path, monkeypatch, topology="solo", local_model_chosen=True)
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.org_council_members = json.dumps(
        [{"model": "a", "provider": "local"}, {"model": "b", "provider": "local"}]
    )
    s.commit()
    s.close()
    _soup, row = _row(client)
    assert row.get_text(" ", strip=True) == "Change your council"


def test_only_an_admin_sees_the_shortcut(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, topology="org", role="member", local_model_chosen=True)
    _soup, row = _row(client)
    assert row is None
