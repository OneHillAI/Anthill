# Tasks: Phase 6b2 compute/model/council step

## Reference code (verified verbatim against this worktree's `origin/main`, post-#665/#666)

### 1. Real signatures (do not guess - an earlier draft for this same capability got these wrong)

```python
# anthill/hosting/sizing.py
def recommend_by_family(
    mem_gb: float, *, kind: str = "apple", context_k: float = 8.0, concurrency: int = 1,
    catalog: tuple[Model, ...] | None = None, families: tuple[str, ...] | None = None,
) -> list[FamilyPick]: ...

def onprem_council_fits(
    member_params_b: list[float], *, context_k: float = 8.0, concurrency: int = 1,
) -> bool: ...  # NO mem_gb/kind params - calls local_hardware() internally

def onprem_council_fits_on_disk(
    member_params_b: list[float], *, path: str | None = None,
) -> bool: ...

def local_hardware() -> tuple[float, str]: ...  # (mem_gb, kind)

@dataclass
class FamilyPick:
    family: str
    origin: str
    recommended: Model | None
    download_gb: float
```

`suggest_regional_council(region: str, *, catalog=None) -> RegionalCouncilSuggestion` exists but is
UNRELATED to this task (region is "US"|"EU"|"China", for choosing remote VPC provider geography) - an
earlier draft misused it here; do not reuse it for local capacity decisions.

### 2. `_model_picker_view` (`anthill/web/app.py:1306-1370`, to extend) and its two callers

```python
def _model_picker_view(cfg):
    from ..hosting.sizing import (
        families_by_intelligence, family_download_gb, fit_tier, load_catalog, local_hardware,
    )
    from ..inference.ollama import OllamaBackend
    LOCAL_CATALOG = load_catalog()
    LOCAL_FAMILIES = families_by_intelligence(LOCAL_CATALOG)
    mem_gb, kind = local_hardware()
    installed = set(OllamaBackend(cfg.ollama_url, cfg.ollama_model).installed_models()) if cfg else set()
    # ... builds families/default_tag as today; ADD a suggest_local_setup() call alongside this,
    # do not remove the existing per-family breakdown (still useful for an "advanced: pick manually"
    # affordance even when a council is suggested).

@app.get("/setup/model", response_class=HTMLResponse)
def model_picker_get(request: Request, user: dict = Depends(_require_user)):
    db = _db()
    org = _require_org(db, user)
    cfg = _cfg(db, org)
    ctx = _model_picker_view(cfg)
    return templates.TemplateResponse(
        request, "model_picker.html", {"request": request, "setup_step": 3, **ctx}
    )
```

`cfg.deployment_topology` (set by `/setup/account-type`, Phase 6b1, merged) already distinguishes Solo
from organization - read it here to decide whether Local is offered. `normalize_topology()` (used
elsewhere, e.g. the dashboard route) is the existing helper that turns this field into a clean
solo/org boolean - reuse it, do not re-derive.

### 3. `org_council_members` JSON shape (`anthill/web/app.py:880-905`, `_empty_member()`, verified in full)

```python
def _empty_member() -> dict:
    return {
        "endpoint": "", "provider": "", "model": "", "params_b": "", "region": "", "gpu_tier": "",
        "quantized": False, "model_key_enc": "", "provision_key_enc": "", "hf_token_enc": "",
        "backend_handle": "", "backend_status": "unconfigured", "backend_detail": "",
        "lifecycle": "vpc",  # "onprem" is a PROVIDER value, not a lifecycle value - leave this at "vpc"
        "provider_config": {},
    }
```

A local council member accepted from this step should be written as: `{**_empty_member(), "provider":
"onprem", "endpoint": cfg.ollama_url, "model": <ollama_tag>, "params_b": str(<params_b>), "backend_status":
"live"}` (or whatever status value means "ready now" - check `provision_run.py`'s existing on-prem status
constants rather than inventing a new one). `cfg.org_council_members` is a JSON TEXT column
(`json.dumps([...])`), matching `_members_from_cfg`'s own `json.loads` read side.

### 4. `resolve_council_backends` (`anthill/council/engine.py:108-160`, verified in full this session) -
the function that must actually resolve whatever gets written above. A member resolves when
`lifecycle == "vpc"` (the default) AND `endpoint`/`model` are non-blank - `provider == "onprem"` maps to
an Ollama backend via `_provider_backend_kind`. Write a test that round-trips: save the 3 suggested
members via this step, then call `resolve_council_backends(cfg, decrypt)` and assert `len(resolved) == 3`
- do not just assert the JSON was saved, prove it actually resolves.

### 5. Existing `model_picker.html` (read in full, Tier 0 work this session) - today's single-model radio
picker. Extend rather than replace: add the 3 compute-option tiles above the existing per-family radio
list (which becomes the "Local" tile's content, shown when Local is selected/chosen), plus a
council-suggestion summary block when `suggestion_mode == "council"`.

### 6. Existing test pattern (`tests/test_setup_account_type.py`'s `_client`, this session's Phase 6b1
work) - reuse for any new route test needing a fresh sqlite-backed `TestClient` with a specific
`deployment_topology`/`account_type_chosen` state.

## Build steps

1. Add `sizing.LocalSetupSuggestion` + `sizing.suggest_local_setup(mem_gb, kind, *, catalog=None)` using
   `recommend_by_family` (never `suggest_regional_council`), with correctly-shaped calls to
   `onprem_council_fits`/`onprem_council_fits_on_disk` (task 1).
2. Extend `_model_picker_view` to include the suggestion + compute-option list, reading
   `cfg.deployment_topology` for the Local-only-for-Solo rule.
3. Extend `model_picker.html` for the 3-tile layout + council/single suggestion summary.
4. Extend `POST /setup/model` so accepting a council suggestion downloads all 3 tags AND writes them into
   `org_council_members` per task 3; a single suggestion keeps today's exact single-`ollama_model` behavior
   (no council registration).
5. Link the cloud/self-hosted-Mac-mini compute options to `/settings/organization` (no new provisioning UI
   built here).
6. New/updated tests: both suggestion branches (mock the probes, do not depend on real hardware/catalog);
   the Local-hidden-for-org rule; the round-trip resolve test (task 4); the redirect-to-Settings behavior
   for the other two compute options.
7. `ruff check`, `ruff format --check`, `mypy`, full test suite. No em/en-dashes, no TODO/FIXME/XXX markers.

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section.
