"""Chat depth routing (#421): the system decides quick single-pass RAG vs the multi-step deep agent.
`looks_deep` is the deterministic multi-hop signal; `classify` also carries a model `depth`. Both
default to quick so simple questions stay fast."""

from anthill.agent import intent

# Clearly multi-hop / multi-source -> deep
DEEP = [
    "Compare Postgres and SQLite for our backend",
    "What's the difference between OKF and OKGF?",
    "pros and cons of local vs cloud models",
    "walk me through the setup step by step",
    "How does the cache affect latency?",
    "Summarize our notes and compare them to last week's",
    "give me the trade-offs across all our deployment options",
    "What is agent mode? And how do I enable it?",  # two questions in one
]

# Simple / single-source / factual -> quick (must NOT escalate)
QUICK = [
    "What is agent mode?",
    "summarize this document",
    "who wrote this wiki page?",
    "what time is the standup?",
    "define OKGF",
    "how do I turn on dark mode",
    "explain how caching works",
    "make me a one-pager",
]


def test_looks_deep_precision():
    for m in DEEP:
        assert intent.looks_deep(m) is True, m
    for m in QUICK:
        assert intent.looks_deep(m) is False, m


def test_classify_carries_depth(monkeypatch):
    # the model's depth is honoured...
    monkeypatch.setattr(
        intent, "json_chat", lambda b, m: '{"intent":"answer","depth":"deep","summary":"s"}'
    )
    assert intent.classify("tell me about x", None)["depth"] == "deep"
    monkeypatch.setattr(
        intent, "json_chat", lambda b, m: '{"intent":"answer","depth":"quick","summary":"s"}'
    )
    assert intent.classify("what is x", None)["depth"] == "quick"


def test_deterministic_deep_overrides_model_quick(monkeypatch):
    # even if the model says quick, a clear multi-hop question is deep (the deterministic signal wins).
    monkeypatch.setattr(
        intent, "json_chat", lambda b, m: '{"intent":"answer","depth":"quick","summary":"s"}'
    )
    assert intent.classify("compare x and y across our teams", None)["depth"] == "deep"


def test_missing_depth_defaults_quick(monkeypatch):
    # a model that omits depth (or a flaky call) defaults to quick - escalation is the exception.
    monkeypatch.setattr(intent, "json_chat", lambda b, m: '{"intent":"answer","summary":"s"}')
    assert intent.classify("what is x", None)["depth"] == "quick"


# ── redo_mode: the words that replace the retired re-run buttons (#421) ──────────

REDO_DEEP = [
    "go deeper",
    "dig deeper",
    "can you go deeper",
    "look into this more",
    "look into it further",
    "elaborate",
    "elaborate on that",
    "expand on this",
    "give me more detail",
    "in more depth please",
    "be more thorough",
]
REDO_WEB = [
    "check the web",
    "search the web",
    "look it up online",
    "search online",
    "can you google this",
    "web search this",
]
REDO_PROVIDER = [
    "use the cloud model",
    "use the org model",
    "use the connected backend",
    "try the provider",
    "try the connected backend",
    "escalate this",
    "escalate",
    "answer this with the provider",
    "answer with the cloud",
]
REDO_NONE = [
    "what is the refund policy?",  # a normal new question
    "make me a summary of this",  # a task, not a redo cue
    "thanks, that's helpful",  # acknowledgement
    "who wrote the deeper-learning wiki page",  # 'deeper' as a noun, not a cue
    "can you also add the regional totals to the table",  # a new instruction, no redo phrase
    "our cloud provider raised prices again this quarter",  # 'cloud'/'provider' as ordinary nouns
]


def test_redo_mode_deep_web_and_none():
    for m in REDO_DEEP:
        assert intent.redo_mode(m) == "deep", m
    for m in REDO_WEB:
        assert intent.redo_mode(m) == "web", m
    for m in REDO_PROVIDER:
        assert intent.redo_mode(m) == "provider", m
    for m in REDO_NONE:
        assert intent.redo_mode(m) == "", m


def test_redo_mode_web_wins_over_deep():
    # "search the web in more detail" is a web redo, not a deep one (web is checked first).
    assert intent.redo_mode("search the web in more detail") == "web"


def test_redo_mode_is_length_bounded():
    # a short "go deeper" is a redo cue; the same phrase buried in a long new question is not (a real
    # question that long is handled by looks_deep on its own merits, not treated as a re-run button).
    assert intent.redo_mode("go deeper") == "deep"
    long_q = (
        "go deeper " + "and also cover the migration, the rollback plan, and the on-call rota " * 3
    )
    assert len(long_q) > 120 and intent.redo_mode(long_q) == ""


# ── looks_uncertain: hedging WITHIN an answer (PR #661 Tier 2), distinct from looks_deep (reads the
# QUESTION) and from wiki.ask._NON_ANSWER (an outright refusal, not hedging in a substantive answer) ──

UNCERTAIN = [
    "I'm not entirely sure, but the timeout may be configurable.",
    "It's possible that the result is cached.",
    "It is unclear whether the setting applies globally.",
    "I think, however, the default is 30 seconds.",
    "I could be wrong about the default.",
    "This may vary between operating systems.",
    "Without more context, I cannot identify the failing service.",
    "Without additional details, the cause is hard to identify.",
    "It is hard to say for sure from this trace.",
    "I'm not entirely clear which version introduced it.",
]

# Confident, or merely using words ("may"/"possible"/"think"/"certain") that must NOT trigger this on
# their own - only the explicit hedging PHRASES above should.
CONFIDENT = [
    "",
    "The timeout defaults to 30 seconds.",
    "Run make build and then make test.",
    "The three planes are solo, org, and web.",
    "The result may contain several records.",
    "This function checks whether the value is present.",
    "Possible values are red, green, and blue.",
    "I think the timeout is configured in settings.py.",
    "I am fully certain this is not documented.",  # "certain" alone must not false-positive
]


def test_looks_uncertain_precision():
    for m in UNCERTAIN:
        assert intent.looks_uncertain(m) is True, m
    for m in CONFIDENT:
        assert intent.looks_uncertain(m) is False, m
