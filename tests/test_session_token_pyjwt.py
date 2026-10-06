"""Session tokens after moving from python-jose to PyJWT (spec: replace-python-jose-with-pyjwt).

python-jose has an advisory with no fixed version, so the login token code uses PyJWT. Everyone who is signed in
holds a token made by the old library; it must still be accepted, and the algorithm must stay pinned. No network.
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from anthill.web import crypto

# Made with python-jose 3.5.0 before it was removed: HS256, sub=7, org=1, role=admin, exp in 2100.
LEGACY_SECRET = "compat-secret-0123456789abcdef0123456789abcdef"
LEGACY_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3Iiwib3JnIjoxLCJyb2xlIjoiYWRtaW4iLCJzaWQiOiJzaWQtYWJjIiwi"
    "dmVyIjowLCJvcmQiOjAsImRldiI6IiIsImlhdCI6MTc2MDAwMDAwMCwiZXhwIjo0MTAyNDQ0ODAwfQ."
    "-3RYGTMvivXm5o6jDtb2OaD1jOkCoTFtl_HLZjLeib8"
)


def _claims(**over):
    now = datetime.now(timezone.utc)
    base = {"sub": "7", "org": 1, "role": "admin", "iat": now, "exp": now + timedelta(hours=1)}
    base.update(over)
    return base


def test_a_token_made_by_python_jose_is_still_accepted(monkeypatch):
    monkeypatch.setattr(crypto, "_JWT_SECRET", LEGACY_SECRET)
    claims = crypto.decode_token(LEGACY_TOKEN)
    assert claims["sub"] == "7" and claims["org"] == 1 and claims["role"] == "admin"
    assert claims["sid"] == "sid-abc" and claims["iat"] == 1760000000


def test_a_new_token_round_trips_and_keeps_the_session_fields():
    token = crypto.make_token(
        5, 2, "member", session_id="s1", auth_version=3, session_order=4, device_id="d"
    )
    claims = crypto.decode_token(token)
    assert (claims["sub"], claims["org"], claims["role"]) == ("5", 2, "member")
    assert (claims["sid"], claims["ver"], claims["ord"], claims["dev"]) == ("s1", 3, 4, "d")
    assert isinstance(claims["iat"], int) and crypto.session_needs_renewal(claims) is False


def test_a_tampered_or_garbage_token_is_rejected():
    token = crypto.make_token(5, 2, "member")
    head, body, sig = token.split(".")
    for bad in (f"{head}.{body}.{sig[:-2]}xx", f"{head}.{body}", "not-a-token", "", "a.b.c"):
        with pytest.raises(jwt.PyJWTError):
            crypto.decode_token(bad)


def test_an_expired_token_is_rejected():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    token = jwt.encode(
        _claims(iat=past, exp=past + timedelta(hours=1)), crypto._JWT_SECRET, algorithm="HS256"
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        crypto.decode_token(token)


def _forge(alg, key=b""):
    """A token whose header names `alg`, signed by hand with HMAC-SHA256 (the classic confusion attack)."""

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    claims = {"sub": "7", "org": 1, "role": "admin", "exp": int(time.time()) + 3600}
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(key, head + b"." + body, hashlib.sha256).digest()) if key else b""
    return (head + b"." + body + b"." + sig).decode()


def test_only_hs256_is_accepted():
    # The advisory is about callers that do not restrict algorithms: none, another HMAC size, or an asymmetric
    # header with a symmetric signature.
    secret = crypto._JWT_SECRET.encode()
    for forged in (_forge("none"), _forge("RS256", secret), _forge("HS512", secret)):
        with pytest.raises(jwt.PyJWTError):
            crypto.decode_token(forged)
    assert crypto.decode_token(_forge("HS256", secret))["sub"] == "7"


def test_a_token_signed_with_another_secret_is_rejected():
    token = jwt.encode(
        _claims(), "another-secret-0123456789abcdef0123456789abcdef", algorithm="HS256"
    )
    with pytest.raises(jwt.InvalidSignatureError):
        crypto.decode_token(token)


def test_a_few_seconds_of_clock_drift_is_tolerated_but_not_minutes():
    soon = datetime.now(timezone.utc) + timedelta(seconds=5)
    ok = jwt.encode(_claims(iat=soon), crypto._JWT_SECRET, algorithm="HS256")
    assert crypto.decode_token(ok)["sub"] == "7"
    later = datetime.fromtimestamp(time.time() + 600, timezone.utc)
    bad = jwt.encode(_claims(iat=later), crypto._JWT_SECRET, algorithm="HS256")
    with pytest.raises(jwt.ImmatureSignatureError):
        crypto.decode_token(bad)
