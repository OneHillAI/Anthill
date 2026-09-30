"""Regression coverage for promoted local MLX chat streaming (#757)."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _mlx_chat(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    session.add(user)
    session.flush()
    session.add(
        OrgSettings(
            org_id=org.id,
            deployment_topology="solo",
            local_serve_url="http://127.0.0.1:11435/v1",
            local_serve_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
        )
    )
    conversation = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conversation)
    session.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conversation.id


def test_promoted_mlx_chat_keeps_its_model_and_skips_ollama_discovery(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _mlx_chat(tmp_path, monkeypatch)
    sent = {}
    discovery_urls = []

    class _Response:
        is_success = True

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"Hello"}}]}'
            yield "data: [DONE]"

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    def _stream(method, url, *, json, headers, timeout):
        sent.update(json)
        return _Stream()

    def _get(url, **kwargs):
        discovery_urls.append(url)
        raise AssertionError("OpenAI-compatible chat must not use Ollama discovery")

    monkeypatch.setattr("anthill.inference.openai_compat.httpx.stream", _stream)
    monkeypatch.setattr("anthill.routing.router.httpx.get", _get)

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert "Hello" in response.text
    assert sent["model"] == "mlx-community/Qwen2.5-3B-Instruct-4bit"
    assert discovery_urls == []

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == "Hello"
    assert assistant.model == "mlx-community/Qwen2.5-3B-Instruct-4bit"
    assert assistant.generation_failed is False


def test_promoted_mlx_agent_chat_uses_the_configured_model(tmp_path, monkeypatch):
    client, _, conversation_id = _mlx_chat(tmp_path, monkeypatch)
    captured = {}

    class _Executor:
        def __init__(self, backend, tools, *, model, **kwargs):
            captured["model"] = model

        def stream(self, message, *, context):
            yield "Agent answer"

    monkeypatch.setattr("anthill.agent.executor.AgentExecutor", _Executor)

    response = client.get(
        f"/chat/{conversation_id}/stream", params={"message": "Test", "agent_mode": "true"}
    )

    assert response.status_code == 200
    assert "Agent answer" in response.text
    assert captured["model"] == "mlx-community/Qwen2.5-3B-Instruct-4bit"


def test_failed_mlx_chat_emits_and_persists_one_error_without_training(tmp_path, monkeypatch):
    import httpx

    from anthill.web.db import TrainingExample

    client, app_mod, conversation_id = _mlx_chat(tmp_path, monkeypatch)
    detail = "Repo id is invalid: qwen2.5:3b"
    request = httpx.Request("POST", "http://127.0.0.1:11435/v1/chat/completions")
    failed_response = httpx.Response(
        404,
        stream=httpx.ByteStream(f'{{"error":"{detail}"}}'.encode()),
        request=request,
    )

    class _Stream:
        def __enter__(self):
            return failed_response

        def __exit__(self, *args):
            failed_response.close()
            return False

    monkeypatch.setattr(
        "anthill.inference.openai_compat.httpx.stream", lambda *args, **kwargs: _Stream()
    )
    post_failure_calls = []
    monkeypatch.setattr(
        app_mod,
        "_distil_memory_from_chat",
        lambda *args, **kwargs: post_failure_calls.append("memory"),
    )
    monkeypatch.setattr(
        "anthill.web.metrics.record",
        lambda *args, **kwargs: post_failure_calls.append("metrics"),
    )

    response = client.get(f"/chat/{conversation_id}/stream", params={"message": "Test"})

    assert response.status_code == 200
    assert response.text.count(detail) == 1
    assert "data: [DONE]" in response.text

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == (
        f'⚠️ Generation failed - The model endpoint returned 404: {{"error":"{detail}"}}'
    )
    assert assistant.generation_failed is True
    assert session.query(TrainingExample).count() == 0
    assert post_failure_calls == []


def test_failed_research_preserves_partial_output_and_skips_success_paths(tmp_path, monkeypatch):
    from anthill.web.db import TrainingExample

    client, app_mod, conversation_id = _mlx_chat(tmp_path, monkeypatch)
    detail = "The model endpoint returned 503: temporarily unavailable"
    post_failure_calls = []

    def _failed_research(topic, *, backend):
        yield "Partial research result"
        raise RuntimeError(detail)

    monkeypatch.setattr("anthill.research.research_stream", _failed_research)
    monkeypatch.setattr(
        app_mod,
        "_distil_memory_from_chat",
        lambda *args, **kwargs: post_failure_calls.append("memory"),
    )
    monkeypatch.setattr(
        "anthill.web.metrics.record",
        lambda *args, **kwargs: post_failure_calls.append("metrics"),
    )

    response = client.get(
        f"/chat/{conversation_id}/stream",
        params={"message": "Research this", "research": "true", "confirm": "true"},
    )

    assert response.status_code == 200
    assert "Partial research result" in response.text
    assert response.text.count(detail) == 1
    assert "data: [DONE]" in response.text

    session = app_mod._SessionFactory()
    assistant = (
        session.query(db_mod.ChatMessage)
        .filter_by(conversation_id=conversation_id, role="assistant")
        .one()
    )
    assert assistant.content == ("Partial research result\n\n⚠️ Generation failed - " + detail)
    assert assistant.generation_failed is True
    assert session.query(TrainingExample).count() == 0
    assert post_failure_calls == []


def test_memory_distillation_and_badges_use_explicit_failure_state(tmp_path, monkeypatch):
    client, app_mod, conversation_id = _mlx_chat(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    conversation = session.query(db_mod.Conversation).filter_by(id=conversation_id).one()
    successful_marker = db_mod.ChatMessage(
        conversation_id=conversation_id,
        role="assistant",
        content="A valid quote: " + app_mod._CHAT_FAILURE_MARKER + "is display text.",
    )
    failed = db_mod.ChatMessage(
        conversation_id=conversation_id,
        role="assistant",
        content="Partial output\n\n" + app_mod._CHAT_FAILURE_MARKER + "endpoint down",
        generation_failed=True,
    )
    session.add_all(
        [
            db_mod.ChatMessage(conversation_id=conversation_id, role="user", content="one"),
            db_mod.ChatMessage(
                conversation_id=conversation_id, role="assistant", content="first answer"
            ),
            db_mod.ChatMessage(conversation_id=conversation_id, role="user", content="two"),
            successful_marker,
            db_mod.ChatMessage(conversation_id=conversation_id, role="user", content="three"),
            failed,
            db_mod.ChatMessage(conversation_id=conversation_id, role="user", content="four"),
            db_mod.ChatMessage(
                conversation_id=conversation_id, role="assistant", content="final answer"
            ),
        ]
    )
    session.commit()
    captured = []
    monkeypatch.setattr(
        "anthill.memory.extract", lambda transcript, backend: captured.append(transcript) or []
    )

    app_mod._distil_memory_from_chat(session, 1, conversation.user_id, conversation, object())

    assert len(captured) == 1
    assert "Partial output" not in captured[0]
    assert "endpoint down" not in captured[0]
    assert successful_marker.content in captured[0]
    assert "assistant: final answer" in captured[0]

    page = client.get(f"/chat/{conversation_id}").text
    successful_meta = page.split(f'id="bubble-{successful_marker.id}"', 1)[1].split(
        '<button class="thumbs"', 1
    )[0]
    failed_meta = page.split(f'id="bubble-{failed.id}"', 1)[1].split('<button class="thumbs"', 1)[0]
    assert "💻 local" in successful_meta
    assert "💻 local" not in failed_meta


def test_create_tables_adds_generation_failure_state_to_existing_chat_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY)"))

    db_mod.create_tables(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("chat_messages")}
    assert "generation_failed" in columns
