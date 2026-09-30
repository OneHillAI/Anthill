"""The built-in PII scrubber (anthill/hybrid/scrub.py) redacts structured identifiers before text can
leave the perimeter (cloud escalation, training export, wiki-review flagging). These cover the hardened
patterns - JWT / IBAN / MAC - plus the common set and the no-false-positive guard. Model-free."""

from anthill.hybrid.scrub import restore, scrub


def test_scrubs_jwt_iban_and_mac():
    r = scrub(
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig12345 "
        "iban GB82 WEST 1234 5698 7654 32 mac 3c:22:fb:aa:bb:cc"
    )
    assert r.counts.get("JWT") == 1 and "eyJ" not in r.text
    assert r.counts.get("IBAN") == 1 and "GB82" not in r.text  # the WHOLE IBAN, incl. country code
    assert r.counts.get("MAC") == 1 and "3c:22" not in r.text


def test_scrubs_the_common_structured_pii():
    r = scrub(
        "mail a@b.com card 4111 1111 1111 1111 ssn 123-45-6789 "
        "phone 415-555-1234 key sk-abcdef012345ghijkl ip 10.1.2.3 url https://x.io/p"
    )
    for kind in ("EMAIL", "CARD", "SSN", "PHONE", "APIKEY", "IPV4", "URL"):
        assert r.counts.get(kind) == 1, kind


def test_no_false_positive_on_ordinary_numbers_and_dates():
    r = scrub("order 12345, invoice date 2026-07-11, quantity 42, app version 1.10.0")
    assert not r.had_pii  # short numbers, dates, and versions are not redacted


def test_restore_round_trips():
    r = scrub("reach me at alice@example.com")
    assert "[EMAIL_1]" in r.text
    assert restore(r.text, r.replacements) == "reach me at alice@example.com"
