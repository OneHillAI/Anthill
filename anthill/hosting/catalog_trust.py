"""Authenticity + freshness checks for the published model catalog.

The catalog tells every client which models to download and load, so whoever controls it has a path into
customer infrastructure (see docs/specs/model-catalog-trust.md). Shape-gating the entries narrows what a
tampered catalog can say; it cannot tell us WHO said it. That is this module.

**Keyless signing, so there is no key to guard.** The catalog is signed with Sigstore: the publishing
workflow proves its *identity* through GitHub's OIDC, Fulcio issues it a short-lived certificate, and the
signing event is recorded in Rekor's public append-only transparency log. Verification therefore asks "was
this signed by our publishing workflow?" rather than "does this match a key we hold" - there is no
long-lived private key anywhere to steal, rotate, or leak, and a forged signature is publicly detectable
after the fact. This is what makes hijacking anthill.run, the CDN, or DNS insufficient on its own.

Trust bottoms out at: GitHub's OIDC + Fulcio + Rekor, and whoever can run that workflow on `main` (hence
branch protection on `main` is load-bearing, not cosmetic).
"""

from __future__ import annotations

import os

# The publishing workflow's identity IS the credential. Pinned to refs/heads/main so a signature produced
# from a fork, a topic branch, or a dispatch on another ref does not satisfy it.
DEFAULT_IDENTITY = (
    "https://github.com/OneHillAI/Anthill/.github/workflows/"
    "refresh-model-catalog.yml@refs/heads/main"
)
DEFAULT_ISSUER = "https://token.actions.githubusercontent.com"


def expected_signer() -> tuple[str, str]:
    """Who must have signed the catalog.

    Overridable so a self-hosted mirror can pin its OWN signer - deliberately NOT a way to disable the
    check: there is no "skip verification" switch, because that is the switch that gets turned on during an
    incident and left on. A mirror signs with its own OIDC identity and pins it here.

    Unset OR blank both fall back to the pinned default (one rule, no gap): emptying the variable is a
    misconfiguration, not a way to opt out of verification.
    """
    identity = (os.environ.get("ANTHILL_MODEL_CATALOG_IDENTITY") or "").strip() or DEFAULT_IDENTITY
    issuer = (os.environ.get("ANTHILL_MODEL_CATALOG_ISSUER") or "").strip() or DEFAULT_ISSUER
    return identity, issuer


def verify_catalog(payload: bytes, bundle_json: str) -> str:
    """Verify ``payload`` was signed by the expected workflow identity.

    Returns "" when it verifies, else a short human-readable reason. Never raises: the caller refuses the
    refresh on any non-empty reason, so every unexpected failure - a missing dependency, an unreadable
    bundle, a network hiccup inside the verifier - fails CLOSED and keeps the catalog already in place.
    """
    identity, issuer = expected_signer()
    if not identity or not issuer:
        return "no expected signer is configured"
    try:
        from sigstore.models import Bundle
        from sigstore.verify import Verifier, policy
    except Exception as e:  # absent from a frozen build -> refuse rather than silently trust
        return f"signature verification is unavailable ({e})"
    try:
        bundle = Bundle.from_json(bundle_json)
    except Exception as e:
        return f"the signature is unreadable ({e})"
    try:
        Verifier.production().verify_artifact(
            payload, bundle, policy.Identity(identity=identity, issuer=issuer)
        )
    except Exception as e:
        return f"the signature does not verify as the expected publisher: {e}"
    return ""


def is_rollback(new_generated: str, current_generated: str) -> bool:
    """Whether ``new_generated`` is older than the catalog we already trust.

    A signature proves WHO published a catalog, not WHICH ONE. Whoever controls the host can replay an
    older, still-validly-signed catalog to re-introduce a model that has since been dropped - a rollback
    attack, which signing alone does not address. ``generated`` is ISO (YYYY-MM-DD), so a string compare
    orders it.

    A *missing* date on the incoming catalog counts as a rollback whenever we already trust one: stripping
    the field is precisely how you would dodge this check.
    """
    current = (current_generated or "").strip()
    if not current:
        return False  # nothing trusted yet - any catalog is an advance
    incoming = (new_generated or "").strip()
    if not incoming:
        return True
    return incoming < current
