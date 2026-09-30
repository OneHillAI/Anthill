"""ANTHILL_FORCE_MODEL hard pin: the router uses exactly the configured model for non-vision tasks
instead of loading the largest installed one (qwen3:14b over a configured qwen3:8b). Lets a constrained
deployment or a reproducible test pin the model. Vision still routes to a vision model. Opt-in."""

from anthill.routing.router import TaskRouter, TaskType


def test_pin_forces_the_model_for_non_vision_tasks(monkeypatch):
    r = TaskRouter(pinned_model="qwen3:8b")
    monkeypatch.setattr(
        r, "_installed_models", lambda: {"qwen3:8b", "qwen3:14b", "granite3.2-vision:2b"}
    )
    # without the pin, REASON prefers qwen3:14b over 8b (the reported bug); the pin forces 8b
    assert r.pick(TaskType.REASON) == "qwen3:8b"
    assert r.pick(TaskType.GENERAL) == "qwen3:8b"
    assert r.pick(TaskType.FAST) == "qwen3:8b"
    # ...but a text pin can't do vision, so vision still routes to a vision model
    assert r.pick(TaskType.VISION) == "granite3.2-vision:2b"


def test_no_pin_keeps_task_routing(monkeypatch):
    r = TaskRouter()  # no pin, no ANTHILL_FORCE_MODEL
    monkeypatch.delenv("ANTHILL_FORCE_MODEL", raising=False)
    r.pinned_model = ""
    monkeypatch.setattr(r, "_installed_models", lambda: {"qwen3:8b", "qwen3:14b"})
    assert r.pick(TaskType.REASON) == "qwen3:14b"  # task routing still upgrades REASON


def test_pin_falls_through_when_not_installed(monkeypatch):
    r = TaskRouter(pinned_model="qwen3:8b")
    monkeypatch.setattr(
        r, "_installed_models", lambda: {"qwen3:14b"}
    )  # the pinned 8b isn't installed
    # never substitute a different size silently for the pin; fall through to normal routing
    assert r.pick(TaskType.GENERAL) == "qwen3:14b"


def test_env_force_model_is_read(monkeypatch):
    monkeypatch.setenv("ANTHILL_FORCE_MODEL", "qwen3:8b")
    assert TaskRouter().pinned_model == "qwen3:8b"
    monkeypatch.delenv("ANTHILL_FORCE_MODEL", raising=False)
    assert TaskRouter().pinned_model == ""


def test_env_force_wins_over_a_passed_pin(monkeypatch):
    """#567: the chat route passes the account's configured model as `pinned_model` (#413). When an
    operator or an eval gate sets ANTHILL_FORCE_MODEL, that is a HARD override and must win over the
    account model - otherwise a security-sensitive turn (or a gate pinning qwen3:8b for injection
    resistance) silently serves the weaker account model instead."""
    monkeypatch.setenv("ANTHILL_FORCE_MODEL", "qwen3:8b")
    r = TaskRouter(pinned_model="qwen2.5:3b")  # the account model the chat route would pass
    assert r.pinned_model == "qwen3:8b"
    monkeypatch.setattr(r, "_installed_models", lambda: {"qwen2.5:3b", "qwen3:8b"})
    assert r.pick(TaskType.GENERAL) == "qwen3:8b"  # forced model serves, not the account 3b


def test_passed_pin_stands_when_no_env_force(monkeypatch):
    """Without ANTHILL_FORCE_MODEL the account pin (#413) is used unchanged - the env override is opt-in."""
    monkeypatch.delenv("ANTHILL_FORCE_MODEL", raising=False)
    assert TaskRouter(pinned_model="qwen2.5:3b").pinned_model == "qwen2.5:3b"


def test_backend_from_cfg_honors_env_force(monkeypatch):
    """#567: ANTHILL_FORCE_MODEL overrides the account's cfg.ollama_model for the non-router chat paths
    (review gate, memory, taskgen) that call backend.chat() directly, so the served local model matches
    the forced one rather than the account model."""
    import types

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_BACKEND", "ollama")
    cfg = types.SimpleNamespace(ollama_model="qwen2.5:3b", ollama_url="http://localhost:11434")
    monkeypatch.delenv("ANTHILL_FORCE_MODEL", raising=False)
    assert (
        app_mod._backend_from_cfg(cfg).model == "qwen2.5:3b"
    )  # account model stands when unforced
    monkeypatch.setenv("ANTHILL_FORCE_MODEL", "qwen3:8b")
    assert (
        app_mod._backend_from_cfg(cfg).model == "qwen3:8b"
    )  # hard override wins over the account model


def test_413_configured_model_served_not_a_larger_installed_one(monkeypatch):
    """#413 regression: the founder configured mistral-nemo:12b but qwen3:14b loaded and froze the box.
    The chat route now pins the configured LOCAL model, so the router serves exactly it - never the
    larger installed one - for every non-vision task."""
    r = TaskRouter(pinned_model="mistral-nemo:12b")
    monkeypatch.setattr(r, "_installed_models", lambda: {"mistral-nemo:12b", "qwen3:14b"})
    for task in (TaskType.GENERAL, TaskType.REASON, TaskType.FAST):
        assert r.pick(task) == "mistral-nemo:12b"
