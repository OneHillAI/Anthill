"""PII scrub is Presidio-augmented on the perimeter (cloud escalation / training export / wiki review),
with a graceful regex fallback (#255).

The regex baseline always runs (structured identifiers + app secrets). When the optional Microsoft
Presidio engine is installed, scrub() *adds* NER-detected PII a regex can't catch - names, locations,
government IDs. Without Presidio, the regex baseline is used alone, so the boundary always works. These
tests exercise both, using a fake analyzer to verify the augment logic without the heavy dependency.
"""

import importlib

import pytest

from anthill.hybrid.scrub import restore, scrub

# The `anthill.hybrid` package exports a `scrub` *function* that shadows the submodule name, so grab the
# module object explicitly for monkeypatching its internals.
scrub_mod = importlib.import_module("anthill.hybrid.scrub")

_FAKE_ENTS = [("Alice Smith", "PERSON"), ("Berlin", "LOCATION")]


# ── the regex baseline is always present (no Presidio needed) ─────────────────


def test_regex_baseline_catches_structured_pii():
    res = scrub("email me at ada@acme.com or card 4111 1111 1111 1111")
    assert res.had_pii
    assert "ada@acme.com" not in res.text and "[EMAIL_1]" in res.text
    assert "4111 1111 1111 1111" not in res.text
    assert (
        restore(res.text, res.replacements)
        == "email me at ada@acme.com or card 4111 1111 1111 1111"
    )


def test_scrub_never_crashes_without_presidio(monkeypatch):
    monkeypatch.setattr(scrub_mod, "_get_analyzer", lambda: None)  # simulate not-installed
    res = scrub("plain text with ssn 123-45-6789")
    assert "123-45-6789" not in res.text  # regex still fired
    assert isinstance(res.replacements, dict)


def test_presidio_available_returns_bool():
    assert isinstance(scrub_mod.presidio_available(), bool)  # never raises


def test_augment_is_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(scrub_mod, "_get_analyzer", lambda: None)
    res = scrub("Alice Smith went to Berlin")  # no structured PII, no Presidio
    assert res.text == "Alice Smith went to Berlin" and not res.had_pii


# ── the Presidio augmentation (verified with a fake analyzer) ─────────────────


class _FakeHit:
    def __init__(self, start, end, entity_type, score=0.95):
        self.start, self.end, self.entity_type, self.score = start, end, entity_type, score


class _FakeAnalyzer:
    """Stand-in for Presidio: flags a couple of known names/locations by string search."""

    def analyze(self, text, language, entities):
        hits = []
        for needle, kind in _FAKE_ENTS:
            if kind in entities:
                i = text.find(needle)
                if i >= 0:
                    hits.append(_FakeHit(i, i + len(needle), kind))
        return hits


def test_augment_redacts_names_and_locations(monkeypatch):
    monkeypatch.setattr(scrub_mod, "_get_analyzer", lambda: _FakeAnalyzer())
    original = "Email Alice Smith at ada@acme.com about Berlin"
    res = scrub(original)
    # regex caught the email; Presidio caught the name + location a regex can't
    assert "ada@acme.com" not in res.text
    assert "Alice Smith" not in res.text and "[PERSON_1]" in res.text
    assert "Berlin" not in res.text and "[LOCATION_1]" in res.text
    # and everything restores exactly (the map never leaves the machine)
    assert restore(res.text, res.replacements) == original


def test_augment_skips_low_confidence(monkeypatch):
    class _LowConf(_FakeAnalyzer):
        def analyze(self, text, language, entities):
            return [_FakeHit(0, len("Alice Smith"), "PERSON", score=0.2)]

    monkeypatch.setattr(scrub_mod, "_get_analyzer", lambda: _LowConf())
    res = scrub("Alice Smith is here")
    assert "Alice Smith" in res.text  # below the 0.5 threshold -> not redacted


def test_augment_does_not_touch_existing_placeholders(monkeypatch):
    # A hit that lands on the regex placeholder must be skipped (no double-redact, no corruption).
    class _HitsPlaceholder(_FakeAnalyzer):
        def analyze(self, text, language, entities):
            i = text.find("[EMAIL_1]")
            return [_FakeHit(i, i + len("[EMAIL_1]"), "PERSON")] if i >= 0 else []

    monkeypatch.setattr(scrub_mod, "_get_analyzer", lambda: _HitsPlaceholder())
    res = scrub("write to ada@acme.com")
    assert res.text.count("[EMAIL_1]") == 1 and "PERSON" not in res.text


# ── the real engine, if it happens to be installed (skipped in CI) ────────────


@pytest.mark.skipif(
    not scrub_mod.presidio_available(), reason="presidio + spaCy model not installed"
)
def test_real_presidio_detects_a_name():
    res = scrub("Please contact Barack Obama about the plan.")
    assert "Barack Obama" not in res.text
    assert restore(res.text, res.replacements) == "Please contact Barack Obama about the plan."
