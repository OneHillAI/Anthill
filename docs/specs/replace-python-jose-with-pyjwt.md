# Spec: replace python-jose with PyJWT for session tokens

Status: implemented (PR 82)
Lane: `pillar:privacy`
Relates to: `anthill/web/crypto.py`, `anthill/web/app.py`, `pyproject.toml`, `requirements.lock`,
`.github/workflows/pr-validation.yml`, `.github/workflows/security-audit.yml`

## 1. Problem

Anthill signs login session tokens with `python-jose` 3.5.0. On 2026-10-06 the dependency audit started failing
on every pull request: CVE-2026-85394 says python-jose accepts an asymmetric public key as the secret of an HMAC
token, so whoever holds a service's public key can forge tokens when the caller does not restrict the
algorithms. No fixed version exists.

Anthill is not exposed. `python-jose` is used in one place (`anthill/web/crypto.py`), which signs and checks
HS256 tokens with a random symmetric secret and passes `algorithms=["HS256"]`. But a red audit on every PR stops
the team telling real findings from this one, and the library also pulls in `ecdsa`, which already needs a
documented exception (PYSEC-2026-1325). Living on exceptions is not a policy. PyJWT is already installed in the
packaged app (it comes in through the `mcp` extra), so using it adds no new package.

## 2. Requirements

R1. THE session token code SHALL use PyJWT. `python-jose`, `ecdsa` and `rsa` SHALL no longer be required by
`pyproject.toml` or listed in `requirements.lock`. (`pyasn1` left the lock with them at first, but `sigstore`,
which the packaged app needs for the signed model catalog, requires it, so the regenerated lock lists it again
for that reason. See `docs/specs/regenerate-requirements-lock.md`.)

R2. A token made by python-jose before this change SHALL still be accepted, so that nobody who is signed in is
signed out by the upgrade.

R3. THE token check SHALL accept HS256 only. A token whose header says `none`, another HMAC size, or an
asymmetric algorithm SHALL be rejected.

R4. An expired token, a token with a changed signature, a token made with another secret, and anything that is
not a token SHALL be rejected. A clock difference of up to 10 seconds SHALL be tolerated, because PyJWT rejects a
token issued in the future and python-jose did not.

R5. THE dependency audit SHALL pass without an exception for CVE-2026-85394.

## 3. Design

- `anthill/web/crypto.py`: `import jwt` (PyJWT) instead of `from jose import jwt`. `make_token` and
  `decode_token` keep their signatures and the claims they carry. `decode_token` passes `algorithms=["HS256"]`
  and `leeway=10`.
- `anthill/web/app.py`: `from jwt import PyJWTError as JWTError`, so the seven existing `except JWTError`
  blocks catch every PyJWT failure unchanged.
- `pyproject.toml`: `PyJWT>=2.8,<3` replaces `python-jose[cryptography]`.
- `requirements.lock`: the four packages only python-jose needed (`python-jose`, `ecdsa`, `rsa`, `pyasn1`) are removed; nothing else changes. `pyasn1` returns later through `sigstore`.

## 4. Acceptance

- `tests/test_session_token_pyjwt.py` covers R2 to R4, including a token made by python-jose 3.5.0 before it was
  removed, forged `none`, `HS512` and `RS256` headers, an expired token, a tampered token and clock drift.
- `tests/test_jwt_live_revocation.py` (the live session revocation tests) still passes.
- The whole suite passes and `pip-audit` reports no vulnerability beyond the ones already excepted.

## 5. Not in this change

- Regenerating `requirements.lock` from `pyproject.toml`. The committed lock differs from `pyproject.toml` in
  other ways (it lists `torch` and `sentence-transformers` and does not list `sigstore`). Regenerating it would
  change what the packaged app ships, so it needs its own change and its own frozen-build check. This change
  only removes the four packages python-jose needed (`pyasn1` returns through `sigstore`).
- Removing the `ecdsa` exception from the two audit workflows. It becomes unnecessary once this lands and is
  removed separately, because it edits `.github/`.
