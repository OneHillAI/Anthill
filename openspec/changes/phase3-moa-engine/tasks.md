# Tasks

- [ ] Add a resolver: `org_council_members` -> list of real `InferenceBackend` instances.
- [ ] Add the MoA orchestration function: parallel propose -> synthesis -> one answer.
- [ ] Single-member degrade path (no fan-out, no synthesis call).
- [ ] Per-member timeout + partial-failure tolerance.
- [ ] Tests with fake/injectable backends (no live account, no network).

## Reference: `InferenceBackend` / `Config` / `build_backend` (verbatim, `anthill/inference/base.py` +
`anthill/config.py`)

```python
# anthill/inference/base.py
@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    images: list[str] | None = None

@runtime_checkable
class InferenceBackend(Protocol):
    model: str
    def chat(self, messages: Sequence[Message], *, temperature: float = 0.2) -> str: ...
    def health(self) -> str | None: ...

def build_backend(config: Config) -> InferenceBackend:
    if config.backend == "ollama":
        return OllamaBackend(config.base_url, config.model, config.timeout)
    if config.backend == "openai":
        return OpenAICompatBackend(config.base_url, config.model, config.api_key, config.timeout)
    raise BackendError(...)

# anthill/config.py
@dataclass
class Config:
    backend: str = "ollama"   # "ollama" | "openai"
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    api_key: str | None = None
    timeout: float = 120.0
```

## Reference: the EXISTING single-member org-plane resolve-and-build pattern (verbatim,
`anthill/web/agents_run.py` - the cleanest of three near-identical copies; `scheduler.py`'s
`_run_task` does the same thing inline)

```python
# anthill/web/agents_run.py
def _backend_for(agent, plane_inf):
    from ..config import Config
    from ..inference.base import build_backend

    config = Config.from_env()
    config.backend = plane_inf.backend
    config.base_url = plane_inf.base_url
    config.model = agent.model or plane_inf.model
    if plane_inf.api_key:
        config.api_key = plane_inf.api_key
    return build_backend(config)

# called as:
plane_inf = plane_inference(plane, cfg, decrypt=decrypt)  # raises PlaneUnavailable if org down
backend = _backend_for(agent, plane_inf)
```

## Reference: the existing single-member org-endpoint resolver your new multi-member one should mirror
(verbatim, `anthill/web/plane_routing.py`)

```python
def _org_api_key(cfg, decrypt: Callable[[str], str]) -> str | None:
    key_enc = getattr(cfg, "org_model_key_enc", "") or ""
    if not key_enc and (getattr(cfg, "org_provider", "") or "").strip().lower() == "runpod":
        key_enc = getattr(cfg, "org_provision_key_enc", "") or ""
    if not key_enc:
        return None
    try:
        return decrypt(key_enc)
    except Exception:
        return None

def _org_endpoint(cfg, decrypt) -> tuple[str, str, str | None] | None:
    """(endpoint_url, model, api_key) for a validated org backend, else None (not connected)."""
    endpoint = (getattr(cfg, "org_model_endpoint", "") or "").strip()
    if not planes.org_available(cfg) or not endpoint:
        return None
    return endpoint, (getattr(cfg, "org_model", "") or ""), _org_api_key(cfg, decrypt)
```

Your multi-member resolver does the analogous thing per COUNCIL MEMBER instead of the single legacy
`org_model_endpoint`/`org_model`/`org_model_key_enc` columns: read `cfg.org_council_members` (JSON) the
same shape `_members_from_cfg(cfg) -> list[dict]` in `anthill/web/app.py` already parses, decrypt each
member's `model_key_enc` the same way, skip members that aren't ready (no endpoint/model, or
`lifecycle != "vpc"`), and build one `Config` -> `build_backend(...)` per resolvable member, exactly
like `_backend_for` above does for the single legacy case.

CAUTION on where this lives: `anthill/web/app.py` is the FastAPI app module (~11,000 lines) that will
eventually need to IMPORT your new council engine (Phase 4 wires it in). Do not have your new module
import `_members_from_cfg` FROM `anthill.web.app` - that risks a circular import once app.py imports
you back. Either duplicate the small member-parsing logic (it's a short JSON-parse-and-merge-defaults
function) into your new module/`plane_routing.py`, or extract it to a shared low-level location neither
of you needs to import the other for. State which you chose and why.

## Reference: the member shape (`_empty_member()`, `anthill/web/app.py`, relevant fields only)

```python
{
    "endpoint": "",         # base_url once provisioned/connected
    "provider": "",         # "onprem" | "lambda" | "runpod" | "datacrunch" | ...
    "model": "",
    "model_key_enc": "",    # encrypted per-member API key
    "backend_status": "unconfigured",
    "backend_handle": "",
    "lifecycle": "vpc",     # "vpc" (built) | "inference-provider" (Phase 5, not built - skip these)
}
```
Index 0 = the lead/core model (product convention, NOT the dev-council tool's own "last = lead"
convention - see proposal.md). `anthill/web/app.py` already has:
```python
def _members_from_cfg(cfg) -> list[dict]:
    """The account's council members, in order; [] for no cfg or no council configured yet."""
    ...
```

## Reference: `verify.py`'s memory-pressure guard, for your open-question answer on reusing it
(verbatim, `anthill/verify/verify.py`)

```python
_VERIFIER_MIN_FREE_GB = 6.0
...
from ..hosting.sizing import free_mem_gb
free = free_mem_gb()
if free is not None and free < _VERIFIER_MIN_FREE_GB:
    return CrossCheck(ok=None, reason=f"skipped: only {free:.1f} GB free - not loading a 2nd model "
                                       "under memory pressure")
```

## Reference: the one existing (single-worker) `ThreadPoolExecutor` bridge in this codebase, for style
consistency (verbatim, `anthill/mcp/client.py` - NOT a fan-out pattern, just showing the codebase's
existing comfort level with this stdlib module so you don't need to justify introducing it from scratch)

```python
def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()
```
