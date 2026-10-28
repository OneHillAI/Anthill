"""The frontier model catalog is data (a bundled JSON), ranked by intelligence x what fits the hardware,
and refreshable in-app - so the picker keeps up with a fast-moving open frontier without an app update."""

import json

from anthill.hosting import sizing


def test_bundled_catalog_loads_and_carries_intelligence():
    cat = sizing.load_catalog()
    assert len(cat) >= 12
    # the frontier is present: the leading Chinese labs + gpt-oss
    fams = {m.family for m in cat}
    assert {"GLM", "DeepSeek", "Qwen", "MiniMax", "Kimi", "gpt-oss"} <= fams
    # every model is servable (a pullable Ollama tag) and scored, with total >= active params (MoE)
    for m in cat:
        assert m.ollama_tag and m.intelligence > 0
        assert m.active_b <= m.params_b


def test_families_are_ordered_by_intelligence():
    order = sizing.families_by_intelligence(sizing.load_catalog())
    # the strongest family (by its best model) leads; the Western tail trails
    assert order.index("GLM") < order.index("Gemma")
    assert order.index("DeepSeek") < order.index("Phi")


def test_recommend_prefers_the_smartest_that_fits_not_the_largest():
    small = sizing.Model("dumb-but-huge", 30, "x/y", "z:30b", intelligence=10)
    smart = sizing.Model("smart-and-small", 8, "a/b", "c:8b", intelligence=90)
    # both fit an ample box; the smarter one wins even though it is smaller
    rec = sizing.recommend(80, kind="gpu", catalog=(small, smart))
    assert rec.recommended.name == "smart-and-small"


def test_refresh_override_supersedes_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    override = tmp_path / "model_catalog.json"
    override.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "name": "Brand New 4B",
                        "family": "New",
                        "origin": "Lab, X",
                        "params_b": 4,
                        "active_b": 4,
                        "ollama_tag": "brandnew:4b",
                        "hf_id": "lab/BrandNew-4B",
                        "intelligence": 99,
                    }
                ]
            }
        )
    )
    cat = sizing.load_catalog()
    # the refreshed override wins over the bundled seed
    assert [m.name for m in cat] == ["Brand New 4B"]
    assert sizing.recommend(32, kind="apple", catalog=None).recommended.name == "Brand New 4B"


def test_load_catalog_falls_back_when_override_is_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    (tmp_path / "model_catalog.json").write_text("}{ not json")
    # a corrupt override never breaks the picker: it falls back to the bundled seed
    cat = sizing.load_catalog()
    assert len(cat) >= 12 and all(m.ollama_tag for m in cat)


# --- supply chain: the catalog is a download+load instruction list, so a network row is untrusted -----


def test_safe_tag_accepts_real_library_refs():
    ok = [
        "qwen3.6",
        "qwen3.5:122b",
        "gpt-oss:120b",
        "llama3.3:70b",
        "glm-4.7-flash",
        "user/model:7b",
    ]
    for t in ok:
        assert sizing.is_safe_ollama_tag(t), t


def test_safe_tag_rejects_anything_that_redirects_the_pull():
    # each of these makes `ollama pull` fetch weights from a host the publisher does not control
    evil = [
        "hf.co/attacker/backdoored-GGUF",  # the app's own Advanced field proves this resolves
        "evil.com/model",  # a registry host as the first segment
        "https://evil.com/m.gguf",
        "registry.evil.com/ns/model:7b",
        "../../etc/passwd",
        "user/../../evil",
        "qwen3.6 ; rm -rf /",
        "",
    ]
    for t in evil:
        assert not sizing.is_safe_ollama_tag(t), t


def test_safe_hf_id_rejects_urls_and_traversal_but_allows_org_repo():
    assert sizing.is_safe_hf_id("Qwen/Qwen3.5-122B-A10B")
    assert sizing.is_safe_hf_id("")  # Ollama-only row
    for bad in ["https://evil.com/repo", "../../etc", "a/b/c", "evil", "org/repo/../x", "a b/c"]:
        assert not sizing.is_safe_hf_id(bad), bad


def test_is_safe_row_gates_on_identity_not_presentation():
    base = {"name": "M", "ollama_tag": "m:7b", "hf_id": "org/repo"}
    assert sizing.is_safe_row(base)
    # a hostile score only misranks; it must not by itself reject the row
    assert sizing.is_safe_row({**base, "intelligence": 999})
    # but a hostile pull/serve target must
    assert not sizing.is_safe_row({**base, "ollama_tag": "hf.co/evil/x"})
    assert not sizing.is_safe_row({**base, "hf_id": "https://evil/x"})
    assert not sizing.is_safe_row({**base, "quant_hf_id": "../../x"})
    assert not sizing.is_safe_row({"name": "M"})  # no tag at all
    assert not sizing.is_safe_row("nope")


def test_a_tampered_override_is_rejected_wholesale_not_partially_imported(tmp_path, monkeypatch):
    """The regression: one poisoned row must not drag the rest of an attacker's file into the catalog."""
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    (tmp_path / "model_catalog.json").write_text(
        json.dumps(
            {
                "models": [
                    {"name": "Looks Fine", "ollama_tag": "fine:7b", "hf_id": "org/fine"},
                    {
                        "name": "Backdoor",
                        "ollama_tag": "hf.co/attacker/evil-GGUF",
                        "intelligence": 99,
                    },
                ]
            }
        )
    )
    cat = sizing.load_catalog()
    names = [m.name for m in cat]
    assert "Backdoor" not in names  # obviously
    assert "Looks Fine" not in names  # and the tampered file is not cherry-picked either
    assert len(cat) >= 12  # fell back to the vetted bundled seed
