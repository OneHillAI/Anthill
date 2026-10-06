# Spec: Regenerate requirements.lock so the packaged app has what it imports

Status: implemented. Lane: `pillar:platform`. Source: QA review of the PR checks, 2026-10-06.

## Problem

`requirements.lock` is the only input to the packaged app's dependency set. `scripts/build-sidecar.sh`
installs it with `--require-hashes`, then the `anthill` package with `--no-deps`, then PyInstaller. Nothing
else is added. The lock has drifted from `pyproject.toml` in both directions:

- It lists 36 packages the app no longer declares: `torch`, `sentence-transformers`, `transformers`,
  `scikit-learn`, `scipy` and the CUDA and NVIDIA wheels they pull in. They are not in the frozen sidecar
  (`Anthill-sidecar.spec` excludes `torch`, `sentence_transformers` and `transformers`), but every sidecar
  build and the Windows CI job still download and hash-check them.
- It lacks 17 packages the app needs, including `sigstore` and its dependency tree (with `pyasn1`), `mammoth`
  and `pandas`. `anthill/hosting/catalog_trust.py` imports `sigstore` and fails closed when it is missing
  (`signature verification is unavailable`). A sidecar built from the stale lock therefore refuses every
  `POST /models/refresh-catalog`, so a packaged app can never refresh the signed model catalog.

## Requirements

1. Regenerate the lock with the documented command plus `--python-version 3.11`:
   `uv pip compile --universal --python-version 3.11 --generate-hashes --extra docs --extra mcp pyproject.toml -o requirements.lock`.
2. The pin is deliberate. Without it uv uses the machine's Python. On 3.13 or newer the result silently
   drops the macOS pin `onnxruntime<1.20` (its 1.20 wheels need macOS 13, the app supports 11) and the
   numpy splits for Python 3.11 and 3.12. With the pin, the regeneration itself changes no package version; the seven upgrades that follow it are in `docs/specs/clear-fixable-dependency-alerts.md`.
3. `CONTRIBUTING.md` and the comment in `scripts/build-sidecar.sh` state the same command and why the
   pin is there.

## Out of scope

- The packaged app's behaviour is otherwise unchanged: no source file under `anthill/` is touched.
- `pypdfium2 5.12.0` is flagged as yanked upstream. That was already in the lock and is left alone.
- A build-time guard that fails when the frozen sidecar lacks a declared dependency. Proposed as a
  follow-up, not part of this change.

## Acceptance criteria

- `requirements.lock` has no `torch`, `nvidia-*`, `cuda-*`, `sentence-transformers` or `transformers`, and
  has `sigstore`, `mammoth` and `pandas`.
- `onnxruntime` is `1.19.2` and `numpy` keeps its two version splits.
- A fresh Python 3.11 environment installs from the lock with `--require-hashes`, and `uv pip check` passes.
- `catalog_trust.verify_catalog` accepts the live signed catalog and rejects a tampered copy.
- The frozen sidecar built from the new lock passes `--selfcheck-office`, `--selfcheck-cache` and
  `scripts/smoke_frozen_chat.py`, and the frozen binary refreshes the live signed catalog.
