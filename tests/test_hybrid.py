"""Model-free / network-free tests for the hybrid cloud module."""

from anthill.hybrid.escalate import HybridPolicy, maybe_escalate
from anthill.hybrid.providers import PROVIDERS
from anthill.hybrid.quality import assess
from anthill.hybrid.scrub import restore, scrub

# ── quality gate ──────────────────────────────────────────────────────────────


def test_assess_strong_answer_is_sufficient():
    v = assess(
        "We chose PostgreSQL for the billing database because of its stronger "
        "ACID guarantees and the team's existing familiarity with its tooling.",
        "which database did we pick for billing and why?",
        threshold=0.5,
    )
    assert v.sufficient
    assert v.confidence >= 0.5


def test_assess_hedging_answer_is_insufficient():
    v = assess(
        "I'm not sure - the wiki doesn't cover which database was chosen for billing.",
        "which database did we pick for billing and why?",
        threshold=0.5,
    )
    assert not v.sufficient
    assert v.confidence < 0.5
    assert any("hedg" in r for r in v.reasons)


def test_assess_empty_answer():
    v = assess("", "anything", threshold=0.5)
    assert v.confidence == 0.0 and not v.sufficient


def test_assess_threshold_is_tunable():
    ans = "Postgres, mainly for ACID guarantees."  # short but on-topic
    low = assess(ans, "which database and why?", threshold=0.3)
    high = assess(ans, "which database and why?", threshold=0.95)
    assert low.sufficient and not high.sufficient


# ── PII scrub ─────────────────────────────────────────────────────────────────


def test_scrub_redacts_email_and_restores():
    sr = scrub("Contact jane.doe@acme.com about the invoice.")
    assert "jane.doe@acme.com" not in sr.text
    assert sr.had_pii
    assert restore(sr.text, sr.replacements) == "Contact jane.doe@acme.com about the invoice."


def test_scrub_redacts_multiple_kinds():
    sr = scrub("Email a@b.com, call +1 415 555 1234, key sk-abcdef0123456789.")
    assert "a@b.com" not in sr.text
    assert "sk-abcdef0123456789" not in sr.text
    assert sr.had_pii


def test_scrub_clean_text_untouched():
    sr = scrub("Which database did we pick for billing?")
    assert not sr.had_pii
    assert sr.text == "Which database did we pick for billing?"


# ── escalation policy ─────────────────────────────────────────────────────────


def test_no_escalation_when_disabled():
    policy = HybridPolicy(enabled=False)
    out = maybe_escalate("q", "I don't know.", policy=policy)
    assert not out.escalated
    assert out.answer == "I don't know."
    assert out.reason == "hybrid disabled"


def test_no_escalation_when_local_sufficient():
    policy = HybridPolicy(enabled=True, provider="openrouter")
    strong = (
        "PostgreSQL was selected for billing due to stronger ACID guarantees "
        "and the team's familiarity with its tooling ecosystem."
    )
    out = maybe_escalate("which database for billing and why?", strong, policy=policy)
    assert not out.escalated
    assert out.reason == "local answer sufficient"


def test_escalation_blocked_without_api_key(monkeypatch):
    # Enabled + weak answer, but no API key configured → stays local, explains why.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    policy = HybridPolicy(enabled=True, provider="openrouter", api_key="")
    out = maybe_escalate(
        "obscure question the wiki can't answer",
        "I'm not sure, the wiki doesn't cover this.",
        policy=policy,
    )
    assert not out.escalated
    assert "no API key" in out.reason


def test_escalation_blocked_unknown_provider():
    policy = HybridPolicy(enabled=True, provider="does-not-exist")
    out = maybe_escalate("q", "I don't know.", policy=policy)
    assert not out.escalated
    assert "unknown provider" in out.reason


def test_budget_cap_blocks_escalation(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    policy = HybridPolicy(enabled=True, provider="openrouter", monthly_budget_usd=10.0)
    out = maybe_escalate(
        "obscure question",
        "I don't know, the wiki doesn't cover this.",
        policy=policy,
        spent_this_month=10.0,
    )
    assert not out.escalated
    assert "budget" in out.reason


# ── provider registry ─────────────────────────────────────────────────────────


def test_providers_are_openai_compatible_urls():
    for p in PROVIDERS.values():
        assert p.base_url.startswith("https://")
        # env var is an uppercase identifier (e.g. OPENROUTER_API_KEY, HF_TOKEN)
        assert p.api_key_env.isupper() and p.api_key_env.replace("_", "").isalnum()
        assert p.default_model
        assert p.retention  # every provider must state its data-retention posture
