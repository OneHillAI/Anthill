# Tasks: Phase 6a disk-space capacity checking

## Reference code (verified verbatim against this worktree's `origin/main`)

### 1. Module docstring and existing constants (`anthill/hosting/sizing.py:1-45`)

```python
_GB_PER_B_Q4 = 0.55  # GB of memory per billion params at 4-bit (weights, plus a little overhead)
...
_COMFORT_FREE_GB = (
    7.0  # free system memory a model should LEAVE (after its resident footprint) to run
)
```

`_GB_PER_B_Q4` is ALSO what `family_download_gb()` uses for on-disk size - the same 4-bit-quantized
convention already stands in for "how big is this file on disk," so no new per-model data is needed.

### 2. `family_download_gb` and `FamilyPick` (`anthill/hosting/sizing.py:568-579`)

```python
class FamilyPick:
    """The best on-device choice within one model family for this machine."""

    family: str
    origin: str
    recommended: Model | None  # largest model in the family that fits, or None if none fit
    download_gb: float  # approx 4-bit download/disk size of the recommended model (0 if none)


def family_download_gb(params_b: float) -> float:
    """Approximate 4-bit on-disk / download size (GB) for a model of ``params_b`` billions."""
    return round(params_b * _GB_PER_B_Q4, 1)
```

This already exists and is display-only today (`FamilyPick.download_gb`). Reuse it; do not add a second
disk-size formula.

### 3. `onprem_council_fits` - the exact pattern to mirror for disk (`anthill/hosting/sizing.py:368-398`)

```python
def onprem_council_fits(
    member_params_b: list[float],
    *,
    context_k: float = 8.0,
    concurrency: int = 1,
) -> bool:
    """Do all on-prem council members fit together in ONE shared local-hardware budget?
    ...
    """
    if not member_params_b:
        return True
    mem_gb, kind = local_hardware()
    if mem_gb <= 0:
        return True  # hardware could not be probed; do not block the save on a bad probe
    budget = usable_gb(mem_gb, kind=kind, context_k=0.0, concurrency=1)
    if kind != "apple":
        budget -= _LOCAL_RUNNER_GB
    total = sum(
        max(0.0, pb) * _GB_PER_B_Q4 + _kv_budget_gb(context_k, concurrency)
        for pb in member_params_b
    )
    return total <= budget
```

Build the new disk gate with the SAME shape: a list of `params_b` in, a bool out, fail-open (return
`True`) when the probe itself is unavailable - never block a save/suggestion on a probe failure, exactly
as this function already does for a bad memory read. Suggested name: `onprem_council_fits_on_disk`, OR
fold disk into `onprem_council_fits` itself as an additional check (state and justify whichever you pick;
if folding in, `onprem_council_fits`'s docstring and every existing caller's understanding of what "fits"
means must be updated consistently - do not silently broaden its meaning without saying so in the PR).

### 4. `free_mem_gb` - the None-on-failure hardware-probe convention to follow (`anthill/hosting/sizing.py:820-834`)

```python
def free_mem_gb() -> float | None:
    """Best-effort FREE/available system memory in GB - distinct from ``local_hardware``'s TOTAL. ...
    None if unknown."""
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            return _parse_vm_stat_free_gb(out.stdout) if out.returncode == 0 else None
        with open("/proc/meminfo") as fh:  # Linux
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024**2)  # kB -> GB
    except Exception:
        return None
    return None
```

The new `free_disk_gb()` should follow this SAME contract (`float | None`, `None` on any failure, never
raises) but the implementation is much simpler: use stdlib `shutil.disk_usage(path).free / (1024**3)`
directly - no subprocess, no per-OS branching needed, since `shutil.disk_usage` is already
cross-platform. Default `path` to wherever local models are actually stored (check
`anthill/hosting/` or wherever Ollama's model directory / the workspace root is referenced elsewhere in
this codebase - e.g. `Workspace` in `anthill/config.py` or similar - so the probe checks the disk that
actually matters, not just `/`).

### 5. Existing test pattern to follow verbatim (`tests/test_onprem_council_fit.py`, read in full)

```python
"""Phase 2: the SUMMED on-prem council footprint gate (product-council-architecture.md R5). ..."""

from anthill.hosting import sizing


def test_two_onprem_members_fit_alone_but_summed_is_refused(monkeypatch):
    monkeypatch.setattr(sizing, "local_hardware", lambda: (40.0, "gpu"))
    assert sizing.onprem_council_fits([32.0])
    assert not sizing.onprem_council_fits([32.0, 32.0])


def test_unprobed_hardware_fails_open_not_closed(monkeypatch):
    monkeypatch.setattr(sizing, "local_hardware", lambda: (0.0, "apple"))
    assert sizing.onprem_council_fits([32.0, 32.0])
```

Follow this EXACT style for the new disk-gate tests: `monkeypatch.setattr(sizing, "free_disk_gb", lambda
path=None: <value>)` (or however the real signature ends up), one test per meaningful boundary (fits
alone but not summed; empty list never blocks; unprobed/`None` fails open; comfort-headroom constant is
exercised at its exact boundary, mirroring
`test_apple_reserves_runner_overhead_exactly_once`'s worked-arithmetic style - show the numbers, don't
just assert true/false blindly).

## Build steps

1. Add `free_disk_gb(path: str = ...) -> float | None` using `shutil.disk_usage`, following
   `free_mem_gb()`'s None-on-failure contract. Determine the right default path by checking how/where
   local model files are actually stored in this codebase before hardcoding one.
2. Add a comfort-headroom constant for disk (e.g. `_DISK_COMFORT_FREE_GB`), justified in a comment the
   same way `_COMFORT_FREE_GB`/`_LOCAL_RUNNER_GB` already justify theirs (a real, stated reason - e.g. the
   OS itself needs free disk headroom to operate, downloads can overshoot slightly, etc. - not an
   arbitrary round number with no reasoning).
3. Add the disk fit-gate (`onprem_council_fits_on_disk` or folded into `onprem_council_fits` - your
   call, justified in the PR), mirroring `onprem_council_fits`'s exact signature shape and fail-open
   behavior.
4. Decide and document whether `recommend_by_family()`'s `FamilyPick` gains a disk-awareness field (e.g.
   excluding a family whose recommended model would not fit on disk) or whether disk-checking is left as
   a standalone function the onboarding flow (a later change) calls itself. Either is fine; do not leave
   it ambiguous - state the choice and update `recommend_by_family`'s tests accordingly if its behavior
   changes.
5. New tests in a new file (e.g. `tests/test_disk_capacity.py`), following `test_onprem_council_fit.py`'s
   style exactly: monkeypatch the probe, assert exact boundaries with worked arithmetic in comments,
   cover fail-open-on-unprobed and empty-list-never-blocks explicitly.
6. Run `ruff check`, `ruff format --check`, `mypy`, and the full test suite. Compare failures against the
   known pre-existing baseline (should be 0 new failures; recent runs have shown 0 pre-existing failures
   in this environment's venv, or up to 17 markitdown[pdf]-dependency-gap skips/failures in others -
   treat any OTHER new failure as real).
7. No em/en-dashes, no TODO/FIXME/XXX markers.

## Explicitly out of scope

Same as `proposal.md`'s "Explicitly out of scope" section - the onboarding flow itself, cloud/VPC-side
storage checking, any UI, and any legacy Settings/dashboard copy cleanup.
