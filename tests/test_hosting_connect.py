"""Org endpoint validation + model-source discovery (the connect/discover engine for P1)."""

from anthill.hosting import endpoint, source

# ── endpoint validation (network calls injected) ─────────────────────────────────

EP = endpoint.OrgEndpoint(base_url="https://gpu.myorg.example/v1", api_key="k", model="qwen2.5:32b")


def test_validate_all_pass():
    v = endpoint.validate(
        EP,
        list_models=lambda: ["qwen2.5:32b", "llama3.3:70b"],
        chat=lambda prompt: "ok",
    )
    assert v.ok
    assert [c.name for c in v.checks] == ["reachable", "model_available", "round_trip"]
    assert all(c.ok for c in v.checks)


def test_validate_unreachable_stops_at_first_check():
    def _boom():
        raise OSError("connection refused")

    v = endpoint.validate(EP, list_models=_boom, chat=lambda p: "ok")
    assert not v.ok
    assert len(v.checks) == 1
    assert v.checks[0].name == "reachable" and not v.checks[0].ok
    assert "connection refused" in v.checks[0].detail


def test_validate_model_not_served():
    v = endpoint.validate(EP, list_models=lambda: ["llama3.3:70b"], chat=lambda p: "ok")
    assert not v.ok
    last = v.checks[-1]
    assert last.name == "model_available" and not last.ok
    assert "qwen2.5:32b" in last.detail


def test_validate_round_trip_failure_and_empty():
    # a chat that raises
    v = endpoint.validate(
        EP,
        list_models=lambda: ["qwen2.5:32b"],
        chat=lambda p: (_ for _ in ()).throw(RuntimeError("502")),
    )
    assert not v.ok and v.checks[-1].name == "round_trip" and "502" in v.checks[-1].detail
    # a chat that returns nothing
    v2 = endpoint.validate(EP, list_models=lambda: ["qwen2.5:32b"], chat=lambda p: "   ")
    assert not v2.ok and v2.checks[-1].name == "round_trip"


def test_validate_default_model_accepts_any():
    ep = endpoint.OrgEndpoint(base_url="https://x/v1")  # no specific model -> server default
    v = endpoint.validate(ep, list_models=lambda: ["whatever"], chat=lambda p: "ok")
    assert v.ok


# ── model sourcing ────────────────────────────────────────────────────────────────


def test_params_from_name():
    assert source.params_from_name("qwen2.5:32b") == 32.0
    assert source.params_from_name("meta-llama/Llama-3.3-70B-Instruct") == 70.0
    assert source.params_from_name("Qwen2.5-1.5B") == 1.5
    assert source.params_from_name("Qwen2.5 235B") == 235.0
    assert source.params_from_name("some-embedding-model") is None  # no size token
    assert source.params_from_name("mymodel-q4_K_M") is None  # quant, not a size


def test_builtin_catalog_has_params_and_license():
    cat = source.builtin_catalog()
    by_name = {m.name: m for m in cat}
    assert by_name["Qwen3.5 27B"].params_b == 27 and by_name["Qwen3.5 27B"].license == "Apache-2.0"
    assert by_name["Llama 3.3 70B"].license == "Llama Community"
    assert by_name["GLM-5.2"].license == "MIT"  # the catalog carries each model's own licence
    assert all(m.source == "builtin" for m in cat)


def test_every_builtin_model_has_both_servable_ids():
    # a display name alone is not servable: every catalog entry must carry a real HF id + Ollama tag,
    # so the picker can never again offer a name that fails at deploy time.
    from anthill.hosting.sizing import DEFAULT_CATALOG

    for m in DEFAULT_CATALOG:
        assert m.hf_id and "/" in m.hf_id, f"{m.name} missing/invalid hf_id"
        # a pullable Ollama tag; a bare name (no ":size") is valid for a single-variant model (glm-5.2)
        assert m.ollama_tag, f"{m.name} missing ollama_tag"


def test_gated_models_are_flagged_in_the_catalog():
    by_name = {m.name: m for m in source.builtin_catalog()}
    assert by_name["Llama 3.3 70B"].gated and by_name["Gemma 3 27B"].gated  # HF-gated
    assert not by_name["Qwen3.5 27B"].gated and not by_name["GLM-5.2"].gated  # open


def test_servable_id_resolves_display_name_per_stack():
    # cloud/vLLM wants the HF repo id; on-prem Ollama wants the tag
    assert (
        source.servable_id("Llama 3.3 70B", prefer_hf=True) == "meta-llama/Llama-3.3-70B-Instruct"
    )
    assert source.servable_id("Llama 3.3 70B", prefer_hf=False) == "llama3.3:70b"
    assert source.servable_id("gpt-oss 20b", prefer_hf=True) == "openai/gpt-oss-20b"  # case-insens
    # an advanced user's raw tag / id is left untouched, and blank stays blank
    assert source.servable_id("my-org/Custom-7B", prefer_hf=True) == "my-org/Custom-7B"
    assert source.servable_id("qwen2.5:7b", prefer_hf=False) == "qwen2.5:7b"
    assert source.servable_id("", prefer_hf=True) == ""
