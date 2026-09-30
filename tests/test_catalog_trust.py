"""Authenticity + freshness of the published model catalog.

Signing is what makes hijacking anthill.run / the CDN / DNS insufficient: the catalog must be provably
published by our workflow. These cover the pure policy (who must have signed, rollback ordering) and the
fail-closed contract, without reaching the network.
"""

from anthill.hosting import catalog_trust as ct

# --- who must have signed ---------------------------------------------------------------------------


def test_default_signer_is_our_publishing_workflow_pinned_to_main():
    identity, issuer = ct.expected_signer()
    # keyless: the credential is the workflow's identity, not a key we hold
    assert identity.startswith("https://github.com/OneHillAI/Anthill/.github/workflows/")
    assert identity.endswith("@refs/heads/main")  # a fork/branch signature must not satisfy it
    assert issuer == "https://token.actions.githubusercontent.com"


def test_a_mirror_can_pin_its_own_signer(monkeypatch):
    monkeypatch.setenv("ANTHILL_MODEL_CATALOG_IDENTITY", "https://gitlab.example/x@refs/heads/main")
    monkeypatch.setenv("ANTHILL_MODEL_CATALOG_ISSUER", "https://gitlab.example")
    assert ct.expected_signer() == (
        "https://gitlab.example/x@refs/heads/main",
        "https://gitlab.example",
    )


def test_blanking_the_identity_does_not_disable_the_check(monkeypatch):
    """There is deliberately no 'skip verification' switch - blanking it must refuse, not wave through."""
    monkeypatch.setenv("ANTHILL_MODEL_CATALOG_IDENTITY", "   ")
    monkeypatch.setenv("ANTHILL_MODEL_CATALOG_ISSUER", "   ")
    # blank falls back to the pinned default rather than becoming "trust anything"
    identity, issuer = ct.expected_signer()
    assert identity == ct.DEFAULT_IDENTITY and issuer == ct.DEFAULT_ISSUER


# --- fail closed ------------------------------------------------------------------------------------


def test_unreadable_signature_is_refused_not_ignored():
    reason = ct.verify_catalog(b'{"models":[]}', "not a bundle")
    assert reason and "unreadable" in reason


def test_verify_never_raises_so_the_caller_always_fails_closed():
    for payload, bundle in [(b"", ""), (b"x", "{}"), (b"x", '{"garbage":1}')]:
        assert isinstance(ct.verify_catalog(payload, bundle), str)  # a reason, never an exception


# --- rollback ---------------------------------------------------------------------------------------


def test_rollback_refuses_an_older_but_validly_signed_catalog():
    # a signature proves WHO published it, not WHICH one: replaying yesterday's is still a valid signature
    assert ct.is_rollback("2026-07-01", "2026-07-17") is True


def test_rollback_allows_newer_and_same_day():
    assert ct.is_rollback("2026-07-18", "2026-07-17") is False
    assert ct.is_rollback("2026-07-17", "2026-07-17") is False  # unchanged catalog re-accepted


def test_rollback_allows_anything_when_nothing_is_trusted_yet():
    assert ct.is_rollback("2026-07-17", "") is False
    assert ct.is_rollback("", "") is False


def test_stripping_the_date_counts_as_a_rollback():
    """Dropping `generated` is exactly how you would dodge the freshness check."""
    assert ct.is_rollback("", "2026-07-17") is True
