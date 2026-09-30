# Spec: make a bare-VM LLM endpoint actually securable

Status: done (Option A, PR #627). Built for `hosting/lambda_provision.py` only. `hosting/
datacrunch_provision.py` shipped after this spec was written, with the identical bare-VM shape, but is
untouched by this change: its own module docstring already flags a separate, unresolved gap (no confirmed
user-data/cloud-init field to install anything at boot), so there is nothing to secure there yet. The
shared helper (`hosting/secure_tunnel.py`) is ready for DataCrunch to adopt once that gap closes.
OVHcloud/Scaleway remain planner-only stubs with no live `provision()` to wire up.
Lane: `pillar:privacy`
Relates to: [`llm-endpoint-transport-posture.md`](llm-endpoint-transport-posture.md) - this implements the
"Production posture (the real fix, tracked separately)" section it still defers. Builds on PR #432 (the
Bearer key), #534 (warn + fail-safe refusal) and the tenancy-isolation audit that flipped the default to
refuse-by-default.

## Problem

The transport posture is now honest but **incomplete**: for a bare-VM provisioner there is no way to get a
secure endpoint at all. The two reachable states are

- **default** - refuse to return a plaintext endpoint and tear the instance down (safe, but no endpoint), or
- **`ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT`** - serve `http://<public-ip>:8000/v1` in cleartext (an endpoint,
  but the Bearer key, prompt, wiki context and answer all cross the public internet readable by any hop,
  and the key can be replayed to use and bill the org's own model).

There is no third state. So today **Lambda serving is either off or unsafe**, and the only way an operator
gets a working bare-VM endpoint is by consciously choosing cleartext. A safe default that is also unusable
puts steady pressure on operators to set the unsafe flag - which is how the cleartext path gets taken in
practice.

The API key from #432 authenticates the *caller*; it does nothing to protect the *channel*. The refusal
default (correctly) declines to ship the channel. Nothing yet *builds* one.

## Why this is now blocking

`hosting/lambda_provision.py:224` returns `base_url = f"http://{ip}:{_VLLM_PORT}/v1"`, from a launch
script that runs vLLM with `-p 8000:8000` on a public IP. Lambda is the only current provisioner with this
shape - RunPod serving (`https://api.runpod.ai/v2/<id>/openai/v1`) and the wiki host
(`https://{podId}-{port}.proxy.runpod.net`) get TLS from the provider, and training already moves over SSH.

But the shape, not the vendor, is the problem: **the planned OVHcloud and Scaleway provisioners
(`hosting/provision.py`, today `eu_sovereign = True` planner stubs) are bare VMs too.** Under the current
rules they will provision, refuse, and tear down - i.e. EU-sovereign serving cannot ship in a usable *and*
safe state until a secure bare-VM channel exists. This spec is a prerequisite for those two providers, on
precisely the clouds whose selling point is data protection.

## Requirements (EARS)

- The system SHALL provide a secure, working channel to a bare-VM inference endpoint, so that the
  refuse-by-default posture is satisfied **without** the operator opting into cleartext.
- The system SHALL NOT expose a provisioned endpoint's inference port to the public internet in cleartext.
- The system SHALL NOT use TLS with certificate verification disabled: an unverified channel is not a fix,
  it is MITM-able while looking secure.
- The channel SHALL be a shared helper used by every bare-VM provisioner (Lambda today; OVHcloud and
  Scaleway next), not reimplemented per vendor.
- WHERE the secure channel is established, provisioning SHALL succeed under the default configuration,
  with `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` unset.
- `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` SHALL continue to be honoured as a hard override and SHALL NOT
  refuse an endpoint secured by this mechanism.
- WHERE the secure channel cannot be established, provisioning SHALL return `ok=False` and tear the
  billable instance down, preserving the existing teardown guarantee.

## Options

### A. SSH tunnel (recommended)

Bind vLLM to loopback on the VM (`-p 127.0.0.1:8000:8000`) so the port is not reachable from the internet
at all, and reach it through an SSH local forward from the Anthill host. `org_model_endpoint` becomes
`http://127.0.0.1:<local-port>/v1`.

- Closes the port rather than merely encrypting it - the strongest posture, and it removes the
  scan-and-replay exposure entirely.
- No CA, no DNS name, no third party in the trust path, which keeps the sovereignty story clean and ports
  unchanged to OVHcloud and Scaleway.
- **Prerequisite (do not assume reuse):** Anthill does not today hold SSH material that can open this
  tunnel. `org_lambda_ssh_keys` is registered key *names* (the public keys Lambda installs on the VM -
  `web/db.py`), not a private key. The training path uses a *different* key (`ANTHILL_TRAINING_SSH_KEY`)
  against a *different* host (`training_gpu_endpoint`) and is one-shot command exec, not a supervised
  tunnel (`training/remote.py`). Lambda *serving* opens no Anthill->VM SSH session at all - vLLM comes up
  from a launch-time cloud-init script (the "serving-bootstrap gap to confirm live",
  `lambda_provision.py`). So A must **generate and securely hold a new Anthill-controlled keypair** for the
  serving VM: create the keypair, register/install its public half at launch, and store the private half
  encrypted alongside the other org secrets.
- Cost: the new keypair above, **plus** a long-lived tunnel process per endpoint built greenfield.
  Supervision is the real work and has two concrete failure modes the implementing session must handle:
  (a) pin a **stable** local port (the stored `org_model_endpoint` embeds it), and (b) **re-establish the
  tunnel on Anthill restart**, or the stored endpoint silently breaks. `org_reachability()` must report the
  endpoint down when the tunnel is down. The endpoint is also bound to the Anthill host - acceptable, since
  Anthill is the only client of the org plane.

### B. TLS with a pinned self-signed certificate

Generate a cert on the VM at launch, return it (or its fingerprint) through the provisioner, and have
**every** outbound client to the endpoint verify against that pin - `httpx(..., verify=<pinned-cert.pem>)`,
which is strict verification against exactly that cert. There are two such clients and both must pin: the
provisioning/validation + reachability path (`OrgEndpoint` in `hosting/endpoint.py`, via `_headers` /
`_round_trip` / `validate`) and the inference path (`OpenAICompatBackend` in `inference/openai_compat.py`,
which today takes no cert/verify argument).

- This is **not** unverified TLS (certificate verification stays on): pinning is strict verification
  against a known key, so it answers the prior spec's objection directly.
- No tunnel process to supervise.
- Cost: the pinned cert must be captured at provision time and plumbed through `OrgSettings` into both
  clients above; cert rotation is unhandled; the inference port stays internet-reachable (encrypted, but
  scannable and DoS-able).

### C. Public CA certificate (rejected for now)

Caddy/Let's Encrypt needs a DNS name. Anthill has no domain or DNS-API concept, and IP-to-hostname
services (`sslip.io` and friends) put a third party in the trust path, contradicting the sovereignty
posture.

**Recommendation: A**, with B as the fallback if tunnel supervision proves fragile in practice. Note that A
and B are closer on cost than they first appear: neither reuses existing trust material - A plumbs a new
private key through provisioning + `OrgSettings`, B plumbs a pinned cert through the same. A is still
preferred because it *closes* the port rather than leaving it internet-reachable, but the decision rests on
posture, not on A being cheap.

## Security considerations

Option A introduces one new secret: the SSH private key Anthill uses to open the tunnel. Store it
encrypted at rest alongside the other org secrets (AES-GCM, as with `org_model_key_enc`), scoped per org
so a leak exposes only that org's serving VM, not the fleet, and treat it as sensitively as the model key
- because vLLM binds to loopback and the inference port is never public, this key is the only remote path
into the VM. Reduce that blast radius where practical: install the public half with a forced/restricted
`authorized_keys` command (port-forward only, no shell) and rotate the pair on re-provision. The key
protects the channel only; it does not change caller authentication, which stays the Bearer key from #432.

## Acceptance

Split by how each criterion is checkable, so the implementing session does not call it done on unit tests
alone (the same "green units != fully verified" reality flagged on #254 and #355).

**Unit-verifiable** (extend `tests/test_lambda_provision.py`, model-free with an injected client, in the
style of the existing provisioner tests):

- With no configuration set (the safe default), a Lambda provision **succeeds** and returns a non-cleartext
  endpoint; `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` is not required for a working endpoint.
- The returned `org_model_endpoint` is a loopback (A) or pinned-TLS (B) URL, never `http://<public-ip>`.
- A failed channel setup returns `ok=False` and tears the instance down (no billable VM left running).
- `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` set: the endpoint secured by this mechanism is accepted, not
  refused.

**Live-only** (requires a real provisioned VM; cannot be shown by an injected-client unit test - verify on
a first live run and record the result, as with #254/#355):

- The VM's :8000 is not reachable from a third-party host.
- An org-plane chat turn round-trips over the provisioned endpoint (`hosting/endpoint.py::validate`
  passes), and `org_reachability()` reports the endpoint down when the channel is down.

## Out of scope

- The RunPod serverless and wiki-host paths (already TLS, terminated by the provider).
- Training transport (already SSH).
- mTLS between Anthill and the endpoint; the Bearer key from #432 remains the caller authentication.
- Removing `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT`. It stays as an escape hatch; this spec removes the
  *need* to reach for it.
