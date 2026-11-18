"""Where a model call goes decides whether it stayed on this machine, not which backend made it (#33).

``stays_local`` fed the per-answer "answered on this device" badge, the audit log and the gates that skip
escalation and scrubbing. It used to say True for every Ollama, so an Ollama pointed at another machine (the
address is a user setting) was reported as local."""

import pytest

from anthill.inference.base import stays_local


@pytest.mark.parametrize(
    ("backend", "url"),
    [
        ("ollama", "http://localhost:11434"),
        ("ollama", "http://127.0.0.1:11434"),
        ("ollama", "http://127.0.1.1:11434"),  # all of 127.0.0.0/8 is loopback
        ("ollama", "http://[::1]:11434"),
        ("ollama", "HTTP://LOCALHOST:11434"),
        ("ollama", "http://localhost.:11434"),  # a trailing dot is the same name
        ("ollama", "http://my.localhost:11434"),
        ("ollama", "http://0.0.0.0:11434"),
        (
            "ollama",
            "http://host.docker.internal:11434",
        ),  # the host, from inside Docker (docker-compose.yml)
        ("ollama", "http://host.containers.internal:11434"),
        ("ollama", ""),  # no address set: the default Ollama on this machine
        ("ollama", "   "),
        ("openai", "http://127.0.0.1:8091/v1"),
        ("openai", "http://localhost:8091/v1"),
    ],
)
def test_an_address_on_this_machine_stays_local(backend, url):
    assert stays_local(backend, url) is True


@pytest.mark.parametrize(
    ("backend", "url"),
    [
        ("ollama", "http://192.168.1.20:11434"),  # a LAN box
        ("ollama", "http://10.0.0.5:11434"),
        ("ollama", "http://gpu-box.local:11434"),
        (
            "ollama",
            "http://ollama:11434",
        ),  # a bare name: not known to be this machine, so not claimed
        ("ollama", "https://ollama.example.com"),
        ("ollama", "http://localhost.evil.com:11434"),  # a name that only starts with localhost
        ("ollama", "http://127.0.0.1.evil.com:11434"),
        ("ollama", "http://evil.com/localhost"),
        (
            "ollama",
            "http://localhost@evil.com:11434",
        ),  # "localhost" is the user name; the host is evil.com
        ("ollama", "http://notlocalhost:11434"),
        ("ollama", "not a url"),
        ("openai", "https://api.berget.ai/v1"),
        ("openai", ""),
        ("openai", None),
    ],
)
def test_any_other_address_is_remote_whatever_the_backend(backend, url):
    assert stays_local(backend, url) is False


def test_an_ollama_with_no_address_at_all_is_the_default_local_one():
    assert stays_local("ollama", None) is True


# -- the badge a user sees --------------------------------------------------------------------------


def _stream_with_ollama_url(tmp_path, monkeypatch, url):
    from test_chat_stream_cancellation import _client

    from anthill.web.db import OrgSettings

    client, app_mod, conversation_id = _client(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    session.add(OrgSettings(org_id=1, ollama_url=url))
    session.commit()
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["An ", "answer."]))
    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})
    assert response.status_code == 200
    saved = app_mod._SessionFactory().query(app_mod.ChatMessage).filter_by(role="assistant").one()
    return response.text, saved


def test_a_remote_ollama_is_told_to_the_user_as_remote_and_saved_as_remote(tmp_path, monkeypatch):
    text, saved = _stream_with_ollama_url(tmp_path, monkeypatch, "http://192.168.1.20:11434")
    assert '"answered_locally": false' in text
    assert saved.answered_locally is False


def test_the_default_local_ollama_is_still_told_to_the_user_as_local(tmp_path, monkeypatch):
    text, saved = _stream_with_ollama_url(tmp_path, monkeypatch, "http://localhost:11434")
    assert '"answered_locally": true' in text
    assert saved.answered_locally is True
