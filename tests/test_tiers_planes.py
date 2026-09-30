"""Solo/Team/Org tiers - the planes layer (PR-A): the team tier, is_org_mode, and team routing
(local model in Solo, org cloud model in an org, PlaneUnavailable when the org backend is down)."""

from types import SimpleNamespace

import pytest

from anthill import planes
from anthill.web.plane_routing import PlaneUnavailable, plane_inference


def test_team_is_a_plane():
    assert planes.PLANES == ("solo", "team", "org")
    assert planes.normalize("team") == "team"
    assert planes.normalize("bogus") == "solo"


def test_is_org_mode():
    assert planes.is_org_mode(SimpleNamespace(org_backend_status="validated"))
    assert planes.is_org_mode(SimpleNamespace(org_backend_status="error"))  # configured, not ready
    assert not planes.is_org_mode(SimpleNamespace(org_backend_status="unconfigured"))
    assert not planes.is_org_mode(SimpleNamespace(org_backend_status=""))
    assert not planes.is_org_mode(None)


def test_route_team_solo_vs_org():
    solo = planes.route("team", org_mode=False)
    assert solo.model_source == "local" and solo.wiki_scope == "team"
    assert not solo.requires_connection and not solo.reads_org
    org = planes.route("team", org_mode=True)
    assert org.model_source == "org" and org.wiki_scope == "team"
    assert org.requires_connection and org.reads_org
    assert planes.route("solo").wiki_scope == "personal"  # unchanged


def _cfg(**kw):
    base = {
        "org_backend_status": "unconfigured",
        "org_model_endpoint": "",
        "org_model": "",
        "org_model_key_enc": "",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "qwen2.5:3b",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_team_inference_solo_is_local_with_personal():
    pi = plane_inference("team", _cfg(), decrypt=lambda x: x)
    assert pi.plane == "team" and pi.backend == "ollama"
    assert pi.wiki_scope == "team" and pi.use_personal_context is True


def test_team_inference_org_is_cloud_without_personal():
    cfg = _cfg(
        org_backend_status="validated",
        org_model_endpoint="http://org:8000/v1",
        org_model="qwen2.5:14b",
    )
    pi = plane_inference("team", cfg, decrypt=lambda x: x)
    assert pi.plane == "team" and pi.backend == "openai"
    assert pi.base_url == "http://org:8000/v1" and pi.wiki_scope == "team"
    assert pi.use_personal_context is False  # team-in-org is cloud -> personal excluded


def test_team_inference_org_configured_but_unreachable_raises():
    # is_org_mode True (configured) but the backend is not validated/reachable -> never falls to local
    cfg = _cfg(org_backend_status="error", org_model_endpoint="")
    with pytest.raises(PlaneUnavailable):
        plane_inference("team", cfg, decrypt=lambda x: x)
