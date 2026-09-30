# Tasks: PR #627 secure bare-VM LLM endpoint (SSH tunnel, Option A)

## Build steps

1. New `anthill/hosting/secure_tunnel.py`:
   - `generate_tunnel_keypair() -> TunnelKeypair` (dataclass: `private_key_pem` or OpenSSH private text,
     `public_key_line`) - Ed25519 via `cryptography.hazmat.primitives.asymmetric.ed25519` +
     `serialization` with `Encoding.PEM`/`PrivateFormat.OpenSSH` (private) and
     `Encoding.OpenSSH`/`PublicFormat.OpenSSH` (public). No `ssh-keygen` subprocess.
   - `restricted_authorized_keys_line(public_key_line, *, remote_port=8000) -> str` - prefixes the
     public key line with `command="echo no-shell",no-agent-forwarding,no-X11-forwarding,no-pty,
     permitopen="127.0.0.1:<remote_port>"`.
   - `SSHTunnelManager`: keyed (by an opaque string id, e.g. `f"{org_id}:{member_index}"`) manager of
     live `ssh -N -L 127.0.0.1:<local_port>:127.0.0.1:<remote_port> <user>@<host>` subprocesses. Mirror
     `remote/tunnel.py`'s `TunnelManager` shape: `_procs: dict[str, subprocess.Popen]`,
     `_keyfiles: dict[str, str]`, a `threading.Lock`, injectable `spawn` for tests. `start(key, *, host,
     user, remote_port, local_port, private_key_pem, spawn=None)`: writes the private key to a
     `tempfile.NamedTemporaryFile(delete=False)` chmod 0600, builds the argv (`-o BatchMode=yes -o
     StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -i <keyfile> -N -L ...`, mirroring
     `training/remote.py`'s `_ssh()` option style), spawns, stores. `stop(key)`: terminate the process if
     running, unlink the keyfile, drop from both dicts - idempotent (no-op if `key` unknown).
     `status(key) -> dict {running: bool}` via `proc.poll() is None`.
   - `free_local_port() -> int` - bind a `socket.socket` to `("127.0.0.1", 0)`, read `getsockname()[1]`,
     close, return it.
2. `anthill/hosting/provision.py`: extend `endpoint_is_secure()` - parse `endpoint.base_url` with
   `urllib.parse.urlsplit`, treat `hostname in ("127.0.0.1", "localhost")` as secure in addition to the
   existing `scheme == "https"` check. Keep `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT`/
   `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` handling for the remaining (cleartext-public) case unchanged.
3. `anthill/hosting/lambda_provision.py`:
   - `vllm_startup_script(model, api_key, *, authorized_keys_line="")`: change `-p 8000:8000` to
     `-p 127.0.0.1:8000:8000`; if `authorized_keys_line` is given, append a step appending it to
     `/home/ubuntu/.ssh/authorized_keys` (Lambda's default AMI user) before the `docker run` line. Keep
     the function usable with no key line (existing tests / any non-tunnel caller).
   - `LambdaLiveProvisioner.provision()`: after the instance is confirmed active, call
     `secure_tunnel.generate_tunnel_keypair()`, pass its `restricted_authorized_keys_line(...)` into the
     (now updated) startup script instead of launching before the tunnel exists - **note the ordering
     constraint**: the startup script is fixed at `launch()` time (before the IP/instance exists), so the
     keypair must be generated and its public line baked into `startup_script` BEFORE `client.launch(...)`
     is called, not after. Only after the instance is active does `secure_tunnel.free_local_port()` +
     `SSHTunnelManager.start(...)` run. On any exception from keypair/tunnel setup, `client.terminate(
     instance_id)` (existing guaranteed-teardown pattern) and return `ok=False` - do not fall back to the
     old public-IP `base_url`.
   - Build `base_url = f"http://127.0.0.1:{local_port}/v1"`; `endpoint_is_secure(endpoint)` now passes
     under default config (no plumbing changes needed there - task 2 already covers it).
   - Return the tunnel's private key (encrypted by the caller, mirroring how `api_key` is returned today
     and encrypted by `provision_run.py`) and local port through `ProvisionResult` or an equivalent side
     channel the caller can persist - confirm the exact shape by reading `ProvisionResult`'s fields before
     deciding whether to extend the dataclass or carry this via a second return value; do not silently
     drop it (a repeat of the "endpoint credential was discarded" bug class already caught once this
     session in `test_council_provision_run.py`).
4. `anthill/web/db.py`: add `org_lambda_tunnel_key_enc` (Text, default `""`) and
   `org_lambda_tunnel_port` (Integer, default `0`) columns to `OrgSettings`, plus matching
   `_ensure_columns` migration entries (existing pattern at the call sites around line 1174+).
5. `anthill/web/provision_run.py`:
   - `_mirror_lead_into_council`: fold the two new lead columns into `provider_config` alongside
     `ssh_keys`/`instance_type` (only when either is non-empty/non-zero, matching the existing
     conditional there).
   - `_MemberRef`: add `org_lambda_tunnel_key_enc`/`org_lambda_tunnel_port` properties reading from
     `provider_config`, mirroring `org_lambda_ssh_keys`/`org_lambda_instance_type`.
   - `provision_org`/`provision_council_member`: after a successful Lambda provision, persist the
     tunnel's encrypted private key + port (+ the VM's host/IP, needed for tunnel restart) into the
     lead's new columns or the member's `provider_config`, respectively - mirror exactly how
     `result.endpoint.api_key` is already encrypted and persisted right next to this code today.
   - `teardown_org`/`teardown_council_member`: call `secure_tunnel.manager.stop(key)` (or the module-
     level singleton chosen in task 1) before/alongside the existing provider teardown, and clear the two
     new persisted fields, mirroring how `org_model_endpoint`/`backend_handle` are cleared today.
6. `anthill/web/app.py`: new `_autostart_llm_tunnels()` - query every `OrgSettings` row (lead columns +
   each `org_council_members` entry) with `org_provider == "lambda"` and a non-zero persisted tunnel
   port, decrypt the stored key, and `SSHTunnelManager.start(...)` against the persisted host. Call it
   from `@app.on_event("startup")` in a `try/except: pass` alongside the three existing autostart calls -
   one org's failure must never block boot for the rest.
7. Tests:
   - `tests/hosting/test_secure_tunnel.py` (new): keypair generation + OpenSSH format round-trip via
     `cryptography`'s own public-key loader; `restricted_authorized_keys_line` shape; `SSHTunnelManager`
     start/stop/status with an injected fake `spawn` (a fake Popen with a controllable `.poll()`); keyfile
     is written 0600 and removed on stop; `free_local_port` returns a bindable port.
   - `tests/hosting/test_provision.py` (or wherever `endpoint_is_secure` is currently tested - locate
     before writing): loopback accepted, localhost accepted, spoof-lookalike host rejected, existing
     https/public-http cases unchanged.
   - `tests/test_lambda_provision.py`: extend with an injected fake tunnel manager/spawn so no real SSH
     process runs; new tests per proposal.md's "Unit-verifiable" acceptance list 1-4, 8. Confirm every
     EXISTING test in this file still passes with the new tunnel step present (inject a no-op fake by
     default so pre-existing tests that don't care about the tunnel are unaffected) - do not let this
     change force every existing test to know about tunnels.
   - `tests/test_council_provision_run.py`: extend the existing Lambda-reviewer coverage (mirroring
     `test_provision_reviewer_persists_the_generated_endpoint_key`) to also assert the tunnel key/port are
     persisted into the reviewer's `provider_config`, not discarded.
8. Update `docs/specs/llm-endpoint-secure-transport.md`: flip `Status: proposed` to `Status: done (Option
   A)`, and add a short note on the DataCrunch scope finding (see proposal.md) so a future session working
   on DataCrunch's bootstrap gap knows this helper is ready to adopt.
9. `changelog.d/+627-secure-transport.added.md`.
10. `ruff check`, `ruff format --check`, `mypy`, full `pytest` - must be green (not just the new/changed
    test files).

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section - Option B, `datacrunch_provision.py`,
OVHcloud/Scaleway live provisioners, rotation UI, mTLS, removing the insecure escape hatch.
