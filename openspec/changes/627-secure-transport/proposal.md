# PR #627: make a bare-VM LLM endpoint actually securable

## Why

`docs/specs/llm-endpoint-secure-transport.md` (merged, `Status: proposed`) names the gap: for a bare-VM
provisioner there are only two reachable states today - refuse and tear down (safe, unusable), or
`ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` (usable, cleartext). There is no third state, so a safe default is
also an unusable one, which pressures operators toward the unsafe flag in practice.

**Recommendation: Option A (SSH tunnel)**, per the spec's own recommendation - it closes the inference
port rather than merely encrypting it, needs no CA/DNS, and keeps the two EU-sovereign bare-VM
provisioners (OVHcloud, Scaleway - still planner stubs) on the same story. Option B (pinned TLS) stays
the documented fallback if tunnel supervision proves fragile; not built now.

## Scope finding not in the original spec text (grounded against current code, not just the spec)

The spec names "Lambda today; OVHcloud and Scaleway next" as the bare-VM provisioners needing this. Since
the spec was written, **`anthill/hosting/datacrunch_provision.py` shipped as a third live bare-VM
provisioner** (this session, PRs earlier in this workstream) with the identical shape: public IP, vLLM on
`:8000`, the same `endpoint_is_secure()` refusal gate. It is **explicitly, and separately, already blocked**
on its own pre-existing gap: DataCrunch's create-instance API documents no user-data/cloud-init field, so
there is no confirmed way to get vLLM (or an authorized_keys line) onto the box at all - the module's own
docstring says "we refuse to invent one" and defers to "an SSH-based post-boot install... once the instance
has an IP" as a documented, separate open question.

**Decision**: build the secure-channel helper (keypair, restricted-authorized_keys line, tunnel
supervisor, `endpoint_is_secure()` loopback recognition) as a shared module so DataCrunch can adopt it the
day its bootstrap gap closes, but **do not wire it into `datacrunch_provision.py` in this change** - there
is no working serving path there yet to secure, and inventing a user-data mechanism to unblock it would
silently reverse that module's own explicit prior decision. `datacrunch_provision.py` is untouched by this
PR. OVHcloud/Scaleway are still plan-only stubs (`_VpcProvisioner` subclasses with no live `provision()`)
and are likewise untouched - there is nothing to wire yet.

## What already exists and is REUSED

- **`anthill/remote/tunnel.py`'s `TunnelManager`** - an already-shipped, already-tested supervised-
  subprocess pattern (Popen + `poll()`-based liveness, injectable `spawn`, `start`/`stop`/`status`) for a
  *different* tunnel (outbound cloudflared, exposing this Anthill instance off-LAN). The **shape** - a
  process-wide manager owning live subprocess handles, restarted from a DB-backed startup hook - is exactly
  what Option A's supervision problem needs, so the new SSH-forward supervisor mirrors this class's
  structure rather than inventing a new one. It is not reused directly: cloudflared's tunnel carries
  *inbound* app traffic and needs no key material; ours is an *outbound* local port-forward keyed by
  private-key auth, and (unlike the single global cloudflare tunnel) there can be more than one live tunnel
  at once - one per Lambda-backed council member.
- **`anthill/web/app.py`'s `@app.on_event("startup")`** already calls `_autostart_remote_access()` -
  "re-open a configured off-LAN tunnel on boot." A sibling `_autostart_llm_tunnels()` is added to the same
  handler, following the same try/except-swallow convention as the other three autostart calls there (a
  failure to re-establish one org's tunnel must never block app startup for everyone else).
- **`anthill/web/plane_routing.py`'s `org_reachability()`** needs **no code change**. Verified: it already
  does a real `_list_models()` round-trip against `cfg.org_model_endpoint` and reports `"unreachable"` on
  any exception. Once `org_model_endpoint` is `http://127.0.0.1:<port>/v1`, a dead tunnel means nothing is
  listening on that local port, so the existing `httpx.ConnectError` path already reports the endpoint
  down. The acceptance criterion "`org_reachability()` must report the endpoint down when the tunnel is
  down" is satisfied by the tunnel supervisor actually closing/never binding the local port when the SSH
  process is not connected - not by new reachability logic.
- **`anthill/hosting/endpoint.py`'s `OrgEndpoint`/`validate`/`_list_models`/`_round_trip`** - unchanged.
  Confirmed: these are generic HTTP calls against `base_url`; a loopback `base_url` needs nothing extra
  here. (This file only needs changes for Option B's cert pinning, which is not being built.)
- **`anthill/inference/openai_compat.py`'s `OpenAICompatBackend`** - unchanged, same reasoning as above
  (Option B only).
- **`anthill/web/db.py`'s `org_model_key_enc` / `org_provision_key_enc` / `org_hf_token_enc`** - the
  AES-256-GCM-at-rest convention (`anthill.web.crypto.encrypt`/`decrypt`) the new SSH private key column
  follows exactly.
- **`anthill/web/provision_run.py`'s `_MemberRef`/`_mirror_lead_into_council`/`provider_config` nesting** -
  Lambda-specific launch config (`ssh_keys`, `instance_type`) already nests under each council member's
  `provider_config` dict rather than being generic top-level fields; the new per-member tunnel key + local
  port follow the same nesting (Lambda/bare-VM-specific, not every provider needs them), for the same
  reason `ssh_keys`/`instance_type` do. **Each council member is its own potential Lambda VM** (confirmed:
  `test_provision_reviewer_persists_the_generated_endpoint_key` provisions a *reviewer* member, index 1,
  independently of the lead) - so the tunnel key and local port must be per-member, not a single
  account-level secret, exactly mirroring how `model_key_enc` is already per-member and not account-level.
- **`anthill/training/remote.py`'s `_ssh()` argv-building convention** (`BatchMode=yes`,
  `StrictHostKeyChecking=accept-new`, `-p <port>`, `-i <key_path>` when set) - mirrored for the new tunnel's
  SSH invocation's option style, though the process shape differs completely: `training/remote.py` runs
  short-lived, run-to-completion `subprocess.run` calls (ship data, run one command, fetch results); the
  new code needs a long-lived supervised `Popen` (`ssh -N -L ...`, running until torn down or the process
  dies), which does not exist anywhere in this codebase yet and does not reuse `training/remote.py`'s
  functions directly - only its option conventions.

## What's new

1. **`anthill/hosting/secure_tunnel.py`** (new module - the shared helper every bare-VM provisioner will
   use): keypair generation (Ed25519 via the `cryptography` package already vendored for AES-GCM, OpenSSH
   wire format - no `ssh-keygen` binary dependency, keeps keygen pure-Python and unit-testable),
   `restricted_authorized_keys_line(public_key)` (forced `command=`, `no-pty`, `no-agent-forwarding`,
   `no-X11-forwarding`, `permitopen="127.0.0.1:<remote_port>"` - port-forward only, no shell, per the
   spec's Security Considerations), and an `SSHTunnelManager` supervising N keyed local-forward `ssh -N -L`
   subprocesses (one per `(org_id, member_index)`), mirroring `TunnelManager`'s Popen/`poll()`/injectable-
   `spawn` shape. The private key is written to a restricted-permission temp file for the process's
   lifetime (needed by `ssh -i`) and removed on stop.
2. **`vllm_startup_script`** (`lambda_provision.py`): bind vLLM to loopback
   (`-p 127.0.0.1:8000:8000` instead of `-p 8000:8000`) and append the generated tunnel public key to the
   default user's `authorized_keys` (restricted command) in the same cloud-init/user-data script Lambda
   already runs at launch - no new Lambda API call, no new "registered key name" concept; this is
   independent of the admin's own optional `ssh_key_names`.
3. **`LambdaLiveProvisioner.provision()`**: after the instance is active (unchanged polling), generate the
   tunnel keypair, launch the startup script with the public key baked in, start the SSH tunnel via
   `SSHTunnelManager`, and set `base_url = f"http://127.0.0.1:{local_port}/v1"` instead of the public IP.
   `endpoint_is_secure()` now accepts this loopback URL by construction (see below), so under the default
   config (no env flags) this succeeds - satisfying "WHERE the secure channel is established, provisioning
   SHALL succeed under the default configuration." On any step of tunnel setup failing, terminate the
   instance (existing guaranteed-teardown pattern) and return `ok=False` - **no fallback to the old
   public-IP path**: a failed secure channel must not silently regress to the pre-existing insecure one.
4. **`endpoint_is_secure()`** (`hosting/provision.py`): accept a loopback `base_url` (host is exactly
   `127.0.0.1` or `localhost`, parsed via `urllib.parse` rather than substring match, so
   `http://127.0.0.1.evil.example/v1` cannot spoof it) as secure, alongside the existing `https://` check.
   `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` continues to work unchanged, since it only gates *cleartext
   public* endpoints today and a loopback URL was never cleartext-public to begin with.
5. **`anthill/web/db.py`**: two new `OrgSettings` columns for the lead's legacy singleton storage -
   `org_lambda_tunnel_key_enc` (Text, AES-GCM, default `""`) and `org_lambda_tunnel_port` (Integer, default
   `0`) - plus `_ensure_columns` migration entries. `_mirror_lead_into_council` folds both into the lead's
   `provider_config` (alongside the existing `ssh_keys`/`instance_type`) exactly as it already does for
   those two fields. A reviewer member (index >= 1) stores the same two keys directly in its own
   `provider_config` dict - no schema change needed there, it is already a free-form JSON blob.
6. **`anthill/web/provision_run.py`**: `_MemberRef` grows two more `provider_config`-backed properties
   (`org_lambda_tunnel_key_enc`, `org_lambda_tunnel_port`) alongside the existing `org_lambda_ssh_keys`/
   `org_lambda_instance_type`, following the identical pattern.
7. **`anthill/web/app.py`**: new `_autostart_llm_tunnels()`, called from the existing `@app.on_event(
   "startup")` handler the same way `_autostart_remote_access()` is - iterate every `OrgSettings` row (lead
   + every council member) with `org_provider == "lambda"` and a persisted tunnel port + encrypted key, and
   re-`start()` that member's tunnel against the VM's last-known IP (already stored in `org_model_endpoint`
   before this change rewrote it to loopback - the **VM's public IP must additionally be persisted
   somewhere the restart path can read**, since the loopback URL alone no longer carries it; stored
   alongside the tunnel port in `provider_config` as `tunnel_remote_host`). A failure to restart one org's
   tunnel is caught and swallowed (logged), matching the existing three autostart calls' try/except
   convention - one org's dead VM must never block the whole app's boot.

## A design question resolved during grounding, not left implicit

The stable local port (spec: "pin a stable local port - the stored `org_model_endpoint` embeds it")
is chosen once, at first successful tunnel establishment, by asking the OS for a free ephemeral port
(bind to port 0, read back the assigned port, close it, hand that number to `ssh -L`), then persisted in
`provider_config.tunnel_local_port`. Every later restart (including the boot-time autostart) reuses the
persisted port rather than re-picking one - re-picking on every restart would change `org_model_endpoint`
out from under anything that cached the old URL. A collision (the OS handing back a port something else
grabbed between the check and the bind) is possible but rare and self-correcting: `ssh -N -L` fails to
bind, the tunnel manager records the failure, and the reachability probe reports "unreachable" until the
admin re-provisions (regenerating a fresh port) - it does not silently retry onto a different port, which
would again invalidate the stored endpoint.

## Explicitly out of scope

- Option B (pinned-TLS) - not built; the spec's own fallback, kept as a documented alternative only.
- `datacrunch_provision.py` - untouched; see "Scope finding" above.
- OVHcloud/Scaleway live provisioners - still planner-only stubs; nothing to wire yet.
- Cert/key rotation UI or automatic rotation on a schedule - the spec asks to "rotate the pair on
  re-provision," which a fresh `provision()` call already does by construction (a new keypair is generated
  every time); a standalone "rotate without re-provisioning" admin action is not requested and not built.
- mTLS, or removing `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` - both explicitly out of scope per the spec.
- mDNS/multi-host Anthill deployments - the spec itself notes the endpoint is bound to the Anthill host,
  "acceptable, since Anthill is the only client of the org plane"; this change does not change that.

## Acceptance criteria (from the spec, mapped to this change)

**Unit-verifiable** (extend `tests/test_lambda_provision.py`; new `tests/hosting/test_secure_tunnel.py`
for the shared helper, model-free with injected `spawn`/socket):

1. Default config (no env flags set): a Lambda provision with a working tunnel succeeds and returns a
   `http://127.0.0.1:<port>/v1` endpoint; `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` is not required.
2. The returned endpoint is never `http://<public-ip>:8000/v1`.
3. A failed tunnel/keypair/authorized_keys step returns `ok=False` and the fake client's instance was
   terminated (no billable VM left).
4. `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` set: the loopback-secured endpoint is accepted, not refused.
5. `endpoint_is_secure()`: accepts `https://...` (unchanged), accepts `http://127.0.0.1:<port>/v1` and
   `http://localhost:<port>/v1`, still refuses `http://1.2.3.4:8000/v1` and a spoof attempt like
   `http://127.0.0.1.evil.example/v1`.
6. `SSHTunnelManager`: start/stop/status with an injected `spawn`; a "dead" process (fake `poll()` !=
   None) reports not-running; stop() removes the temp key file.
7. The generated keypair round-trips: the public-key line `restricted_authorized_keys_line()` produces
   is accepted by `cryptography`'s own OpenSSH public-key loader (proves the format is well-formed without
   a live SSH server).
8. Every existing `test_lambda_provision.py` test still passes with the new tunnel step injected as a fake
   (no real subprocess/network in unit tests).

**Live-only** (requires a real provisioned Lambda VM; record the result per the #254/#355 precedent):

9. The VM's `:8000` is not reachable from a third-party host (only reachable via the tunnel).
10. An org-plane chat turn round-trips over the tunnel (`hosting/endpoint.py::validate` passes).
11. Killing the SSH process (simulating a dropped tunnel) makes `org_reachability()` report the endpoint
    down without any code path change - confirms the "no new reachability logic needed" claim above holds
    against a real process, not just the reasoning.
12. Restarting the Anthill process re-establishes the tunnel via `_autostart_llm_tunnels()` and the stored
    `org_model_endpoint` becomes reachable again without a re-provision.

`ruff check`, `ruff format --check`, `mypy`, full test suite must pass. No em/en-dashes, no TODO/FIXME/XXX
markers.
