# Spec: SSRF DNS-rebinding pin

Status: accepted
Lane: `pillar:privacy`
Relates to: the codebase security review (H6; PR #432 acknowledged rebinding as a residual).

## Thesis

The direct web fetch guarded against SSRF by resolving the target host and refusing any private/loopback/
link-local/reserved address (blocking e.g. `169.254.169.254`). But it then let `httpx` **re-resolve** the
hostname for the actual connection. Between the check and the connect the DNS answer can change: a low-TTL
attacker returns a public IP to the guard and an internal one to the connect (**DNS rebinding**), pivoting
a prompt-injected `fetch_url` to instance metadata or localhost.

## Requirement

- WHEN the agent fetches a URL directly, the system MUST resolve + validate the host **once** and connect
  to that **pinned** IP; it MUST NOT re-resolve the hostname for the connection.
- The pinned connection MUST still perform normal TLS: SNI + certificate verification against the real
  hostname (not the IP), so security is added without weakening cert checking.
- Each redirect hop MUST be revalidated and pinned the same way.
- All resolved addresses must be public; a mixed public/private answer MUST be refused.

## Design

- `_resolve_pinned(host)` resolves once, requires every address public, returns one IP to pin.
- `_pinned_client(ip, timeout)` builds an `httpx` client with a custom `httpcore` network backend whose
  `connect_tcp` connects to the pinned IP while httpcore keeps the origin host for TLS SNI + verify. (A
  naive alternative - rewriting the URL to the IP + setting `sni_hostname` - was rejected: it was shown to
  weaken cert verification.)
- `_safe_get` uses both, per hop.

## Acceptance

- A host resolving to a private/metadata IP is refused; a mixed public+private answer is refused.
- A legit HTTPS fetch still connects to the validated IP AND verifies the cert against the hostname
  (a wrong-host cert is rejected).
- `_safe_get` connects to the IP from the single validation resolve, never a fresh re-resolution.
- Regression tests in `tests/test_security_fixes.py`.
