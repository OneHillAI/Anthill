"""Shared LoRA trainer: toolchain detection + the eval-gate orchestration.

Model-free: the GPU-side fine-tune and Ollama calls are mocked, so what's tested is the logic
that must be right regardless of hardware - pick the right toolchain, and **promote a candidate
only if it beats the current model on held-out gold** (a regression is discarded, never shipped).
"""

import pytest

from anthill.lifecycle.evaluate import EvalResult
from anthill.training import trainer

# ── toolchain detection ───────────────────────────────────────────────────────


def test_detect_toolchain_mlx_on_apple_silicon(monkeypatch):
    monkeypatch.setattr(trainer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(trainer.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        trainer.importlib.util, "find_spec", lambda name: object() if name == "mlx_lm" else None
    )
    assert trainer.detect_toolchain() == "mlx"


def test_detect_toolchain_peft_on_nvidia(monkeypatch):
    monkeypatch.setattr(trainer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(trainer.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        trainer.importlib.util,
        "find_spec",
        lambda name: object() if name in ("peft", "torch") else None,
    )
    assert trainer.detect_toolchain() == "peft"


def test_detect_toolchain_none_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(trainer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(trainer.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(trainer.importlib.util, "find_spec", lambda name: None)
    assert trainer.detect_toolchain() is None


def test_train_adapter_raises_without_a_toolchain(monkeypatch, tmp_path):
    monkeypatch.setattr(trainer, "detect_toolchain", lambda: None)
    with pytest.raises(trainer.TrainerError):
        trainer.train_adapter("d.jsonl", "qwen3:8b", out_dir=str(tmp_path))


# ── MLX base-model resolution (Ollama tag -> mlx-community repo) ───────────────


def test_mlx_base_model_maps_an_ollama_tag(monkeypatch):
    monkeypatch.delenv("ANTHILL_MLX_MODEL", raising=False)
    assert trainer._mlx_base_model("qwen2.5:3b") == "mlx-community/Qwen2.5-3B-Instruct-4bit"
    assert trainer._mlx_base_model("llama3.2:1b") == "mlx-community/Llama-3.2-1B-Instruct-4bit"


def test_mlx_base_model_passes_through_repos_and_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHILL_MLX_MODEL", raising=False)
    # already an HF repo id -> untouched
    assert trainer._mlx_base_model("mlx-community/Foo-4bit") == "mlx-community/Foo-4bit"
    # an existing local dir -> untouched
    assert trainer._mlx_base_model(str(tmp_path)) == str(tmp_path)


def test_mlx_base_model_env_override_wins(monkeypatch):
    monkeypatch.setenv("ANTHILL_MLX_MODEL", "mlx-community/Pinned-4bit")
    assert trainer._mlx_base_model("qwen2.5:3b") == "mlx-community/Pinned-4bit"


def test_mlx_base_model_raises_on_unmappable_tag(monkeypatch):
    monkeypatch.delenv("ANTHILL_MLX_MODEL", raising=False)
    with pytest.raises(trainer.TrainerError):
        trainer._mlx_base_model("some-obscure-model:7b")
    with pytest.raises(trainer.TrainerError):
        trainer._mlx_base_model("")


# ── MLX dataset staging (gold export -> mlx-lm train/valid.jsonl) ──────────────


def test_stage_mlx_dataset_converts_export_format(tmp_path):
    import json

    src = tmp_path / "gold.jsonl"
    src.write_text(
        json.dumps({"instruction": "Summarize", "input": "the doc", "output": "a summary"})
        + "\n"
        + json.dumps({"instruction": "Greet", "input": "", "output": "hello"})
        + "\n"
        + "   \n"  # blank line skipped
        + "not json\n"  # unparseable line skipped
        + json.dumps({"instruction": "no output", "input": "x"})  # missing output -> skipped
        + "\n"
    )
    data_dir = trainer._stage_mlx_dataset(str(src))
    train = (tmp_path / "mlx-data" / "train.jsonl").read_text().strip().splitlines()
    valid = (tmp_path / "mlx-data" / "valid.jsonl").read_text().strip().splitlines()
    assert data_dir.endswith("mlx-data")
    # two complete rows, split into disjoint train/valid (the third row is skipped: missing output)
    rendered = {json.loads(line)["text"] for line in train + valid}
    assert rendered == {"Summarize\n\nthe doc\n\na summary", "Greet\n\nhello"}
    assert len(train) == 1 and len(valid) == 1  # empty input collapses cleanly; valid held out


def test_stage_mlx_dataset_valid_split_is_disjoint_from_train(tmp_path):
    import json

    src = tmp_path / "gold.jsonl"
    src.write_text(
        "".join(
            json.dumps({"instruction": f"q{n}", "input": "", "output": f"a{n}"}) + "\n"
            for n in range(10)
        )
    )
    trainer._stage_mlx_dataset(str(src))
    train = set((tmp_path / "mlx-data" / "train.jsonl").read_text().strip().splitlines())
    valid = set((tmp_path / "mlx-data" / "valid.jsonl").read_text().strip().splitlines())
    assert valid and train.isdisjoint(valid)  # mlx never validates on a trained row


def test_stage_mlx_dataset_raises_when_empty(tmp_path):
    src = tmp_path / "empty.jsonl"
    src.write_text("\n\nnot json\n")
    with pytest.raises(trainer.TrainerError):
        trainer._stage_mlx_dataset(str(src))


# ── eval-gated orchestration ──────────────────────────────────────────────────


def _wire(monkeypatch, tmp_path, eval_result):
    """Mock the GPU/Ollama steps; return (registered, removed) trackers."""
    registered, removed = {}, []
    monkeypatch.setattr(trainer, "train_adapter", lambda *a, **k: str(tmp_path / "adapter"))
    monkeypatch.setattr(
        trainer,
        "register_with_ollama",
        lambda name, base, adapter, **k: registered.update(name=name),
    )
    monkeypatch.setattr(trainer, "remove_from_ollama", lambda name: removed.append(name))
    monkeypatch.setattr(trainer, "evaluate_models", lambda *a, **k: eval_result)
    return registered, removed


def _run(tmp_path):
    # run_training was a thin train_adapter + promote_if_better wrapper (removed as dead code);
    # call the two directly, exactly as the executor does, to keep the promote/eval-gate coverage.
    adapter = trainer.train_adapter("d.jsonl", "qwen3:8b", out_dir=str(tmp_path))
    return trainer.promote_if_better(
        adapter,
        base_model="qwen3:8b",
        model_name="acme-model-v3",
        version=3,
        eval_examples=[("q", "a")],
        current_model="acme-model-v2",
        inference_backend=object(),
    )


def test_promotes_when_candidate_wins(monkeypatch, tmp_path):
    # candidate (model_b) scores higher -> winner is the candidate
    res = EvalResult("acme-model-v2", "acme-model-v3", 5, 0.80, 0.90, 1, 4, 0)
    registered, removed = _wire(monkeypatch, tmp_path, res)
    out = _run(tmp_path)
    assert out.won_eval and out.model_version == 3
    assert registered["name"] == "acme-model-v3"
    assert removed == []  # winner is kept


def test_rejects_and_discards_when_candidate_loses(monkeypatch, tmp_path):
    # current (model_a) scores higher -> candidate must be discarded, current stays live
    res = EvalResult("acme-model-v2", "acme-model-v3", 5, 0.90, 0.80, 4, 1, 0)
    _registered, removed = _wire(monkeypatch, tmp_path, res)
    out = _run(tmp_path)
    assert not out.won_eval and out.model_version is None
    assert removed == ["acme-model-v3"]  # rejected candidate removed


def test_rejects_on_a_tie(monkeypatch, tmp_path):
    res = EvalResult("acme-model-v2", "acme-model-v3", 5, 0.85, 0.85, 0, 0, 5)
    _registered, removed = _wire(monkeypatch, tmp_path, res)
    out = _run(tmp_path)
    assert not out.won_eval and removed == ["acme-model-v3"]


def test_gate_rejects_unscored_candidate_against_a_live_model(monkeypatch, tmp_path):
    registered, _removed = _wire(monkeypatch, tmp_path, None)
    out = trainer.promote_if_better(
        str(tmp_path / "adapter"),
        base_model="qwen3:8b",
        model_name="acme-model-v3",
        version=3,
        eval_examples=[],  # nothing to score against -> never replace a live model
        current_model="acme-model-v2",
        inference_backend=object(),
    )
    assert not out.won_eval and out.model_version is None
    assert registered == {}  # rejected before it ever reached Ollama


def test_gate_promotes_a_first_model_unvalidated(monkeypatch, tmp_path):
    registered, _removed = _wire(monkeypatch, tmp_path, None)
    out = trainer.promote_if_better(
        str(tmp_path / "adapter"),
        base_model="qwen3:8b",
        model_name="acme-model-v1",
        version=1,
        eval_examples=[],
        current_model="qwen3:8b",
        inference_backend=object(),
        allow_unvalidated=True,
    )
    assert out.won_eval and out.model_version == 1
    assert registered["name"] == "acme-model-v1"


def test_local_mlx_gate_rejects_unscored_candidate_against_a_live_adapter():
    out = trainer.promote_local_mlx(
        "/tmp/adapter", base_model="qwen3:8b", version=2, eval_examples=[]
    )
    assert not out.won_eval and out.model_version is None
