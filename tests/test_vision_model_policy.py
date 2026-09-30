"""Vision-model policy: a fully-local, licence-clean, non-Chinese default tiered stack, with Qwen3-VL
reachable only through the opt-in "maximum accuracy" mode. Guards the licence fix (the non-commercial
qwen2.5vl:3b must never be selected). See docs/specs/local-vision-model-selection.md."""

from anthill.routing.router import (
    _MAX_ACCURACY_VISION_LEAD,
    _PREFERENCE,
    CATALOGUE,
    TaskRouter,
    TaskType,
    _preferred_tags,
)

# Chinese-origin vision families. The sovereign default must place these AFTER the licence-clean,
# non-Chinese models (IBM Granite / Mistral).
_CHINESE_VISION_FAMILIES = {"qwen2.5vl", "qwen3-vl", "qwen3.5"}
_NON_CHINESE_VISION_FAMILIES = {"granite3.2-vision", "mistral-small3.2"}


def _family(tag: str) -> str:
    return tag.split(":")[0]


# ── (a) the non-commercial qwen2.5vl:3b is gone and can never be selected ──────────────────────────


def test_qwen25vl_3b_removed_from_catalogue():
    assert all(spec.tag != "qwen2.5vl:3b" for spec in CATALOGUE)


def test_qwen25vl_3b_absent_from_every_preference():
    for task, tags in _PREFERENCE.items():
        assert "qwen2.5vl:3b" not in tags, task


def test_router_never_returns_qwen25vl_3b_even_if_installed(monkeypatch):
    # Hard licence backstop: qwen2.5vl:3b is under the Qwen RESEARCH LICENSE (non-commercial). Even
    # when it is the only installed vision model, the router refuses it rather than serve it.
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: {"qwen2.5vl:3b"})
    monkeypatch.setattr(router, "_model_capabilities", lambda tag: {"vision", "completion"})
    assert router.pick(TaskType.VISION, fallback_tag="") == ""


def test_router_prefers_clean_default_over_installed_qwen25vl_3b(monkeypatch):
    # With both installed, the licence-clean Granite default wins; :3b is never chosen.
    router = TaskRouter()
    monkeypatch.setattr(
        router, "_installed_models", lambda: {"granite3.2-vision:2b", "qwen2.5vl:3b"}
    )
    assert router.pick(TaskType.VISION) == "granite3.2-vision:2b"


def test_same_family_fallback_cannot_resurrect_qwen25vl_3b(monkeypatch):
    # qwen2.5vl:7b is a (licence-clean) preference entry; the same-family fallback must NOT pull in an
    # installed qwen2.5vl:3b just because it shares the family prefix.
    router = TaskRouter()
    monkeypatch.setattr(router, "_installed_models", lambda: {"qwen2.5vl:3b"})
    monkeypatch.setattr(router, "_model_capabilities", lambda tag: set())
    assert router.pick(TaskType.VISION, fallback_tag="") != "qwen2.5vl:3b"


# ── (b) default (max-accuracy off): non-Chinese first, no Qwen3-VL ─────────────────────────────────


def test_default_vision_preference_is_non_chinese_first():
    prefs = _preferred_tags(TaskType.VISION, vision_max_accuracy=False)
    first_chinese = next(
        (i for i, t in enumerate(prefs) if _family(t) in _CHINESE_VISION_FAMILIES), len(prefs)
    )
    last_non_chinese = max(
        (i for i, t in enumerate(prefs) if _family(t) in _NON_CHINESE_VISION_FAMILIES), default=-1
    )
    assert last_non_chinese != -1, "expected at least one non-Chinese vision model"
    assert last_non_chinese < first_chinese, prefs


def test_default_vision_preference_has_no_qwen3vl():
    prefs = _preferred_tags(TaskType.VISION, vision_max_accuracy=False)
    assert not any(_family(t) == "qwen3-vl" for t in prefs), prefs


def test_default_preference_is_100_percent_non_chinese():
    # The founder's explicit choice: no Chinese-origin model (any Qwen family) in ANY default
    # preference. Chinese models are reachable only via the max-accuracy opt-in or last-resort fallback.
    for task in (TaskType.VISION, TaskType.DOCUMENT):
        prefs = _preferred_tags(task, vision_max_accuracy=False)
        assert not any(_family(t) in _CHINESE_VISION_FAMILIES for t in prefs), (task, prefs)
    # And with phi-4-multimodal unavailable on Ollama, the VISION default is exactly Granite + Mistral.
    assert _preferred_tags(TaskType.VISION, vision_max_accuracy=False) == [
        "granite3.2-vision:2b",
        "mistral-small3.2:24b",
    ]


def test_default_router_picks_granite_first(monkeypatch):
    router = TaskRouter()  # max-accuracy off (default)
    monkeypatch.setattr(
        router,
        "_installed_models",
        lambda: {"granite3.2-vision:2b", "mistral-small3.2:24b", "qwen3-vl:8b"},
    )
    assert router.pick(TaskType.VISION) == "granite3.2-vision:2b"


def test_default_document_vision_entry_is_non_chinese_first(monkeypatch):
    router = TaskRouter()
    monkeypatch.setattr(
        router, "_installed_models", lambda: {"granite3.2-vision:2b", "qwen2.5vl:7b"}
    )
    assert router.pick(TaskType.DOCUMENT) == "granite3.2-vision:2b"


# ── (c) opt-in max-accuracy: Qwen3-VL preferred first ─────────────────────────────────────────────


def test_max_accuracy_preference_leads_with_qwen3vl():
    prefs = _preferred_tags(TaskType.VISION, vision_max_accuracy=True)
    assert prefs[: len(_MAX_ACCURACY_VISION_LEAD)] == _MAX_ACCURACY_VISION_LEAD
    assert _family(prefs[0]) == "qwen3-vl"


def test_max_accuracy_router_picks_qwen3vl_over_granite(monkeypatch):
    router = TaskRouter(vision_max_accuracy=True)
    monkeypatch.setattr(
        router, "_installed_models", lambda: {"granite3.2-vision:2b", "qwen3-vl:8b"}
    )
    assert router.pick(TaskType.VISION) == "qwen3-vl:8b"
    assert router.pick(TaskType.DOCUMENT) == "qwen3-vl:8b"


def test_max_accuracy_falls_back_to_clean_default_when_qwen3vl_absent(monkeypatch):
    # Opt-in on, but Qwen3-VL not installed: still serve the licence-clean default rather than nothing.
    router = TaskRouter(vision_max_accuracy=True)
    monkeypatch.setattr(router, "_installed_models", lambda: {"granite3.2-vision:2b"})
    assert router.pick(TaskType.VISION) == "granite3.2-vision:2b"


def test_env_var_enables_max_accuracy(monkeypatch):
    # Mirrors ANTHILL_FORCE_MODEL: lets the ingestion path / a deployment turn on the opt-in.
    monkeypatch.setenv("ANTHILL_VISION_MAX_ACCURACY", "1")
    assert TaskRouter().vision_max_accuracy is True
    monkeypatch.delenv("ANTHILL_VISION_MAX_ACCURACY", raising=False)
    assert TaskRouter().vision_max_accuracy is False


# ── autopull tag selection follows the opt-in ─────────────────────────────────────────────────────


def test_autopull_tag_defaults_to_granite_and_switches_on_opt_in():
    import types

    from anthill.web.app import _VISION_TAG, _VISION_TAG_MAX_ACCURACY, _vision_autopull_tag

    assert _VISION_TAG == "granite3.2-vision:2b"
    assert _family(_VISION_TAG) not in _CHINESE_VISION_FAMILIES
    assert _vision_autopull_tag(types.SimpleNamespace(vision_max_accuracy=False)) == _VISION_TAG
    assert (
        _vision_autopull_tag(types.SimpleNamespace(vision_max_accuracy=True))
        == _VISION_TAG_MAX_ACCURACY
    )
    assert _family(_VISION_TAG_MAX_ACCURACY) == "qwen3-vl"


# ── ingestion path honors the opt-in (its primary purpose: scanned-PDF / image OCR) ────────────────


def test_ingest_vision_config_honors_max_accuracy(monkeypatch):
    # The scanned-PDF / image ingestion path builds its own TaskRouter inside _vision_backend_config.
    # It must serve Qwen3-VL when the account opted in, and the licence-clean default otherwise.
    from anthill.inference.ollama import OllamaBackend
    from anthill.multimodal.reader import FileContent
    from anthill.routing.router import TaskRouter
    from anthill.wiki.ingest import _vision_backend_config

    monkeypatch.setattr(
        TaskRouter,
        "_installed_models",
        lambda self: {"granite3.2-vision:2b", "qwen3-vl:8b"},
    )
    backend = OllamaBackend("http://localhost:11434", "qwen2.5:3b")
    fc = FileContent(text="", images_b64=["img"], mime_type="image/png", source_name="scan.png")

    default_kwargs, _ = _vision_backend_config(fc, backend)
    assert default_kwargs["model"] == "granite3.2-vision:2b"

    boosted_kwargs, _ = _vision_backend_config(fc, backend, vision_max_accuracy=True)
    assert boosted_kwargs["model"] == "qwen3-vl:8b"


def test_ingest_signature_threads_the_flag():
    # Guard the wiring: ingest() and its helpers must accept vision_max_accuracy so the web upload
    # handlers can pass the account's OrgSettings flag down to the vision router.
    import inspect

    from anthill.wiki import ingest as ingest_mod

    for fn in (
        ingest_mod.ingest,
        ingest_mod.source_to_page,
        ingest_mod._source_content_to_page,
        ingest_mod._ingest_with_vision,
        ingest_mod._vision_backend_config,
    ):
        assert "vision_max_accuracy" in inspect.signature(fn).parameters, fn.__name__
