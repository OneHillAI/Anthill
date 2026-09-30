"""AWS GPU backend: credential validation + secret handling (mocked, no boto3, no network)."""

import base64

import pytest

from anthill.cloud import aws


class _Cfg:
    """Stand-in for OrgSettings with just the AWS fields validate() reads."""

    def __init__(self, **kw):
        self.aws_region = kw.get("region", "us-east-1")
        self.aws_access_key_id = kw.get("key", "AKIAEXAMPLE")
        self.aws_secret_access_key_enc = kw.get("secret_enc", "")


@pytest.fixture(autouse=True)
def _enc_key(monkeypatch):
    # Deterministic 32-byte key so encrypt/decrypt round-trips in-process.
    monkeypatch.setenv("ANTHILL_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode())


def _enc(plaintext: str) -> str:
    from anthill.web import crypto

    return crypto.encrypt(plaintext)


def test_validate_requires_boto3(monkeypatch):
    monkeypatch.setattr(aws, "boto3", None)
    ok, detail = aws.validate(_Cfg(secret_enc=_enc("shh")))
    assert ok is False and "boto3" in detail


def test_validate_missing_region(monkeypatch):
    monkeypatch.setattr(aws, "boto3", object())  # present but unused on this path
    ok, detail = aws.validate(_Cfg(region="", secret_enc=_enc("shh")))
    assert ok is False and "region" in detail.lower()


def test_validate_missing_secret(monkeypatch):
    monkeypatch.setattr(aws, "boto3", object())
    ok, detail = aws.validate(_Cfg(secret_enc=""))  # no secret stored
    assert ok is False and "secret" in detail.lower()


def test_validate_success(monkeypatch):
    captured = {}

    class _Sts:
        def get_caller_identity(self):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/anthill"}

    class _Boto:
        @staticmethod
        def client(service, **kw):
            captured["service"] = service
            captured["kw"] = kw
            return _Sts()

    monkeypatch.setattr(aws, "boto3", _Boto)
    ok, detail = aws.validate(_Cfg(secret_enc=_enc("shh")))
    assert ok is True
    assert "123456789012" in detail and "us-east-1" in detail
    assert captured["service"] == "sts"
    # the decrypted secret is passed to the client, never the ciphertext
    assert captured["kw"]["aws_secret_access_key"] == "shh"
    assert captured["kw"]["region_name"] == "us-east-1"


def test_validate_rejects_bad_credentials(monkeypatch):
    class _BadException(Exception):
        pass

    class _Sts:
        def get_caller_identity(self):
            raise _BadException("InvalidClientTokenId")

    class _Boto:
        @staticmethod
        def client(service, **kw):
            return _Sts()

    monkeypatch.setattr(aws, "boto3", _Boto)
    monkeypatch.setattr(aws, "_AWS_ERRORS", (_BadException,))
    ok, detail = aws.validate(_Cfg(secret_enc=_enc("wrong")))
    assert ok is False and "rejected" in detail.lower()


def test_provision_refuses_when_not_configured():
    # The guardrails must refuse (never launch) when AWS isn't configured / boto3 is absent.
    # The "remote training is the next build" path (with a launch + guaranteed teardown) is
    # covered in test_aws_provision.py.
    with pytest.raises(RuntimeError):
        aws.provision_and_train(_Cfg(), "/tmp/gold.jsonl")
