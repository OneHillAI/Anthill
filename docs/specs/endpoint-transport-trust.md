# Spec: endpoint transport trust

Status: accepted (architecture; path B chosen). Lane: `pillar:privacy`.
Supersedes the "Production posture (the real fix, tracked separately)" section of
[`llm-endpoint-transport-posture.md`](llm-endpoint-transport-posture.md), which stays as the record of
the interim secure-by-default refusal (#615).

## Problem

For the VPC and self-hosted tiers, the locally installed app sends data across the internet to an org
endpoint (model serving, and later the org wiki backend). Three properties are required:

1. **Confidentiality + integrity**: nothing leaves in the clear, nothing can be altered in flight.
2. **Server authenticity**: the app is talking to *this org's* endpoint, not an impostor.
3. **Client authenticity**: the endpoint answers only to this org's app, not to anyone who found the IP.

Encryption alone does not deliver (1). TLS without verification is defeated by a man-in-the-middle who
simply terminates it, so **"HTTPS with `verify=False`" is worse than plaintext**: it looks secure and
is not. The hard part of this problem is not the cipher, it is **naming and trust** - how the client
knows which identity is legitimately "my endpoint".

## Root cause of the current gap

`OrgEndpoint` (`anthill/hosting/endpoint.py`) is:

```python
class OrgEndpoint:
    base_url: str      # the OpenAI-compatible base, including the /v1 suffix
    api_key: str = ""
    model: str = ""
```

That type happily holds `http://1.2.3.4:8000/v1`. Cleartext is **representable**, so only policy (a
flag, a warning, a review) prevents it - and policy erodes. The Lambda endpoint shipped cleartext by
default for exactly this reason until #615 made refusal the default. #615 closed the *silent* hole; it
did not make the insecure state impossible.

## Principle

**Make the insecure case unrepresentable.** An endpoint is not a URL. An endpoint is a URL **plus its
trust material**. If provisioning cannot produce trust material, it cannot return an endpoint. This
removes the class of bug rather than the instance.

## Design

### 1. The `Trust` type

```
Trust = PublicCA                          # a real DNS name; ordinary public-CA verification
      | Pinned(spki_sha256 | cert_pem)    # self-signed / private CA, verified against a pin
      | Tunnel                            # loopback or private address; the tunnel provides the crypto
```

`OrgEndpoint` gains a **required** `trust: Trust`. There is deliberately **no `None` / `Insecure`
variant**. Once this lands, `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` becomes vestigial and is retired.

### 2. One chokepoint

A single client factory in `hosting/endpoint.py` builds **every** outbound org client:

- rejects any scheme that is not `https`, except a loopback/private address whose trust is `Tunnel`;
- derives verification from `Trust` (system store / pin / none-needed-because-tunnel);
- has **no code path that accepts `verify=False`**;
- attaches the API key (and a client certificate where mTLS is used).

No call site constructs its own client. This mirrors the single-seam pattern already used for
`notify()` and `workspace_for()`: one place to audit, one place to test.

### 3. Path B - tunnel / private link (**the chosen default for VPC + self-hosted**)

- The serving process binds to the **private interface or loopback**. Nothing is published on a public
  IP, so the endpoint has **no public attack surface at all**.
- A WireGuard peer link (or an SSH tunnel, or the provider's private link) carries the traffic.
- WireGuard's peer keys give **encryption and mutual authentication in one mechanism**: no PKI, no DNS,
  no certificate lifecycle, and requirement (3) is satisfied by construction.
- The endpoint becomes a loopback/private address with `trust=Tunnel`.
- The serving port is firewalled to the tunnel only. Defence in depth: do not leave `:8000` on
  `0.0.0.0` because "it has a key".
- Prior art: `anthill/remote/tunnel.py` already manages a tunnel lifecycle (Cloudflare/manual) for
  *inbound* access. This is the same idea pointed outbound.

Chosen because it is the best fit for a sovereignty-first product: the strongest story is not "your
data is on the public internet but encrypted", it is "your model endpoint is not on the public internet
at all".

### 4. Path A - named endpoint + public CA (**client half ships now; provisioning is backlog**)

Path A has two separable halves, and this is load-bearing for sequencing:

- **(i) Client trust - ships with this design.** Trusting a normal public-CA HTTPS endpoint is
  ordinary verification and is implemented as the `PublicCA` variant from day one. An org that
  **already terminates TLS on its own domain/proxy** can point Anthill at it immediately. This is the
  "prepare for A" half: no redesign is needed later, only the provisioning half is added.
- **(ii) Provisioning automation - backlog.** Creating the DNS record per VM, running Caddy/Traefik in
  front of the serving process, obtaining and renewing a Let's Encrypt certificate. Deferred; tracked
  as a backlog item.

Note why A cannot be the *default*: a provisioned VM has a **bare public IP and no domain**, and public
CAs do not issue certificates for bare IPs. A requires the org to bring a domain.

### 5. Path C - self-signed + pinning (**backlog, fallback only**)

For a bare IP with no domain and no tunnel. Generate the keypair, inject at launch, serve with
`--ssl-certfile`, and verify against the **pin** (hostname check off, pin check on). Works without DNS,
but it is the most code and rotation is manual. Only if a real customer needs bare-IP.

### 6. Provisioning establishes trust

The root cause of the Lambda gap was that provisioning returned an IP and security was bolted on
afterwards. Invert it: `provision()` is the step that **creates** the trust material and returns an
endpoint that already carries it.

- **B**: generate the keypair, inject the peer config via cloud-init, return the client config and a
  `Tunnel` endpoint.
- **A(ii)**: create the DNS record, wait for the certificate, return `PublicCA`.
- **C**: generate the certificate locally, inject it, return `Pinned(...)`.

There is then never a moment at which an untrusted endpoint exists.

### 7. Mutual authentication

- **Server proves itself to the app**: the `Trust` above.
- **App proves itself to the server**: the API key is the floor (already present). Above it, mTLS with
  a client certificate (the repo already has the vocabulary: `certs/*.crt`,
  `scripts/gen-dev-certs.sh`), or the tunnel's peer keys. Path B gets this free.

### 8. Surface and continuous verification

- The validate/reachability check asserts scheme, that verification is on, and that the pin or tunnel
  matches. Its result is surfaced in Settings **next to the existing sovereignty label**
  (`hosting/tiers.py` green/amber): transport posture becomes part of that label rather than a
  footnote.
- An architecture test asserts that **no** code path can build a client with `verify=False` or an
  `http://` org endpoint.

## Acceptance

- `OrgEndpoint` cannot be constructed without a `Trust`; there is no insecure variant.
- Every outbound org call is built by the single client factory; `verify=False` is unreachable.
- An org that already runs its own HTTPS endpoint on a domain works today via `PublicCA` (path A(i)).
- A provisioned VPC / self-hosted backend reaches the app over a tunnel with `trust=Tunnel`, with the
  serving port firewalled to the tunnel, and no public listener (path B).
- `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` is retired.

## Sequencing

1. **`Trust` type + chokepoint + `PublicCA`** (small, structural, prevents recurrence, and prepares A).
2. **Path B**: tunnel provisioning + `Tunnel` trust. The default for VPC and self-hosted.
3. **Backlog**: path A(ii), DNS + Let's Encrypt automation.
4. **Backlog**: path C, self-signed + pinning.

## Live validation

None of this is provable without a real, paid VM run. The Lambda path already carries a "NEEDS LIVE
VALIDATION" note (whether the startup script runs and the server comes up is unconfirmed on real
hardware). Whichever shape ships needs one real end-to-end run before it can be claimed to work.
