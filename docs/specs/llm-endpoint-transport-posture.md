# Spec: provisioned LLM endpoint transport posture

Status: accepted
Lane: `pillar:privacy`
Relates to: the codebase security review; PR #432 (C2) added the API key but noted transport as a residual.

## Thesis

When Anthill provisions a GPU instance (Lambda / RunPod) it serves the model via vLLM at
`http://<public-ip>:8000/v1`, protected by a Bearer API key (added in #432). The key + all
prompts/responses still travel over **plaintext HTTP on a public IP**, so a network eavesdropper can read
the key and traffic. Fully fixing this is a deployment-architecture change (TLS termination with a trusted
cert, or reaching the instance over private networking) that spans provisioning, client cert trust, and
cert retrieval - out of scope for a contained patch, and a partial "HTTPS with `verify=False`" would be
*worse* (it invites MITM while looking secure).

## Requirement (updated: secure by default)

Originally this warned by default and refused only when `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` was set.
The tenancy-isolation audit reclassified "ships cleartext unless you set a flag" as the wrong default
for a sovereignty-first product: the safe posture must not depend on the operator knowing a flag exists.

- The system MUST NOT stand up a plaintext public endpoint **silently**: provisioning MUST surface the
  cleartext posture to the operator (in the result detail and the logs).
- Provisioning MUST **refuse by default** to return a plaintext endpoint, and MUST tear down the
  (billable) instance. This is the default with no configuration.
- Accepting cleartext MUST be a **conscious opt-in**: only when `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT`
  is set does provisioning proceed, having warned.
- `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` is now the default behaviour. It is still honoured as a **hard
  override** (it wins over the opt-in), so an operator who already set it sees no change.

Note: only the **Lambda** path is plaintext. RunPod serves over `https://api.runpod.ai/...`, and the
hosted wiki backend uses the pod's HTTPS proxy URL, so neither is affected.

## Production posture (the real fix, tracked separately)

Specced in [`llm-endpoint-secure-transport.md`](llm-endpoint-secure-transport.md): until it lands, a
bare-VM endpoint can only be refused (the default) or served in cleartext (the opt-in), never secured.

- Front vLLM with TLS (a reverse proxy + a trusted cert), or
- Reach the instance over the provider's private networking / a VPC / an SSH tunnel, and bind vLLM to the
  private interface.

## Acceptance

- Default (no config): provision returns `ok=False`, the detail explains the cleartext refusal, and the
  instance is torn down (no billable VM left running).
- With `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` set: provision succeeds and the result detail + a log
  warning state the endpoint is plaintext.
- With `ANTHILL_REQUIRE_SECURE_LLM_ENDPOINT` set: refused + torn down, even if the opt-in is also set.
- Tests in `tests/test_lambda_provision.py`.
