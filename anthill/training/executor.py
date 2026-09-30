"""Execute scheduled training runs - the org-side half of §7.5.

Picks up the `TrainingRun` rows the scheduler queued and, for each: exports the org's gold with
every field **PII-scrubbed** (the data-privacy invariant - only scrubbed gold ever leaves the
node), hands the dataset to the selected backend to fine-tune on its GPU, then runs the
**eval-gate locally** - promoting the new adapter to the org's next model version only if it
beats the current model on held-out gold. Backends provide the GPU + guaranteed teardown; this
module owns gold preparation, the eval-gate, and the audit trail.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from ..hybrid.scrub import scrub
from ..inference.ollama import OllamaBackend
from ..web.db import OrgSettings, TrainingExample, TrainingRun
from . import trainer
from .backends import BackendError, get_backend


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_local_mlx(cfg) -> bool:
    """The local/solo Apple-Silicon path: train + serve on this machine via MLX (no Ollama
    adapter registration, which cannot load an MLX adapter)."""
    from .model_select import is_local_training

    return is_local_training(cfg) and trainer.detect_toolchain() == "mlx"


def _adapters_home() -> Path:
    """Stable home for promoted adapters (survives the temp training workdir). Overridable for tests."""
    import os

    override = os.environ.get("ANTHILL_ADAPTERS_DIR", "").strip()
    if override:
        base = Path(override)
    else:
        from ..desktop import data_dir

        base = data_dir() / "adapters"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _persist_adapter(src: str, org_id: int, version: int) -> str:
    """Copy a freshly trained adapter out of the temp workdir into a stable per-version dir."""
    import shutil

    dst = _adapters_home() / f"org{org_id}-v{version}"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return str(dst)


def _promote_and_serve_local_mlx(
    cfg, adapter_src: str, run, version: int, examples, allow_unvalidated: bool = False
) -> trainer.TrainOutcome:
    """Eval-gate the MLX adapter against the currently-served local fine-tune; on a win, persist it
    and (re)start the local mlx-lm server so the Solo plane serves the new model. On a loss, discard
    the candidate and keep serving whatever is live."""
    import shutil

    persisted = _persist_adapter(adapter_src, run.org_id, version)
    outcome = trainer.promote_local_mlx(
        persisted,
        base_model=run.base_model,
        version=version,
        eval_examples=examples,
        current_adapter=getattr(cfg, "local_finetune_path", "") or "",
        allow_unvalidated=allow_unvalidated,
    )
    if outcome.won_eval:
        cfg.local_finetune_path = persisted
        try:
            from .mlx_serve import manager

            st = manager.start(run.base_model, persisted)
            cfg.local_serve_url = st.get("url", "") or ""
            cfg.local_serve_model = st.get("model", "") or ""
        except Exception:
            pass  # serving is best-effort; the promotion is still recorded
    else:
        shutil.rmtree(persisted, ignore_errors=True)  # discard the rejected candidate
    return outcome


def _gold(db, org_id: int, cfg=None) -> list[TrainingExample]:
    """The gold examples this account fine-tunes on. For a SOLO account (whether it trains
    on-device or on a connected cloud GPU - see model_select.is_local_training vs
    is_solo_account) that is the user's PERSONAL gold - a solo tenant is one user, trains its own
    model, and there is no shared org model to protect. For an ORG account it stays org-scope gold
    ONLY - the shared model never trains on personal/unreviewed signals."""
    from .model_select import is_solo_account

    scope = "personal" if (cfg is not None and is_solo_account(cfg)) else "org"
    return (
        db.query(TrainingExample)
        .filter(
            TrainingExample.org_id == org_id,
            TrainingExample.scope == scope,
            TrainingExample.quality == "gold",
        )
        .all()
    )


# Below this many distinct instructions, a held-out eval slice is too small to trust, so we
# don't carve one - see `_split_gold`.
MIN_GOLD_FOR_SPLIT = 6
EVAL_FRACTION = 0.2
# A validated promotion (replacing a live model) must be decided on at least this many distinct
# held-out instructions. Fewer than this is noise, not a regression signal, so the incumbent is
# kept rather than swapped on a coin-flip (issue #365). First models (no incumbent) are exempt.
MIN_EVAL_EXAMPLES = 5


def _norm_instr(instruction: str) -> str:
    """Normalized instruction key used to group paraphrase-duplicates so they never straddle the
    train/eval split (and to count distinct held-out instructions)."""
    return " ".join((instruction or "").lower().split())


def _split_gold(
    rows: list[TrainingExample],
) -> tuple[list[TrainingExample], list[TrainingExample]]:
    """Split gold into (train, eval) so the promotion gate scores rows the adapter never trained on.

    Rows are grouped by normalized instruction and split by group, so a question and its
    paraphrase-duplicates never straddle the boundary (which would leak). ~20% of distinct
    instructions are held out, chosen deterministically by hash. Below `MIN_GOLD_FOR_SPLIT`
    distinct instructions there is too little to hold out a trustworthy slice, so the whole set
    trains and eval is empty (the caller then gates on incumbent presence, not a leaked score).
    """
    groups: dict[str, list[TrainingExample]] = {}
    for r in rows:
        groups.setdefault(_norm_instr(r.instruction), []).append(r)
    if len(groups) < MIN_GOLD_FOR_SPLIT:
        return rows, []
    keys = sorted(groups, key=lambda k: hashlib.md5(k.encode(), usedforsecurity=False).hexdigest())
    n_eval = max(1, round(len(keys) * EVAL_FRACTION))
    eval_keys = set(keys[:n_eval])
    train_rows, eval_rows = [], []
    for k, rs in groups.items():
        (eval_rows if k in eval_keys else train_rows).extend(rs)
    return train_rows, eval_rows


def _write_scrubbed_dataset(rows: list[TrainingExample], out_path: str) -> int:
    """Write gold to jsonl with every field PII-scrubbed BEFORE it can leave the node. Returns
    the number of examples written."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out_path, "w") as fh:
        for r in rows:
            obj = {
                "instruction": scrub(r.instruction).text,
                "input": scrub(r.context or "").text,
                "output": scrub(r.output).text,
            }
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n += 1
    return n


def _current_model(cfg, base_model: str, org_id: int) -> str:
    """The org's live model to beat: the last promoted version, or the base if none yet."""
    ver = int(getattr(cfg, "training_model_ver", 0) or 0)
    return f"org{org_id}-model-v{ver}" if ver else base_model


def _activate_org_serving(cfg, model_name: str) -> str:
    """On a win on the Ollama (non-MLX) path, point the served model at the freshly-registered
    ``model_name`` so users actually get the new model - but only when it is servable from where the org
    serves, i.e. the same local Ollama the fine-tune was registered in (``ollama create`` targets
    ``cfg.ollama_url``). Returns a short audit note. A remote serving endpoint (RunPod / vLLM) does not
    hold the locally registered adapter, so we must NOT repoint at a tag it lacks - deploying the adapter
    there is a separate step, and we say so rather than silently breaking serving."""
    ollama = (getattr(cfg, "ollama_url", "") or "").strip().rstrip("/")
    endpoint = (getattr(cfg, "org_model_endpoint", "") or "").strip().rstrip("/")
    if not endpoint:
        cfg.ollama_model = (
            model_name  # no separate org endpoint: the account serves the local Ollama
        )
        return f"now serving {model_name}"
    if ollama and endpoint.startswith(ollama):
        cfg.org_model = model_name  # the org endpoint IS this local Ollama (same host)
        return f"now serving {model_name}"
    return (
        f"{model_name} registered in the local Ollama but NOT deployed to the remote serving endpoint "
        f"({endpoint}); serving unchanged - deploy the adapter there to activate it"
    )


def execute_run(db, run: TrainingRun) -> None:
    """Run one scheduled `TrainingRun` end to end, recording the outcome on the row (and the
    org's model version on promotion). Never raises - failures are recorded as status=failed so
    the scheduler keeps going."""
    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == run.org_id).first()
    if cfg is None:
        run.status = "failed"
        run.eval_note = "no OrgSettings for org"
        run.finished_at = _now()
        db.commit()
        return

    # Atomically claim the run (scheduled -> running) so a concurrent run_scheduled pass - e.g. clicking
    # "Train now" while the 24h scheduler tick fires - cannot provision a second GPU for the same row.
    # Only the worker whose conditional UPDATE actually flips the row proceeds; the other bails.
    claimed = (
        db.query(TrainingRun)
        .filter(TrainingRun.id == run.id, TrainingRun.status == "scheduled")
        .update({"status": "running"}, synchronize_session=False)
    )
    db.commit()
    if not claimed:
        return  # another worker already claimed this run
    run.status = "running"
    cfg.training_status = "running"
    db.commit()
    workdir = tempfile.mkdtemp(prefix=f"anthill-train-org{run.org_id}-")
    try:
        rows = _gold(db, run.org_id, cfg)  # personal gold for a Solo account, else org-scope gold
        train_rows, eval_rows = _split_gold(rows)  # eval on held-out gold, never on trained rows
        cur_ver = int(getattr(cfg, "training_model_ver", 0) or 0)
        eval_instrs = len({_norm_instr(r.instruction) for r in eval_rows})
        if rows and cur_ver > 0 and eval_instrs < MIN_EVAL_EXAMPLES:
            # A live model exists but the held-out slice is too small to be a trustworthy regression
            # signal (deciding on a row or two is a coin-flip). Keep the incumbent rather than risk
            # swapping in a regression, and record it without spending the training compute (possibly
            # a rented GPU).
            run.status = "rejected"
            run.eval_note = (
                f"rejected: too little gold to hold out a trustworthy eval slice "
                f"(need >= {MIN_EVAL_EXAMPLES} held-out instructions, got {eval_instrs}); "
                f"live model kept"
            )
            cfg.training_status = "idle"
            return
        dataset = f"{workdir}/gold.jsonl"
        if _write_scrubbed_dataset(train_rows, dataset) == 0:
            raise BackendError("no gold examples to train on")

        backend = get_backend(cfg)
        result = backend.run(cfg, dataset_path=dataset, base_model=run.base_model, run=run)
        if not result.adapter_path:
            raise BackendError(f"{backend.name} backend produced no adapter")

        version = cur_ver + 1
        examples = [(r.instruction, r.output) for r in eval_rows]  # local eval refs (not scrubbed)
        # Too little gold to hold out a slice: promote a first model unvalidated, but never let an
        # unvalidated candidate replace a live one - a regression must never ship silently.
        allow_unvalidated = cur_ver == 0
        serve_note = ""
        if _is_local_mlx(cfg):
            # Apple-Silicon local/solo: eval + serve on this machine via MLX (no Ollama).
            outcome = _promote_and_serve_local_mlx(
                cfg, result.adapter_path, run, version, examples, allow_unvalidated
            )
        else:
            model_name = f"org{run.org_id}-model-v{version}"
            inference = OllamaBackend(cfg.ollama_url, cfg.ollama_model)
            outcome = trainer.promote_if_better(
                result.adapter_path,
                base_model=run.base_model,
                model_name=model_name,
                version=version,
                eval_examples=examples,
                current_model=_current_model(cfg, run.base_model, run.org_id),
                inference_backend=inference,
                ollama_url=cfg.ollama_url,
                allow_unvalidated=allow_unvalidated,
            )
            if outcome.won_eval:
                # The MLX path re-points serving itself on a win; the Ollama path must too, or a promoted
                # model is registered + version-bumped yet never actually served (issue #254).
                serve_note = _activate_org_serving(cfg, model_name)
        if outcome.won_eval:
            run.status = "promoted"
            run.model_version = version
            cfg.training_model_ver = version
        else:
            run.status = "rejected"
        run.eval_note = f"{outcome.detail} | {serve_note}" if serve_note else outcome.detail
        cfg.training_status = "idle"
    except Exception as e:
        run.status = "failed"
        run.eval_note = str(e)[:500]
        cfg.training_status = "error"
    finally:
        run.finished_at = _now()
        db.commit()


def run_scheduled(engine) -> int:
    """Execute every `TrainingRun` currently in 'scheduled' status. Returns how many ran."""
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        runs = db.query(TrainingRun).filter(TrainingRun.status == "scheduled").all()
        for run in runs:
            execute_run(db, run)
        return len(runs)
    finally:
        db.close()
