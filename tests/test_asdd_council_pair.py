"""The developer council's endpoint and key are set once for the whole council, not once per member.

`ASDD_MODEL_URL__COUNCIL` / `ASDD_RUNTIME_TOKEN__COUNCIL` serve every council member. Per member the
lookup is `__COUNCIL_<i>` (an optional override), then `__COUNCIL`, then the shared pair, in both
`cli/connect-check.py` (the runtime check) and `cli/dev-council.py` (the runner).
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_ENV = (
    "ASDD_MODEL_URL",
    "ASDD_RUNTIME_TOKEN",
    "ASDD_MODEL_URL__COUNCIL",
    "ASDD_RUNTIME_TOKEN__COUNCIL",
    *[f"ASDD_{k}__COUNCIL_{i}" for k in ("MODEL_URL", "RUNTIME_TOKEN") for i in (1, 2, 3)],
)


def _load_cli(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").removesuffix(".py"), ROOT / "cli" / name
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _members(script: str, tmp_path: Path):
    """(url, token) per council member as `script` resolves them."""
    mod = _load_cli(script)
    if script == "connect-check.py":
        cfg = tmp_path / ".asdd.yml"
        cfg.write_text('dev_council:\n  models:\n    - "a"\n    - "b"\n    - "c"\n')
        return [(u, t) for _, _, u, t in mod.council_members(str(cfg))]
    return [(m["url"], m["token"]) for m in mod.resolve_members(["a", "b", "c"])]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("script", ["connect-check.py", "dev-council.py"])
def test_one_council_wide_pair_serves_every_member(tmp_path, monkeypatch, script):
    monkeypatch.setenv("ASDD_MODEL_URL", "https://shared.example/v1/chat/completions")
    monkeypatch.setenv("ASDD_RUNTIME_TOKEN", "shared-key")
    monkeypatch.setenv("ASDD_MODEL_URL__COUNCIL", "https://runware.example/v1")
    monkeypatch.setenv("ASDD_RUNTIME_TOKEN__COUNCIL", "council-key")
    assert _members(script, tmp_path) == [("https://runware.example/v1", "council-key")] * 3


@pytest.mark.parametrize("script", ["connect-check.py", "dev-council.py"])
def test_per_member_beats_council_wide_beats_shared(tmp_path, monkeypatch, script):
    monkeypatch.setenv("ASDD_MODEL_URL", "https://shared.example/v1")
    monkeypatch.setenv("ASDD_RUNTIME_TOKEN", "shared-key")
    monkeypatch.setenv("ASDD_MODEL_URL__COUNCIL_2", "https://member2.example/v1")
    monkeypatch.setenv("ASDD_RUNTIME_TOKEN__COUNCIL_2", "member2-key")
    # No council-wide pair: members 1 and 3 fall back to the shared pair, member 2 keeps its own.
    assert _members(script, tmp_path) == [
        ("https://shared.example/v1", "shared-key"),
        ("https://member2.example/v1", "member2-key"),
        ("https://shared.example/v1", "shared-key"),
    ]


@pytest.mark.parametrize("script", ["connect-check.py", "dev-council.py"])
def test_council_wide_pair_loses_to_a_per_member_pair(tmp_path, monkeypatch, script):
    monkeypatch.setenv("ASDD_MODEL_URL__COUNCIL", "https://runware.example/v1")
    monkeypatch.setenv("ASDD_RUNTIME_TOKEN__COUNCIL", "council-key")
    monkeypatch.setenv("ASDD_MODEL_URL__COUNCIL_3", "https://lead.example/v1")
    monkeypatch.setenv("ASDD_RUNTIME_TOKEN__COUNCIL_3", "lead-key")
    assert _members(script, tmp_path) == [
        ("https://runware.example/v1", "council-key"),
        ("https://runware.example/v1", "council-key"),
        ("https://lead.example/v1", "lead-key"),
    ]
