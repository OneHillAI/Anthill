# Phase 3: the Mixture-of-Agents (MoA) engine itself

This is a DRAFT task - implement real code, not a review. Draft an implementation against the
acceptance criteria below; be concrete (the code/diff) with a one-paragraph rationale.

## Scope: build the ENGINE only, not its wiring

This phase delivers a standalone, tested orchestration engine: given an account's council members and
a question, run them and return one synthesised answer. It does NOT wire this into any of the three
real answer surfaces (`anthill/wiki/ask.py`, `anthill/web/scheduler.py`, `anthill/web/agents_run.py`) -
that is Phase 4, a separate change. Do not touch those three files.

## Ground truth: what already exists (verified against the real repo, not assumed)

**The `InferenceBackend` interface** (`anthill/inference/base.py`) - the ONLY formal contract every
backend implements:

```python
@runtime_checkable
class InferenceBackend(Protocol):
    model: str
    def chat(self, messages: Sequence[Message], *, temperature: float = 0.2) -> str: ...
    def health(self) -> str | None: ...

def build_backend(config: Config) -> InferenceBackend:
    # config.backend == "ollama" -> OllamaBackend(base_url, model, timeout)
    # config.backend == "openai" -> OpenAICompatBackend(base_url, model, api_key, timeout)
```

`Message` (`anthill/inference/base.py`): `@dataclass class Message: role: str; content: str; images:
list[str] | None = None`.

`Config` (`anthill/config.py`): `@dataclass class Config: backend: str = "ollama"; model: str; base_url:
str; api_key: str | None = None; timeout: float = 120.0`.

**Every model call in this codebase today is synchronous and sequential.** Verified by grep across the
whole `anthill/` tree: zero uses of `asyncio.gather`, zero uses of `aiohttp`, zero uses of
`httpx.AsyncClient` for any inference call (the only 2 `httpx.AsyncClient` hits in the entire codebase
are OAuth token exchanges in `app.py`, unrelated to inference). The one and only
`concurrent.futures.ThreadPoolExecutor` usage anywhere (`anthill/mcp/client.py`, bridging the async MCP
SDK into sync code) is a single-worker bridge, not a fan-out pattern. **There is no existing
parallel-model-calling code to copy in this codebase - this phase introduces the first one.**

**The existing cross-check precedent** (`anthill/verify/verify.py`, cited by
`docs/specs/product-council-architecture.md` as the reason `ask.py`/`scheduler.py`/`agents_run.py` are
the right three surfaces) is SEQUENTIAL, not parallel: it builds one extra `OllamaBackend` and makes one
blocking call after the producer's own answer already exists. It also has a real, load-bearing memory
guard worth knowing about even though it doesn't directly constrain this phase:

```python
_VERIFIER_MIN_FREE_GB = 6.0
...
free = free_mem_gb()
if free is not None and free < _VERIFIER_MIN_FREE_GB:
    return CrossCheck(ok=None, reason=f"skipped: only {free:.1f} GB free - not loading a 2nd model "
                                       "under memory pressure")
```
This exists because loading a SECOND LOCAL model concurrently with the first can saturate a small Mac's
unified memory. It is NOT something this phase needs to reproduce for VPC-provisioned council members
(each already runs on its own dedicated cloud instance - see `docs/specs/product-council-architecture.md`
R2, already shipped). It IS a real consideration if a council has 2+ on-prem members (which Phase 2,
already shipped, gates for storage/idle footprint via `sizing.onprem_council_fits()`, but concurrent
INFERENCE-TIME memory pressure across simultaneously-active local models is a distinct concern Phase 2
did not address). If you have a clean way to reuse the SAME `free_mem_gb()` guard here for the on-prem
case, do so; if not, name it explicitly as a known gap rather than silently ignoring it.

**Nothing today resolves `org_council_members` into real backends.** Grepped the whole tree: outside
the settings-save UI (`anthill/web/app.py`) and provisioning (`anthill/web/provision_run.py`), the ONLY
other references to `org_council_members` are the schema column definition and a one-time backfill
migration. `anthill/web/plane_routing.py`'s `plane_inference()` - the existing single-model resolver
every answer surface uses today - only ever reads the LEGACY single-member columns
(`cfg.org_model_endpoint`, `cfg.org_model`, etc.), never `org_council_members`. **You must build the
"turn `org_council_members` into a list of callable backends" step as part of this phase** - it does not
exist yet anywhere.

**The member shape** (`_empty_member()` in `anthill/web/app.py`, JSON dicts inside
`OrgSettings.org_council_members`, relevant fields only):
```python
{
    "endpoint": "",         # base_url once provisioned/connected, else ""
    "provider": "",         # "onprem" | "lambda" | "runpod" | "datacrunch" | ... (provision.PROVIDER_KEYS)
    "model": "",
    "model_key_enc": "",    # encrypted per-member API key (Phase 1b fixed a real bug where this was
                            # discarded on provision; it is now populated for a live-provisioned member)
    "backend_status": "unconfigured",  # unconfigured | planned | provisioning | provisioned | error
    "backend_handle": "",
    "lifecycle": "vpc",     # "vpc" (built) | "inference-provider" (NOT built yet - Phase 5)
}
```
Index 0 in the list is the account's "lead" / core model (the UI's own term, `settings_organization.html`'s
"Core / Lead model" framing established in Phase 1b); indices 1+ are "reviewers". This is a UI/ordering
convention only, not a schema distinction - nothing enforces it structurally, but it is what every
existing test and the settings UI assume.

**Anthill's own dev-council** (`cli/dev-council.py`, the tool drafting THIS code right now) uses a
different lead convention - "the LAST model in the list is the lead synthesiser" - because that tool's
config list happens to be ordered proposers-then-lead. Anthill's PRODUCT convention (the settings UI, all
existing tests) is the opposite: index 0 is the lead. **Do not copy the dev-council's own "last = lead"
convention into this engine - use index 0, matching the product's own established UI/data convention.**

## Acceptance criteria

1. THE SYSTEM SHALL add a function that resolves an account's `org_council_members` into a list of
   real `InferenceBackend` instances (one per member), reusing `build_backend(Config(...))` exactly as
   every existing surface already does (`_backend_from_cfg` in `app.py`, `_run_task` in `scheduler.py`,
   `_backend_for` in `agents_run.py` - three near-identical copies of the same
   `Config` → `build_backend` boilerplate you can follow). Only members with `lifecycle == "vpc"` and a
   non-blank `endpoint`/`model` are resolvable today (`"inference-provider"` is Phase 5, not built - skip
   those members, do not error on them). Decide and justify where this resolver lives: extending
   `anthill/web/plane_routing.py` (which already owns "turn OrgSettings into inference-routing data" -
   `plane_inference()` is the existing single-member sibling) or a new module. Either is acceptable if
   justified; do not invent a THIRD, inconsistent way to build a `Config`/`InferenceBackend` beyond the
   `build_backend(Config(...))` convention every other call site already uses.
2. THE SYSTEM SHALL run the resolved backends' answers to the SAME question in the PROPOSE layer IN
   PARALLEL (not sequentially) - this is R4's explicit requirement ("THE SYSTEM SHALL NOT implement the
   council as a naive sequential N-model chain"). Since every `InferenceBackend.chat()` call is
   synchronous and there is no async equivalent anywhere in this codebase, use
   `concurrent.futures.ThreadPoolExecutor` (do not introduce `asyncio`/`aiohttp` machinery that nothing
   else in this codebase uses or expects).
3. THE SYSTEM SHALL feed the propose layer's outputs to ONE synthesis call (the lead - index 0 - or a
   dedicated synthesiser role if you have a well-justified reason to differ) that produces ONE final
   answer, per R4 ("a layer's outputs feed the next layer as context, ending in synthesis").
4. THE SYSTEM SHALL tolerate a single member's failure (timeout, unreachable endpoint, error) without
   failing the whole answer, as long as at least one member succeeds. THE SYSTEM SHALL return a clear,
   distinguishable failure only when EVERY member fails.
5. THE SYSTEM SHALL degrade to a single direct `chat()` call (no parallel fan-out, no synthesis
   overhead) when only ONE member resolves - this is the common case today (most accounts have no
   reviewers configured yet; Phase 1b's reviewer UI is opt-in). Do not add council latency/cost for an
   account that has not configured a council.
6. THE SYSTEM SHALL NOT modify `anthill/wiki/ask.py`, `anthill/web/scheduler.py`, or
   `anthill/web/agents_run.py` - wiring the engine into those three surfaces is Phase 4, explicitly out
   of scope here.
7. THE SYSTEM SHALL be tested with injectable/fake backends (implementing the `InferenceBackend`
   Protocol - `model` attribute + `chat()`; no live account, no network) covering AT MINIMUM: (a) true
   parallel execution (not sequential execution disguised as parallel - e.g. a fake backend whose
   `chat()` sleeps, asserting wall-clock time is closer to the SLOWEST member than the SUM of all
   members), (b) one member failing while others succeed still produces an answer, (c) every member
   failing produces a clear, distinguishable failure, (d) the single-member case takes the direct
   `chat()` path with no synthesis call, (e) the resolver correctly skips a member with an unrecognized
   lifecycle or a blank endpoint/model.

## Explicit open questions (flag honestly if you cannot resolve them cleanly - do not fabricate answers)

- Where the propose-layer prompt and the synthesis prompt should live and what they should say. Give
  your own reasonable design; this is not fully specified above on purpose (prompt engineering is
  legitimately your call here), but state your reasoning.
- Timeout handling per member (a slow member should not block the whole layer indefinitely). Propose a
  reasonable default; state it explicitly rather than leaving it unbounded.
- Whether/how to reuse `anthill/verify/verify.py`'s `_VERIFIER_MIN_FREE_GB` memory-pressure guard for a
  multi-on-prem-member council's concurrent inference (distinct from Phase 2's already-shipped
  idle/storage summed check). If you don't have a clean answer, say so rather than silently skipping it.
