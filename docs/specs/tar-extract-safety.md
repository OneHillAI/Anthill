# Spec: Path-traversal-safe tarball extraction

Status: implemented. Lane: `pillar:platform`. Issue: #488 (launch-critical; Security-audit gate red on main).

## Problem

Two code paths call `tarfile.extractall()` without validating members, which bandit flags HIGH+HIGH
(`B202:tarfile_unsafe_members`) and which reddened the `Security audit` workflow on `main`:

- `anthill/inference/ollama.py` extracts the downloaded Ollama runtime tgz. SHA256-gated (low real risk,
  but the extraction itself is unguarded).
- `anthill/training/backends/endpoint.py` extracts an adapter tarball returned from a remote Modal run.
  **Not** checksum-gated, so a compromised or MITM'd run could ship a member like `../../evil` that writes
  outside the temp dir - a real path-traversal.

## Requirements

- Both extractions must reject absolute paths and `..` parent traversal, so no member can write outside
  its destination directory, regardless of where the tarball came from.
- Behavior is otherwise unchanged for well-formed archives (the Ollama binary + libs; the adapter files).
- The bandit HIGH+HIGH `B202` findings clear, so the Security-audit SAST job passes on `main`.

## How

Pass the PEP 706 `filter="data"` to both `extractall()` calls (available on the CI Python 3.11 backport
and 3.12+). The `data` filter refuses absolute paths and parent-dir traversal, raising `tarfile.FilterError`.

## Acceptance criteria

- `bandit -r anthill -lll -iii -q` exits 0 (no HIGH+HIGH), where it previously reported the two `B202`
  findings.
- Extracting a tarball with a `../escape` member via the `data` filter raises `tarfile.FilterError` and
  writes nothing outside the destination.
- Both `extractall` call sites carry `filter="data"` (guarded so a future edit can't silently drop it).
- Covered by `tests/test_tar_extract_safety.py`.
