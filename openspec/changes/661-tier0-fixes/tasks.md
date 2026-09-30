# Tasks: PR #661 Tier 0 fixes

## Reference code (verified verbatim against this worktree's `origin/main`, do not re-derive from memory)

### 1. `anthill/inference/openai_compat.py:10-21` - the default to bump

```python
class OpenAICompatBackend:
    """Talks to any OpenAI-compatible /chat/completions server: vLLM, llama.cpp,
    LM Studio, or a privately hosted endpoint. base_url should include the /v1 suffix."""

    def __init__(
        self, base_url: str, model: str, api_key: str | None = None, timeout: float = 120.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
```

Change only the `120.0` literal. Signature shape, parameter names, and order stay identical.

### 2. `anthill/config.py:11-31` - the other default to bump

```python
@dataclass
class Config:
    """How to reach the local model. Read from the environment so a node can be
    pointed at Ollama (default) or any standard ``/v1`` model server without code changes."""

    backend: str = "ollama"  # "ollama" | "openai"
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    api_key: str | None = None
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> Config:
        backend = os.environ.get("ANTHILL_BACKEND", "ollama").lower()
        base_url = os.environ.get("ANTHILL_BASE_URL") or (
            DEFAULT_OLLAMA_URL if backend == "ollama" else DEFAULT_OPENAI_URL
        )
        return cls(
            backend=backend,
            model=os.environ.get("ANTHILL_MODEL", DEFAULT_MODEL),
            base_url=base_url,
            api_key=os.environ.get("ANTHILL_API_KEY"),
            timeout=float(os.environ.get("ANTHILL_TIMEOUT", "120")),
        )
```

Bump both the dataclass field default (`120.0`) AND the `from_env()` fallback string (`"120"`), to the
same new number, so a deployment that never sets `ANTHILL_TIMEOUT` gets the same protection as one that
constructs `Config()` directly.

### 3. Why the bump is needed everywhere, not just in `chat_stream` (the key finding of this task)

`anthill/web/app.py`'s `chat_stream` (verified, function starts at line 8032) is the ONLY call site in
the entire codebase that already compensates for the cold-start risk, verbatim at lines 8162-8176:

```python
        config = Config.from_env()
        config.backend = plane_inf.backend
        config.base_url = plane_inf.base_url
        config.model = plane_inf.model
        if plane_inf.api_key:
            config.api_key = plane_inf.api_key
        if plane_inf.backend == "openai":
            # A cloud / serverless org endpoint (e.g. RunPod) can cold-start from zero, which easily
            # exceeds the default 120s. Give the first request room before it gives up.
            try:
                _org_to = float(os.environ.get("ANTHILL_ORG_TIMEOUT", "300"))
            except ValueError:
                _org_to = 300.0
            config.timeout = max(float(getattr(config, "timeout", 120) or 120), _org_to)
        backend = build_backend(config)
```

Every OTHER call site that can reach an "openai"-backend (org/serverless) endpoint constructs
`Config.from_env()` and calls `build_backend()` directly, with NO equivalent bump (all verified via
`grep -rn "Config.from_env()\|build_backend("`):

- `anthill/web/agents_run.py:77-83` (agent runs)
- `anthill/web/scheduler.py:129/153, 607/610, 668/676, 814/819` (scheduled tasks, wiki revision jobs,
  skill distillation)
- `anthill/web/a2a.py:137/150` (agent-to-agent)
- `anthill/web/mcp_store.py:216/220` (MCP-backed queries)
- `anthill/council/engine.py:142/150` (the council's own per-member backend construction - this is the
  path Phase 3/4a/4b's `run_council()`/`review_completed_answer()` use)
- `anthill/node_agent/app.py:113/116`

**This is why the fix must be a shared default change, not a `chat_stream`-style per-call-site
`max(...)` bump copy-pasted five more times**: bumping the two shared defaults (file 1 and file 2 above)
closes the gap for every one of these call sites at once. `chat_stream`'s existing `max(...)` bump is
still correct and stays untouched - it becomes a no-op in the common case once the shared default already
meets or exceeds `ANTHILL_ORG_TIMEOUT`'s own default, which is exactly the point: one number, chosen once,
instead of five copies drifting out of sync.

**Recommended new value: `300.0`** (matching `ANTHILL_ORG_TIMEOUT`'s existing default of `"300"` in the
`chat_stream` snippet above). This is not an arbitrary new number - it is the number this codebase's own
author already judged sufficient for this exact cold-start scenario, so reusing it keeps one considered
number instead of introducing a second, uncoordinated one. State this reasoning explicitly in the PR body.

### 4. `anthill/web/templates/personalize.html:40-45` - the card to remove

```html
    <div class="compute-card soon">
      <i data-lucide="plug" style="font-size:22px">🔌</i>
      <div style="font-size:13px;margin-top:8px">Inference provider</div>
      <div class="text-sm text-muted" style="margin-top:2px">per-token</div>
      <span class="compute-pill">coming soon</span>
    </div>
```

Delete this `<div>` block only. The preceding `Self-hosted` `soon` card (lines 34-39) and the grid
container are untouched.

### 5. `docs/specs/intelligence-settings.md:15-18` - the roadmap line to correct

```markdown
- **Model & compute** is chosen from **selectable cards**, not a two-way radio. The account's one compute:
  1. **Local** - on this device (built).
  2. **Virtual private cloud** - your own connected endpoint (built; today's `solo_compute = cloud`).
  3. **Self-hosted** - a Mac mini or other box you run (coming soon).
  4. **Inference provider** - per-token (coming soon).
```

Remove line 4 (`**Inference provider** - per-token (coming soon).`) and renumber the Self-hosted line's
surrounding list to 3 items. Do not touch anything else in this file - it is a large, mostly-unrelated
design doc.

### 6. Existing test fixture pattern to follow (`tests/test_solo_model_settings.py`, verified in full)

The `_client(tmp_path, monkeypatch)` helper (lines 11-32) is the established pattern for a Personalize-page
test: builds a fresh sqlite-backed `TestClient` with one admin user, no fixtures beyond that. Reuse this
exact helper (import it or copy its body - whichever keeps the new test file self-contained) rather than
inventing a new one.

Existing coverage already asserts `"coming soon" in body` generically (`test_personalize_is_the_solo_settings_home_with_compute`,
line 84) - this assertion must keep passing after the "Inference provider" card is removed, since the
`Self-hosted` card is still `coming soon`. Do not weaken or delete that existing assertion; add new,
more specific assertions alongside it.

### 7. `tests/test_openai_compat.py` (verified in full, 3 tests, no existing timeout-value assertion)

No existing test pins the default `timeout` value - only behavior when a timeout/error occurs. Add a new,
separate test asserting the default itself, e.g. `OpenAICompatBackend("https://e/v1", "m").timeout == 300.0`,
so a future silent revert is caught immediately without touching the three existing behavioral tests.

## Build steps

1. Bump `anthill/inference/openai_compat.py`'s `timeout: float = 120.0` default to `300.0`.
2. Bump `anthill/config.py`'s `Config.timeout: float = 120.0` field default AND `from_env()`'s
   `os.environ.get("ANTHILL_TIMEOUT", "120")` fallback string to `300.0` / `"300"`.
3. Remove the "Inference provider" `coming soon` card from `personalize.html` (item 4 above).
4. Correct `docs/specs/intelligence-settings.md`'s compute-card list (item 5 above).
5. Add a new test in `tests/test_openai_compat.py` pinning the new default timeout value.
6. Add a new test in `tests/test_solo_model_settings.py` (or a new file, matching its fixture) asserting
   "Inference provider" no longer renders in `/personalize` while "Self-hosted" and generic "coming soon"
   still do.
7. Run `ruff check`, `ruff format --check`, `mypy`, full `pytest`. Confirm no new failures beyond the
   17 pre-existing `markitdown[pdf]`-dependency-gap failures already known from every prior phase this
   session.
8. No em/en-dashes, no TODO/FIXME/XXX markers (repo-wide CI gates).

## Explicitly out of scope

Same as `proposal.md`'s "Explicitly out of scope" section - Tiers 1-5, and the product council's
`inference-provider` member-lifecycle kind (Phase 5, skipped).
