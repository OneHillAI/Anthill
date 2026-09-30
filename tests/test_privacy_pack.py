"""The optional privacy pack (Presidio + spaCy) that adds name/location redaction to the cloud PII
scrub, and the transparency that the base install is structured-only (issue #540)."""

import importlib
import subprocess
import sys

import anthill.hybrid.privacy_pack as pack

# anthill.hybrid re-exports the scrub() function, shadowing the submodule name, so import the module
# explicitly to reach presidio_available / reset_analyzer_cache / the cache globals.
scrub = importlib.import_module("anthill.hybrid.scrub")


def test_scrub_coverage_names_the_gap_when_presidio_absent(monkeypatch):
    monkeypatch.setattr(scrub, "presidio_available", lambda: False)
    cov = scrub.scrub_coverage()
    assert "structured identifiers" in cov and "NOT" in cov  # the gap is stated, not silent


def test_scrub_coverage_includes_names_when_present(monkeypatch):
    monkeypatch.setattr(scrub, "presidio_available", lambda: True)
    assert "names and locations" in scrub.scrub_coverage()


def test_reset_analyzer_cache_reprobes():
    scrub._ANALYZER, scrub._ANALYZER_TRIED = "sentinel", True
    scrub.reset_analyzer_cache()
    assert scrub._ANALYZER is None and scrub._ANALYZER_TRIED is False


def test_can_install_false_in_a_frozen_app(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pack.can_install() is False  # a packaged app can't pip-install into its bundle


def test_install_is_a_noop_when_it_cannot_install(monkeypatch):
    monkeypatch.setattr(pack, "can_install", lambda: False)
    monkeypatch.setattr(scrub, "presidio_available", lambda: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not shell out")),
    )
    assert pack.install() is False  # returns current state, never shells out


def test_install_runs_pip_then_spacy_and_reprobes(monkeypatch):
    monkeypatch.setattr(pack, "can_install", lambda: True)
    cmds = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: cmds.append(cmd))
    reset = {"n": 0}
    monkeypatch.setattr(
        scrub, "reset_analyzer_cache", lambda: reset.__setitem__("n", reset["n"] + 1)
    )
    monkeypatch.setattr(scrub, "presidio_available", lambda: True)  # pretend the install succeeded
    assert pack.install() is True
    assert reset["n"] == 1  # the cached "unavailable" probe was cleared
    assert any("pip" in c and any("presidio-analyzer" in x for x in c) for c in cmds)
    assert any("spacy" in c and "download" in c for c in cmds)
