"""Score an answer against its annotation, with no model: a required fact is present, a forbidden one is absent, and an answer that
should say "it is not in the knowledge base" does. Scoring by the same small model that answers would reward its own habits."""

from __future__ import annotations

import re

from .cases import Case

SCORING_VERSION = 1

_ABSTAIN = re.compile(
    r"(do(?:es)?n[o']?t know|do not know|don't have (?:any |that |this )?(?:information|answer)|no information|not (?:mentioned|covered|"
    r"included|provided|available|in (?:the|my|our) (?:wiki|knowledge|notes|documents|pages))|cannot find|can't find|couldn't find|"
    r"could not find|unable to (?:find|say|answer)|isn't (?:mentioned|covered|stated|specified)|is not (?:stated|specified)|"
    r"nothing (?:in|about|on)|no (?:record|page|mention)|not sure)",
    re.I,
)


# The dashes and spaces models like to use (non-breaking hyphen, en and em dash, narrow and no-break space), made plain.
_PLAIN = {0x2011: "-", 0x2013: "-", 0x2014: "-", 0x202F: " ", 0xA0: " "}


def norm(text: str) -> str:
    """Lowercase, one space between words, and the dashes and spaces models like to use made plain."""
    return re.sub(r"\s+", " ", text.lower().translate(_PLAIN)).strip()


def abstained(answer: str) -> bool:
    return bool(_ABSTAIN.search(answer or ""))


def score(case: Case, answer: str) -> dict:
    """{"passed", "abstained", "missing": groups not satisfied, "forbidden": phrases that appeared}."""
    text = norm(answer)
    forbidden = [s for s in case.must_not_include if norm(s) in text]
    missing = [g for g in case.must_include if not any(norm(a) in text for a in g)]
    said_unknown = abstained(answer)
    passed = not forbidden and not missing and (said_unknown if case.abstain else True)
    return {"passed": passed, "abstained": said_unknown, "missing": missing, "forbidden": forbidden}
