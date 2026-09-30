"""Shared, backend-agnostic LoRA trainer (ARCHITECTURE §7.5 'locked design').

Turns a (PII-scrubbed) gold dataset + a base model into a candidate LoRA adapter, eval-gates
it against the current model on held-out gold, and - only if it wins - registers it with
Ollama as the org's next model version. Every training backend (AWS / GCP / Azure / IBM /
on-prem / neocloud) runs THIS on its GPU; the backend supplies only the GPU + transport.

The fine-tune step picks a toolchain by hardware - **PEFT/QLoRA** on NVIDIA, **MLX-LoRA** on
Apple Silicon. Those libraries are imported lazily and are NOT base dependencies (they live in
the GPU/training environment), so importing this module is always safe. The orchestration,
eval-gate, and Ollama registration here are pure-Python and fully tested; the two fine-tune
internals are the parts that need a real GPU to exercise.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..lifecycle.evaluate import evaluate_models


class TrainerError(RuntimeError):
    """Training could not run (missing toolchain, bad dataset, or registration failure)."""


@dataclass
class TrainOutcome:
    """What a training run produced (maps onto the backend's TrainingResult)."""

    adapter_path: str
    won_eval: bool
    model_version: int | None  # set only when promoted
    detail: str


# ── toolchain detection ───────────────────────────────────────────────────────


def detect_toolchain() -> str | None:
    """Which LoRA toolchain is usable here: ``mlx`` (Apple Silicon) | ``peft`` (NVIDIA) | None."""
    is_mac_arm = platform.system() == "Darwin" and platform.machine() == "arm64"
    if is_mac_arm and importlib.util.find_spec("mlx_lm") is not None:
        return "mlx"
    if (
        importlib.util.find_spec("peft") is not None
        and importlib.util.find_spec("torch") is not None
    ):
        return "peft"
    return None


# ── the GPU-side fine-tune (lazy-imported; needs a real GPU to exercise) ───────


def train_adapter(
    dataset_path: str,
    base_model: str,
    *,
    out_dir: str,
    toolchain: str | None = None,
    rank: int = 16,
    epochs: int = 3,
) -> str:
    """Fine-tune ``base_model`` on ``dataset_path`` (jsonl) into a LoRA adapter under
    ``out_dir``; return the adapter path. Raises ``TrainerError`` if no toolchain is present."""
    tc = toolchain or detect_toolchain()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    if tc == "mlx":
        return _train_mlx(dataset_path, base_model, out_dir=out_dir, rank=rank, epochs=epochs)
    if tc == "peft":
        return _train_peft(dataset_path, base_model, out_dir=out_dir, rank=rank, epochs=epochs)
    raise TrainerError(
        "No LoRA toolchain available. Install mlx-lm (Apple Silicon) or peft+torch (NVIDIA) "
        "in the training/GPU environment."
    )


def _train_peft(dataset_path, base_model, *, out_dir, rank, epochs) -> str:
    """QLoRA fine-tune on an NVIDIA GPU via PEFT + TRL (lazy imports)."""
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer
    except Exception as e:  # missing deps in this environment
        raise TrainerError(f"PEFT/QLoRA toolchain not importable: {e}") from e

    ds = load_dataset("json", data_files=dataset_path, split="train")
    hf_model = _hf_base_model(base_model)  # the served tag (e.g. "qwen3:8b") is not an HF repo id
    tok = AutoTokenizer.from_pretrained(hf_model)
    model = AutoModelForCausalLM.from_pretrained(
        hf_model, torch_dtype=torch.bfloat16, device_map="auto"
    )
    peft_cfg = LoraConfig(r=rank, lora_alpha=rank * 2, lora_dropout=0.05, task_type="CAUSAL_LM")

    def _fmt(row):
        instr, ctx, out = row.get("instruction", ""), row.get("input", ""), row.get("output", "")
        prompt = f"{instr}\n\n{ctx}".strip()
        return {"text": f"{prompt}\n\n{out}{tok.eos_token}"}

    trainer = SFTTrainer(
        model=model,
        train_dataset=ds.map(_fmt),
        peft_config=peft_cfg,
        args=SFTConfig(output_dir=out_dir, num_train_epochs=epochs, dataset_text_field="text"),
    )
    trainer.train()
    trainer.model.save_pretrained(out_dir)
    return out_dir


def _mlx_base_model(base_model: str) -> str:
    """Resolve the base model to something mlx-lm can load - an HF repo id or a local model dir. The
    served model is often an Ollama tag (e.g. "qwen2.5:3b") that mlx-lm cannot read, so map the common
    tags to their mlx-community 4-bit repos; pass through anything that already looks like a repo/path.
    ``ANTHILL_MLX_MODEL`` overrides with an exact repo."""
    m = (base_model or "").strip()
    if not m:
        raise TrainerError("No base model to fine-tune.")
    override = os.environ.get("ANTHILL_MLX_MODEL", "").strip()
    if override:
        return override
    if "/" in m or os.path.exists(m):  # already an HF repo id or a local path
        return m
    name, _, size = m.partition(":")
    size = size.upper()  # "3b" -> "3B"
    repo = {
        "qwen2.5": f"mlx-community/Qwen2.5-{size}-Instruct-4bit",
        "qwen3": f"mlx-community/Qwen3-{size}-4bit",
        "llama3.2": f"mlx-community/Llama-3.2-{size}-Instruct-4bit",
        "llama3.1": f"mlx-community/Meta-Llama-3.1-{size}-Instruct-4bit",
        "mistral": f"mlx-community/Mistral-{size}-Instruct-v0.3-4bit",
        "gemma2": f"mlx-community/gemma-2-{size}-it-4bit",
    }.get(name.lower())
    if repo and size:
        return repo
    raise TrainerError(
        f"Cannot map the served model '{m}' to an MLX model automatically. Set ANTHILL_MLX_MODEL to an "
        "mlx-community repo (e.g. mlx-community/Qwen2.5-3B-Instruct-4bit)."
    )


def _hf_base_model(base_model: str) -> str:
    """Resolve the base model to a Hugging Face repo id that transformers can load. The served model is
    usually an Ollama tag (e.g. "qwen3:8b") that ``AutoModelForCausalLM.from_pretrained`` cannot resolve,
    so map the common tags to their HF repos; pass through anything that already looks like a repo/path.
    ``ANTHILL_HF_MODEL`` overrides with an exact repo (an escape hatch for an unmapped or gated model)."""
    m = (base_model or "").strip()
    if not m:
        raise TrainerError("No base model to fine-tune.")
    override = os.environ.get("ANTHILL_HF_MODEL", "").strip()
    if override:
        return override
    if "/" in m or os.path.exists(m):  # already an HF repo id or a local path
        return m
    name, _, size = m.partition(":")
    size = size.upper()  # "8b" -> "8B"
    repo = {
        "qwen2.5": f"Qwen/Qwen2.5-{size}-Instruct",
        "qwen3": f"Qwen/Qwen3-{size}",
        "llama3.2": f"meta-llama/Llama-3.2-{size}-Instruct",
        "llama3.1": f"meta-llama/Llama-3.1-{size}-Instruct",
        "mistral": f"mistralai/Mistral-{size}-Instruct-v0.3",
        "gemma2": f"google/gemma-2-{size.lower()}-it",
    }.get(name.lower())
    if repo and size:
        return repo
    raise TrainerError(
        f"Cannot map the served model '{m}' to a Hugging Face repo automatically. Set ANTHILL_HF_MODEL "
        "to an HF repo id (e.g. Qwen/Qwen3-8B)."
    )


def _stage_mlx_dataset(dataset_path: str) -> str:
    """mlx-lm wants a directory of train/valid.jsonl in a {"text": ...} format; the gold export is
    {instruction,input,output} jsonl. Convert + write train.jsonl (and a small valid.jsonl, which mlx-lm
    requires for its periodic eval) into a fresh dir, and return that dir."""
    rows: list[dict] = []
    with open(dataset_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            instr = (r.get("instruction") or "").strip()
            ctx = (r.get("input") or "").strip()
            out = (r.get("output") or "").strip()
            if not (instr and out):
                continue
            prompt = f"{instr}\n\n{ctx}".strip()
            rows.append({"text": f"{prompt}\n\n{out}"})
    if not rows:
        raise TrainerError("No usable training rows in the dataset.")
    data_dir = Path(dataset_path).parent / "mlx-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Carve the valid slice OUT of train so mlx-lm's periodic eval isn't scoring trained rows.
    k = max(1, len(rows) // 10)
    valid, train = (rows, rows) if len(rows) == 1 else (rows[:k], rows[k:])
    (data_dir / "train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in train))
    (data_dir / "valid.jsonl").write_text("".join(json.dumps(r) + "\n" for r in valid))
    return str(data_dir)


def _train_mlx(dataset_path, base_model, *, out_dir, rank, epochs) -> str:
    """LoRA fine-tune on Apple Silicon via the mlx-lm CLI (lazy: requires mlx-lm). ``rank`` is reused as
    the number of layers to fine-tune (mlx-lm's top-level knob; the LoRA rank uses mlx-lm's default)."""
    if importlib.util.find_spec("mlx_lm") is None:
        raise TrainerError("mlx-lm not installed; cannot train on Apple Silicon.")
    model = _mlx_base_model(base_model)
    data_dir = _stage_mlx_dataset(dataset_path)
    cmd = [
        sys.executable,
        "-m",
        "mlx_lm",
        "lora",
        "--model",
        model,
        "--train",
        "--data",
        data_dir,
        "--fine-tune-type",
        "lora",
        "--num-layers",
        str(max(1, min(rank, 16))),
        "--iters",
        str(max(1, epochs) * 100),
        "--batch-size",
        "1",
        "--adapter-path",
        out_dir,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TrainerError(f"mlx-lm LoRA training failed: {(proc.stderr or proc.stdout)[-600:]}")
    return out_dir


# ── Ollama registration (org-side) ────────────────────────────────────────────


def register_with_ollama(
    model_name: str, base_model: str, adapter_path: str, *, ollama_url: str | None = None
) -> None:
    """Register ``model_name`` in Ollama from ``base_model`` + the LoRA ``adapter_path`` via a
    ``FROM`` / ``ADAPTER`` Modelfile. Raises ``TrainerError`` on failure."""
    modelfile = Path(adapter_path).parent / f"Modelfile.{model_name}"
    modelfile.write_text(f"FROM {base_model}\nADAPTER {adapter_path}\n")
    proc = subprocess.run(
        ["ollama", "create", model_name, "-f", str(modelfile)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise TrainerError(f"`ollama create {model_name}` failed: {proc.stderr[-500:]}")


def remove_from_ollama(model_name: str) -> None:
    """Best-effort removal of a rejected candidate model (never raises)."""
    try:
        subprocess.run(["ollama", "rm", model_name], capture_output=True, text=True)
    except Exception:
        pass


# ── orchestration: train -> register candidate -> eval-gate -> promote/discard ─


def promote_if_better(
    adapter_path: str,
    *,
    base_model: str,
    model_name: str,
    version: int,
    eval_examples: list[tuple[str, str]],
    current_model: str,
    inference_backend,
    ollama_url: str | None = None,
    sample: int = 20,
    allow_unvalidated: bool = False,
) -> TrainOutcome:
    """The eval-gate (runs **org-side**, after the adapter is pulled back from wherever it was
    trained): register the adapter as a candidate, score it against ``current_model`` on held-out
    gold, and keep it as ``model_name`` (org model v{version}) **only if it wins** - otherwise
    discard the candidate. A regression never replaces the live model.

    ``allow_unvalidated`` covers the case of too little gold to hold out an eval slice
    (``eval_examples`` empty): promote the candidate unscored (a first model, nothing to regress
    against) when true, otherwise reject it - never replace a live model on an un-evaluated
    candidate."""
    if not eval_examples:
        if not allow_unvalidated:
            return TrainOutcome(
                adapter_path, False, None, f"rejected {model_name}: too little gold to hold out"
            )
        register_with_ollama(model_name, base_model, adapter_path, ollama_url=ollama_url)
        return TrainOutcome(
            adapter_path, True, version, f"promoted {model_name}: unvalidated (too little gold)"
        )
    register_with_ollama(model_name, base_model, adapter_path, ollama_url=ollama_url)
    result = evaluate_models(
        inference_backend, current_model, model_name, eval_examples, sample=sample
    )
    if result.winner == model_name:
        return TrainOutcome(
            adapter_path, True, version, f"promoted {model_name}: {result.summary()}"
        )

    remove_from_ollama(model_name)  # rejected: discard the candidate, keep the current model
    return TrainOutcome(adapter_path, False, None, f"rejected {model_name}: {result.summary()}")


def promote_local_mlx(
    adapter_path: str,
    *,
    base_model: str,
    version: int,
    eval_examples: list[tuple[str, str]],
    current_adapter: str = "",
    sample: int = 20,
    allow_unvalidated: bool = False,
) -> TrainOutcome:
    """The eval-gate for the **local/solo MLX** path (Apple Silicon): score the new adapter against
    what is live today entirely within MLX - no Ollama registration. ``current_adapter`` is the
    currently-promoted adapter ("" means the bare base model, i.e. nothing promoted yet). The new
    adapter is kept (TrainOutcome.won_eval) only if it beats the current one on held-out gold;
    otherwise the candidate is rejected and the caller discards it. Serving the winner (spinning up
    ``mlx_lm server``) is the caller's job - this function only decides.

    ``allow_unvalidated`` promotes a first adapter unscored when there is too little gold to hold
    out an eval slice (``eval_examples`` empty); with a live adapter it rejects instead."""
    if not eval_examples:
        if allow_unvalidated:
            return TrainOutcome(
                adapter_path,
                True,
                version,
                f"promoted local v{version}: unvalidated (too little gold)",
            )
        return TrainOutcome(adapter_path, False, None, "rejected local candidate: too little gold")
    from ..inference.mlx_local import MlxBackend

    backend = MlxBackend(_mlx_base_model(base_model))
    result = evaluate_models(
        backend, current_adapter or "", adapter_path, eval_examples, sample=sample
    )
    if result.winner == adapter_path:
        return TrainOutcome(
            adapter_path, True, version, f"promoted local v{version}: {result.summary()}"
        )
    return TrainOutcome(adapter_path, False, None, f"rejected local candidate: {result.summary()}")
