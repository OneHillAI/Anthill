"""Neocloud endpoint training backend: a GPU rented from the org's OWN RunPod / Modal account.

This is the **pragmatic dev / SMB tier** and the unblock when a cloud account's GPU quota is
zero: instead of its own cloud tenancy, the org uses its **own** RunPod / Modal account (it
supplies the API key - there is **no Onehill-mediated path**). The provider rents *only the
GPU* on third-party shared hardware, so the data-privacy invariants in ``base.py`` are exactly
what make this acceptable:

  - only **PII-scrubbed gold** is ever transmitted (never raw chat/wiki, never bronze);
  - **encrypted in transit**; the job is **ephemeral** (pod/function torn down right after);
  - the provider must **never persist** the data and orgs are **never co-mingled**.

Because it runs on third-party shared GPUs, the Settings panel states on selection: "runs on
third-party shared GPUs; technically your data leaves your account" - unlike the org-cloud and
on-prem backends, which keep everything inside accounts the org controls.

This backend only does the GPU part: run the fine-tune on a rented GPU and pull the adapter
back. The eval-gate + Ollama promotion happen org-side, in the executor (`promote_if_better`).
"""

from __future__ import annotations

import os

from .base import BackendError, TrainingResult

# RunPod is the standard neocloud training provider - it reuses the org's CLOUD RunPod account (the same
# one that serves the model), so there is nothing extra to set up. Modal remains as a secondary option.
PROVIDERS = ("runpod", "modal")
_DEFAULT_PROVIDER = "runpod"


def _runpod_key(cfg) -> str:
    """The RunPod API key training reuses: the org's CLOUD provisioning key (``org_provision_key_enc``),
    so training is aligned with serving - one RunPod account, configured once. "" if none is set."""
    enc = (getattr(cfg, "org_provision_key_enc", "") or "").strip()
    if not enc:
        return ""
    try:
        from ...web.crypto import decrypt

        return decrypt(enc)
    except Exception:
        return ""


def load_modal_env(cfg) -> tuple[bool, str]:
    """Load the org's saved Modal credentials (Settings) into the env the Modal SDK reads.

    Modal authenticates with a token **id** + token **secret**; we store the id in clear
    (`training_token_id`, not secret) and the secret encrypted (`training_api_key_enc`). Sets
    `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` when both are present. Returns ``(ready, detail)`` and
    never raises - so a missing/garbled credential is reported, not thrown."""
    token_id = (getattr(cfg, "training_token_id", "") or "").strip()
    enc = (getattr(cfg, "training_api_key_enc", "") or "").strip()
    secret = ""
    if enc:
        try:
            from ...web.crypto import decrypt

            secret = decrypt(enc)
        except Exception:
            secret = ""
    if token_id and secret:
        os.environ["MODAL_TOKEN_ID"] = token_id
        os.environ["MODAL_TOKEN_SECRET"] = secret
        return True, "Modal credentials loaded into the environment."
    missing = [n for n, v in (("token ID", token_id), ("token secret", secret)) if not v]
    return False, f"Missing Modal {' and '.join(missing)}."


# Rough Modal GPU $/hr for the cost guardrail (display only; never billed against).
_GPU_USD_PER_HR = {"A10G": 1.10, "A100-40GB": 2.10, "L4": 0.80}
_DEFAULT_GPU = "A10G"
_HARD_MAX_RUNTIME_MIN = 1440  # 24h ceiling regardless of config


def _estimate_cost(gpu: str, minutes: int) -> float:
    return round(_GPU_USD_PER_HR.get(gpu, 2.0) * (max(0, minutes) / 60.0), 2)


def _budget_cap(cfg) -> float:
    try:
        return float(getattr(cfg, "aws_max_cost_usd", "") or 25.0)
    except (TypeError, ValueError):
        return 25.0


class EndpointBackend:
    name = "endpoint"

    def validate(self, cfg) -> tuple[bool, str]:
        provider = (
            getattr(cfg, "training_provider", "") or ""
        ).strip().lower() or _DEFAULT_PROVIDER
        if provider not in PROVIDERS:
            return (
                False,
                f"Unknown endpoint provider '{provider}'. Use one of: {', '.join(PROVIDERS)}.",
            )
        if provider == "runpod":
            if not _runpod_key(cfg):
                return (
                    False,
                    "RunPod training reuses your cloud RunPod account. Set up Settings -> "
                    "Organization with RunPod and your API key first; training then uses the same "
                    "account (no separate token).",
                )
            return (
                True,
                "RunPod training is set to reuse your cloud RunPod account. Run Train now on approved "
                "gold to confirm the GPU fine-tune end to end (that is the live check).",
            )

        ready, detail = load_modal_env(cfg)
        if not ready:
            return (
                False,
                f"{detail} Run 'modal token new' and paste the token ID (ak-...) and secret (as-...).",
            )
        # Credentials are present and loaded. We do NOT claim a live-verified connection here: the
        # honest end-to-end check is a real run (Train now), which is what authenticates with Modal.
        try:
            import modal  # noqa: F401

            return (
                True,
                "Modal credentials are saved and loaded into the environment. Run Train now on gold "
                "examples to confirm the full path end to end (that is what authenticates and trains).",
            )
        except Exception:
            return (
                True,
                "Modal credentials saved and loaded. The Modal SDK is not installed on this backend "
                "yet (pip install modal); they will be used once it is. Then run Train now to confirm.",
            )

    def run(self, cfg, *, dataset_path: str, base_model: str, run=None) -> TrainingResult:
        """Run the fine-tune on the org's own neocloud GPU and return the fetched adapter. The
        GPU is ephemeral (torn down when the job returns). Raises ``BackendError`` on refusal."""
        provider = (getattr(cfg, "training_provider", "") or _DEFAULT_PROVIDER).strip().lower()
        if provider == "runpod":
            return self._run_runpod(cfg, dataset_path=dataset_path, base_model=base_model)
        if provider != "modal":
            raise BackendError(
                f"Unknown endpoint provider '{provider}'. Use one of: {', '.join(PROVIDERS)}."
            )

        gpu = _DEFAULT_GPU
        runtime = min(int(getattr(cfg, "aws_max_runtime_min", 0) or 120), _HARD_MAX_RUNTIME_MIN)
        est, cap = _estimate_cost(gpu, runtime), _budget_cap(cfg)
        if est > cap:
            raise BackendError(
                f"Estimated worst-case cost ${est} exceeds the ${cap} per-run cap - "
                f"lower the runtime or raise the cap."
            )
        try:
            adapter = _modal_train(cfg, dataset_path, base_model, gpu=gpu, runtime_min=runtime)
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"Modal training run failed: {e}") from e
        return TrainingResult(
            adapter_path=adapter,
            won_eval=False,  # the executor's eval-gate decides promotion
            cost_usd_est=est,
            detail=f"trained on Modal ({gpu}); ephemeral GPU torn down",
        )

    def _run_runpod(self, cfg, *, dataset_path: str, base_model: str) -> TrainingResult:
        """Fine-tune on an ephemeral RunPod GPU pod, reusing the org's cloud RunPod account.

        Delegates the launch -> SSH-train -> fetch -> guaranteed-teardown orchestration to
        ``RunpodTrainer`` (unit-tested with an injected client). Real pacing via ``time.sleep``."""
        import time

        from .runpod_train import _DEFAULT_GPU_TYPE, RunpodTrainer, estimate_cost

        key = _runpod_key(cfg)
        if not key:
            raise BackendError(
                "No RunPod API key - set up Settings -> Organization as RunPod first; training "
                "reuses that account."
            )
        runtime = min(int(getattr(cfg, "aws_max_runtime_min", 0) or 120), _HARD_MAX_RUNTIME_MIN)
        cap = _budget_cap(cfg)
        try:
            adapter = RunpodTrainer().train(
                dataset_path=dataset_path,
                base_model=base_model,
                api_key=key,
                max_runtime_min=runtime,
                cost_cap_usd=cap,
                sleep=time.sleep,
            )
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"RunPod training run failed: {e}") from e
        return TrainingResult(
            adapter_path=adapter,
            won_eval=False,  # the executor's eval-gate decides promotion
            cost_usd_est=estimate_cost(_DEFAULT_GPU_TYPE, runtime),
            detail="trained on RunPod (ephemeral GPU pod torn down)",
        )


def _modal_train(cfg, dataset_path: str, base_model: str, *, gpu: str, runtime_min: int) -> str:
    """Run the LoRA fine-tune on the org's own Modal account and return the LOCAL adapter path.

    The Modal function is **ephemeral** - Modal tears the GPU down when it returns. Requires
    ``pip install modal`` and the org's Modal token in the environment (the org supplies its own
    credentials; no Onehill-mediated path). Only the already-PII-scrubbed dataset is uploaded.
    """
    load_modal_env(cfg)  # use the org's dashboard-saved token (id + secret), not just ~/.modal.toml
    try:
        import modal
    except Exception as e:
        raise BackendError("Modal SDK not installed - run: pip install modal") from e

    import io
    import tarfile
    import tempfile
    from pathlib import Path

    image = (
        modal.Image.debian_slim()
        .pip_install("torch", "transformers", "peft", "trl", "datasets", "accelerate")
        .add_local_python_source("anthill")
    )
    app = modal.App("anthill-train")

    @app.function(gpu=gpu, timeout=runtime_min * 60, image=image)
    def _remote(dataset_jsonl: str, base: str) -> bytes:
        import io as _io
        import tarfile as _tarfile
        import tempfile as _tempfile

        from anthill.training.trainer import train_adapter

        work = _tempfile.mkdtemp()
        ds_path = f"{work}/train.jsonl"
        with open(ds_path, "w") as fh:
            fh.write(dataset_jsonl)
        out = f"{work}/adapter"
        train_adapter(ds_path, base, out_dir=out, toolchain="peft")
        buf = _io.BytesIO()
        with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(out, arcname="adapter")
        return buf.getvalue()

    with app.run():
        blob = _remote.remote(Path(dataset_path).read_text(), base_model)

    out_dir = tempfile.mkdtemp(prefix="anthill-adapter-")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        # filter="data" (PEP 706) rejects absolute paths + `..` traversal so a compromised or MITM'd
        # Modal run can't path-traverse out of out_dir (this tarball is NOT checksum-gated) (#488).
        tar.extractall(out_dir, filter="data")
    return str(Path(out_dir) / "adapter")
