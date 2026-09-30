from __future__ import annotations

import re
from dataclasses import dataclass, field

# Deterministic PII patterns. This is a redaction layer, not a guarantee - it
# catches the common structured identifiers before text leaves the perimeter.
# Free-text names are NOT reliably detectable by regex and are out of scope;
# the real privacy control is "don't send context" (escalate.py sends the
# question only by default).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),  # credit-card-ish
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"\b(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3,4}[ .-]?\d{3,4}\b")),
    ("APIKEY", re.compile(r"\b(?:sk|pk|xoxb|ghp|gho|AKIA)[-_A-Za-z0-9]{12,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{3,}")),
    # IBAN: country + 2 check digits, then either a contiguous BBAN or space-separated 4-char groups
    # (a real IBAN shape - so it can't greedily swallow following space-separated words).
    (
        "IBAN",
        re.compile(
            r"\b[A-Z]{2}\d{2}(?:[A-Za-z0-9]{11,30}|(?:\s[A-Za-z0-9]{4})+(?:\s[A-Za-z0-9]{1,3})?)\b"
        ),
    ),
    ("MAC", re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")),
    ("URL", re.compile(r"https?://[^\s)]+")),
]


@dataclass
class ScrubResult:
    text: str  # redacted text
    replacements: dict[str, str] = field(default_factory=dict)  # placeholder -> original
    counts: dict[str, int] = field(default_factory=dict)  # kind -> how many

    @property
    def had_pii(self) -> bool:
        return bool(self.replacements)


def _regex_scrub(text: str) -> ScrubResult:
    """Deterministic structured-PII redaction (the always-available baseline) - returns placeholders
    and a restore map.

    Order matters: APIKEY and EMAIL run before PHONE/CARD so their digits
    aren't partially eaten by the looser numeric patterns.
    """
    replacements: dict[str, str] = {}
    counts: dict[str, int] = {}
    out = text
    # Run the more specific patterns first. IBAN before CARD so the full IBAN (with its country code)
    # is redacted, not just its digit tail.
    order = ["APIKEY", "JWT", "IBAN", "MAC", "EMAIL", "URL", "SSN", "CARD", "IPV4", "PHONE"]
    by_name = dict(_PATTERNS)

    for kind in order:
        pat = by_name[kind]
        idx = 0

        def _repl(m: re.Match, kind=kind) -> str:
            nonlocal idx
            original = m.group(0)
            # Skip pure-short phone matches that are really small numbers.
            if kind == "PHONE" and len(re.sub(r"\D", "", original)) < 7:
                return original
            idx += 1
            ph = f"[{kind}_{idx}]"
            replacements[ph] = original
            return ph

        new_out, n = pat.subn(_repl, out)
        if n:
            counts[kind] = idx  # idx is the count of this kind's replacements
            out = new_out

    return ScrubResult(text=out, replacements=replacements, counts=counts)


# ── optional Microsoft Presidio (MIT) augmentation ───────────────────────────
# Presidio adds NER-detected PII a regex can't reliably catch - free-text names, locations, and
# government/medical/bank IDs (the regex baseline even documents names as out of scope). It + its spaCy
# model are a heavy OPTIONAL dependency (the `privacy` extra + `python -m spacy download en_core_web_lg`).
# When it isn't installed - or a model is missing - scrub() falls back to the regex baseline alone, so
# the privacy boundary always works. Presidio only ever ADDS redactions; it never weakens the regex pass.

_ANALYZER = None  # cached AnalyzerEngine (None once we've tried and it's unavailable)
_ANALYZER_TRIED = False

# Entities worth redacting on an egress boundary. Structured ones overlap the regex pass (already
# placeholdered by the time Presidio runs); the real gain is PERSON / LOCATION / NRP + government IDs.
_PRESIDIO_ENTITIES = [
    "PERSON",
    "LOCATION",
    "NRP",
    "US_SSN",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "US_BANK_NUMBER",
    "MEDICAL_LICENSE",
    "CRYPTO",
    "IBAN_CODE",
    "CREDIT_CARD",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IP_ADDRESS",
]
_PRESIDIO_MIN_SCORE = 0.5


def presidio_available() -> bool:
    """True if the optional Presidio engine (and its spaCy model) is usable. Cached; never raises."""
    return _get_analyzer() is not None


def reset_analyzer_cache() -> None:
    """Forget the cached Presidio probe so a freshly-installed privacy pack is picked up without a
    process restart (used by the one-click privacy-pack installer)."""
    global _ANALYZER, _ANALYZER_TRIED
    _ANALYZER, _ANALYZER_TRIED = None, False


def scrub_coverage() -> str:
    """One-line description of what scrub() redacts right now, for consent/settings surfaces so the
    structured-only default is never silent (issue #540)."""
    if presidio_available():
        return "structured identifiers (email, phone, SSN, cards, keys) plus names and locations"
    return (
        "structured identifiers only (email, phone, SSN, cards, keys); names and places are NOT "
        "redacted without the privacy pack"
    )


def _get_analyzer():
    """Lazily build a Presidio AnalyzerEngine, cached. Returns None if presidio (or its spaCy model)
    is not installed - callers then use the regex baseline. Never raises."""
    global _ANALYZER, _ANALYZER_TRIED
    if _ANALYZER_TRIED:
        return _ANALYZER
    _ANALYZER_TRIED = True
    try:
        from presidio_analyzer import AnalyzerEngine

        try:
            # Prefer the small spaCy model the privacy-pack installer fetches (~12 MB vs en_core_web_lg's
            # ~560 MB). Fall back to Presidio's default engine if a different model is already installed.
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            engine = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
                }
            ).create_engine()
            _ANALYZER = AnalyzerEngine(nlp_engine=engine)
        except Exception:
            _ANALYZER = (
                AnalyzerEngine()
            )  # default (en_core_web_lg) if the small model isn't present
    except Exception:
        _ANALYZER = None  # not installed / no spaCy model -> regex baseline only
    return _ANALYZER


def _presidio_augment(res: ScrubResult) -> ScrubResult:
    """Add NER-detected PII (names, locations, government IDs, ...) on top of the regex result, in
    place. No-op when Presidio is unavailable. Redacts right-to-left so offsets stay valid, skips
    anything already inside a placeholder, and never lets one span overlap another. Never raises."""
    analyzer = _get_analyzer()
    if analyzer is None:
        return res
    try:
        hits = analyzer.analyze(text=res.text, language="en", entities=_PRESIDIO_ENTITIES)
    except Exception:
        return res
    hits = sorted(
        (h for h in hits if h.score >= _PRESIDIO_MIN_SCORE),
        key=lambda h: h.start,
        reverse=True,
    )
    out = res.text
    last_start = len(out) + 1
    for h in hits:
        if h.end > last_start:  # overlaps a span already replaced this pass
            continue
        span = out[h.start : h.end]
        if not span.strip() or (span.startswith("[") and span.endswith("]")):
            continue  # empty or an existing placeholder
        kind = h.entity_type
        n = res.counts.get(kind, 0) + 1
        res.counts[kind] = n
        ph = f"[{kind}_{n}]"
        res.replacements[ph] = span
        out = out[: h.start] + ph + out[h.end :]
        last_start = h.start
    res.text = out
    return res


def scrub(text: str) -> ScrubResult:
    """Redact PII before text crosses a perimeter (cloud escalation, training export, wiki review).

    Runs the deterministic regex baseline first (structured identifiers + app secrets), then, when the
    optional Microsoft Presidio engine is installed, augments it with NER-detected PII a regex can't
    reliably catch - free-text names, locations, government/medical IDs. Without Presidio the regex
    baseline is used alone, so the boundary always works. Returns placeholders + a local restore map."""
    return _presidio_augment(_regex_scrub(text))


def restore(text: str, replacements: dict[str, str]) -> str:
    """Put original values back (best-effort) if a placeholder survived round-trip."""
    out = text
    for ph, original in replacements.items():
        out = out.replace(ph, original)
    return out
