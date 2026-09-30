"""What to fine-tune, and when local training is implicitly on.

The fine-tune target is always the model being *served* - never a separate user choice (a LoRA
adapter is base-model-specific, so training a different base than you serve produces an
incompatible adapter). Two axes matter here, and they are NOT the same thing:

- **Topology** (solo vs org) decides whose gold trains the model: a solo account is one user, so
  it trains on its own PERSONAL gold (``_gold`` in ``training/executor.py``); an org trains only
  on org-scope gold ≥2 people corroborated. See ``is_solo_account``.
- **Where compute runs** (on-device vs a connected cloud GPU) decides which training BACKEND
  fires - independently of topology. A solo account is not locked to on-device training just
  because it is solo: if its compute tier is a connected cloud provider (RunPod etc, the same
  "Your cloud" attachment ``_apply_solo_compute`` wires for serving), training reuses that exact
  connection via the same ``training_backend``/``training_provider`` registry an org uses -
  nothing here is org-only, an org account just happens to be the only one that could reach the
  cloud-training settings before Solo's own compute chooser started deriving them too. See
  ``is_local_training``.
"""

from __future__ import annotations


def resolve_base_model(cfg) -> str:
    """The model to fine-tune: the org's selected model, else the local served model."""
    org_model = (getattr(cfg, "org_model", "") or "").strip()
    if org_model:
        return org_model
    from ..config import Config

    return Config.from_env().model


def is_solo_account(cfg) -> bool:
    """True for a solo (one-user) account, regardless of where its compute runs. Drives which
    gold a training run reads (personal for solo, org-scope for org) and whether the Settings ->
    Model "Self-tuning" card applies to this account at all (an org's tuning lives under
    Cloud & model / Training instead) - neither of those cares whether solo is local or cloud."""
    topology = (getattr(cfg, "deployment_topology", "org") or "org").strip().lower()
    return topology == "solo"


def is_local_training(cfg) -> bool:
    """True only when training actually runs ON-DEVICE: a solo account whose compute tier is
    still local (``solo_compute != "cloud"``). A solo account that attached a cloud provider under
    "Your cloud" is NOT local training - it trains on that connected GPU instead, through the same
    backend registry an org's cloud training uses (see this module's docstring). Do not use this
    for anything topology-only (gold scope, "does Self-tuning apply to me") - that's
    ``is_solo_account``."""
    if not is_solo_account(cfg):
        return False
    return (getattr(cfg, "solo_compute", "local") or "local") != "cloud"


def training_on(cfg) -> bool:
    """Effective 'training enabled': admin/user-toggled for a cloud backend (org OR solo-cloud -
    it costs real money either way), always-on for genuinely free on-device local/solo training."""
    return is_local_training(cfg) or bool(getattr(cfg, "training_enabled", False))
