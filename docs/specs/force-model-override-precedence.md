# Spec: ANTHILL_FORCE_MODEL is a hard override that wins over the account model

Status: implemented. Lane: `pillar:model`. Issue: #567 (filed alongside the #541 injection-tier fix).

## Problem

`ANTHILL_FORCE_MODEL` is documented as a hard pin: "force the configured model rather than have the
router load an oversized one". It is meant for a constrained-hardware deployment, or a reproducible
test/eval gate, to serve exactly one model. In practice the DB account model silently overrode it:

- The chat route pins the router to the account's configured local model (#413,
  `respect-configured-local-model.md`): `TaskRouter(pinned_model=config.model ...)`. The router
  constructor preferred that explicit argument over the env var (`pinned_model if pinned_model is not
  None else os.environ[...]`), so a passed account model shadowed `ANTHILL_FORCE_MODEL` entirely.
- `_backend_from_cfg` sets `config.model = cfg.ollama_model or config.model`, never consulting the force
  var, so the non-router chat callers (review gate, memory, taskgen) also served the account model.

Consequences (both observed and verified in #567):

1. The `qa/chat-eval` injection-resistance gate sets `ANTHILL_FORCE_MODEL=qwen3:8b`, but the throwaway
   account's `cfg.ollama_model` (whatever `/setup` chose, e.g. `qwen2.5:3b`) is what actually served. The
   gate's model pin was ineffective, so it could silently certify a weaker model than intended - and
   injection resistance depends on the tier (`qwen2.5:3b` 4/4 hijacked vs `qwen3:8b` 0/4).
2. An operator on constrained hardware could not force a single served model when an account model was
   configured.

## Requirements

- When `ANTHILL_FORCE_MODEL` is set, it is a HARD override and serves for every non-vision task,
  regardless of the account's configured model - on both the router serve path and the non-router
  `_chat` callers built via `_backend_from_cfg`.
- Precedence: `ANTHILL_FORCE_MODEL` (env) > account pin `cfg.ollama_model` (#413) > task routing.
- Opt-in: with the var unset, behaviour is unchanged - the #413 account pin still wins over task routing.
- Local (`ollama`) only. The org/cloud (`openai`) backend serves its own provisioned model, unaffected.
- Exact-match install is preserved: a forced model that is not installed falls through to normal routing
  rather than 404 (existing `TaskRouter.pick` behaviour, unchanged).

## How

- `TaskRouter.__init__`: `env_force = ANTHILL_FORCE_MODEL.strip(); self.pinned_model = env_force or
  (pinned_model or "").strip()` - the env force wins over the passed pin.
- `_backend_from_cfg`: after resolving `config.model` from `cfg.ollama_model`, apply
  `config.model = ANTHILL_FORCE_MODEL` when set and the backend is `ollama`.

## Acceptance criteria

- `ANTHILL_FORCE_MODEL=qwen3:8b` with a passed `pinned_model="qwen2.5:3b"` serves `qwen3:8b` for
  GENERAL/DOCUMENT/etc. (`tests/test_router_pin.py::test_env_force_wins_over_a_passed_pin`).
- With the var unset, a passed account pin still stands (`test_passed_pin_stands_when_no_env_force`,
  `test_413_configured_model_served_not_a_larger_installed_one`).
- `_backend_from_cfg` returns a backend whose `.model` is the forced model when set, else the account
  model (`test_backend_from_cfg_honors_env_force`).

## Related (not in this change)

- Part 1 of #567 (a security-sensitive turn must not degrade to a weaker tier) is already shipped in #541
  (`injection-defence-model-tier.md`): an injection-suspect turn is forced onto `router.pick(GENERAL)`,
  never the fast tier, with an output-side hijack guard that refuses rather than leaks. This spec makes
  that GENERAL pick honour a forced model too.
- Harness follow-up (internal `qa/chat-eval`, separate repo): set the account model at setup as
  defence-in-depth and add a `gate_injection_web` case. Not required now that the force var works.
