"""A greeting or a thank-you is answered without looking anything up in the wiki
(docs/specs/greeting-skips-wiki-grounding.md).

Measured with bge-m3 on 25 of Anthill's own long pages: "hello" scored 0.46 against its best page, "hi there" 0.47,
"thanks!" 0.42, "good morning" 0.44 and "ok" 0.52, above the 0.35 floor, so every one of them was grounded in three
pages (860 to 1,035 tokens of reference text) before the model said hi. Model-free: the backends are fakes.
"""

from typing import ClassVar

import pytest

from anthill.agent.intent import is_small_talk
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace

# Every phrase the spec lists, in the forms a user types them (the spec's lists and the matcher stay in step).
_LISTED = [
    # greetings
    "hello",
    "hello there",
    "hello again",
    "hi",
    "hi there",
    "hi again",
    "hey",
    "hey there",
    "hiya",
    "howdy",
    "yo",
    "good morning",
    "good afternoon",
    "good evening",
    "good night",
    "good day",
    "hallo",
    "servus",
    "moin",
    "grüß dich",
    "Grüß dich",
    "grüss dich",
    "grüezi",
    "guten Morgen",
    "guten Tag",
    "guten Abend",
    "salut",
    "bonjour",
    "hola",
    "buenos días",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "buongiorno",
    "buona sera",
    # thanks
    "thanks",
    "thanks a lot",
    "thanks so much",
    "thanks again",
    "thank you",
    "thank you very much",
    "thank you so much",
    "thank you again",
    "many thanks",
    "thx",
    "ty",
    "cheers",
    "danke",
    "danke schön",
    "Danke schon",
    "danke sehr",
    "vielen Dank",
    "merci",
    "merci beaucoup",
    "gracias",
    "muchas gracias",
    "grazie",
    "grazie mille",
    # acknowledgements
    "ok",
    "okay",
    "k",
    "cool",
    "great",
    "nice",
    "perfect",
    "awesome",
    "alright",
    "got it",
    "understood",
    "sounds good",
    "will do",
    "yes",
    "no",
    "yep",
    "nope",
    "yeah",
    "sure",
    "fine",
    # farewells
    "bye",
    "goodbye",
    "see you",
    "see you later",
    "see you soon",
    "see ya",
    "tschüss",
    "tschuss",
    "adieu",
    "adiós",
    "adios",
    "arrivederci",
    "ciao",
    # check-ins
    "how are you",
    "how are you doing",
    "how are you today",
    "how is it going",
    "how's it going",
    "what's up",
    "wie geht's",
    "wie geht es dir",
    "comment ça va",
    "comment ca va",
    "ça va",
    "ca va",
    "cómo estás",
    "como estas",
]


@pytest.mark.parametrize("message", _LISTED)
def test_every_listed_phrase_is_small_talk(message):
    assert is_small_talk(message)
    assert is_small_talk(message.upper() + "!")  # capitals and a bang do not matter


@pytest.mark.parametrize(
    "message",
    [
        "Hello!",
        "thanks!",
        "Thank you",
        "How are you doing?",
        "¡hola!",
        "¿cómo estás?",
        "¡Gracias!",
        "hola, ¿cómo estás?",
        "GUTEN MORGEN",
        "Vielen Dank!",
        "Wie geht's?",
        "cool, thanks",
        "hi, how are you?",
        "ok, thanks, bye",
        "thanks and bye",
    ],
)
def test_a_bare_greeting_or_thanks_is_small_talk(message):
    assert is_small_talk(message)


def test_a_decomposed_unicode_form_is_small_talk_too():
    import unicodedata

    for phrase in ("Danke schön", "Grüß dich", "buenos días", "adiós", "comment ça va", "tschüss"):
        assert is_small_talk(unicodedata.normalize("NFD", phrase)), (
            phrase
        )  # "o" + combining diaeresis
        assert is_small_talk(unicodedata.normalize("NFC", phrase)), phrase


@pytest.mark.parametrize(
    "message",
    [
        # question words that are not a greeting
        "how much is it?",
        "how many are there?",
        "is it up?",
        "how are you different from the other assistants",
        "what is the update signing key rotation?",
        # single words that only belong to a greeting as part of a phrase
        "Morgen?",
        "morgen",
        "tag",
        "night",
        "schon",
        "welcome",
        "so",
        "there",
        "it",
        "you",
        "many",
        "up",
        # a digit or a symbol makes it a normal message
        "hello 123",
        "ok 404",
        "Is it 9.5?",
        "how much is 2+2?",
        "hello 😀",
        "thanks!!! 🙏",
        "hi @team",
        # an acknowledgement that asks something is a question, not a reply
        "ok?",
        "fine?",
        "Nice?",
        "yes?",
        "sure?",
        "no?",
        # a greeting that carries something else
        "hello, what is our refund policy?",
        "hi, can you summarise the Q3 report",
        "thanks for the report on churn",
        "ok so what now",
        "hello world program in python",
        "good morning team, here is the plan for the launch next week",
        "Say hello in one short sentence.",
        "yes or no: is Reykjavik the capital",
        "very good question about billing",
        "so many tasks overdue",
        # nothing, or too long, or too many phrases
        "",
        "   ",
        "hello " * 20,
        "hi hi hi hi",
    ],
)
def test_anything_more_than_small_talk_still_looks_things_up(message):
    assert not is_small_talk(message)


def _workspace(tmp_path, monkeypatch, name="w", page="Greetings"):
    from anthill.cache import embedder as emb

    monkeypatch.setattr(emb, "available", lambda: False)  # keyword retrieval, no embedding model
    ws = Workspace(tmp_path / name)
    ws.init()
    # a page that the keyword fallback WOULD pick for "hello" and "thanks": small talk must skip it
    ws.write_page(
        page,
        f"# {page}\n\nHello and thanks: how we greet customers. Invoices are due in 30 days.\n",
    )
    return ws


def _grounding_slugs(ws, message, **kwargs):
    seen = {}
    list(
        ask_mod.ask_stream(
            ws, message, _StreamBackend(), on_context=lambda slugs: seen.update(s=slugs), **kwargs
        )
    )
    return seen["s"]


def test_small_talk_retrieves_nothing_and_a_question_still_does(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    assert _grounding_slugs(ws, "hello") == []
    assert _grounding_slugs(ws, "thanks!") == []
    assert _grounding_slugs(ws, "when are invoices due?") == ["greetings"]
    # a greeting that carries a question is a question
    assert _grounding_slugs(ws, "hello, when are invoices due?") == ["greetings"]


class _StreamBackend:
    def __init__(self):
        self.prompt = ""

    def chat_stream(self, messages, **kwargs):
        self.prompt = "\n".join(m.content for m in messages)
        yield "Hi!"


class _Whole:
    def __init__(self):
        self.prompt = ""

    def chat(self, messages, **kwargs):
        self.prompt = "\n".join(m.content for m in messages)
        return "Hi!"


def test_a_streamed_greeting_has_no_reference_material_in_its_prompt(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    seen = {}
    be = _StreamBackend()
    out = list(ask_mod.ask_stream(ws, "thanks!", be, on_context=lambda slugs: seen.update(s=slugs)))
    assert "".join(out) == "Hi!"
    assert "REFERENCE MATERIAL" not in be.prompt and "Invoices are due" not in be.prompt
    assert seen["s"] == []  # nothing is reported as the grounding of a greeting

    be2 = _StreamBackend()
    list(ask_mod.ask_stream(ws, "when are invoices due?", be2))
    assert "REFERENCE MATERIAL" in be2.prompt and "Invoices are due in 30 days" in be2.prompt


def test_a_blocking_greeting_has_no_reference_material_either(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    be = _Whole()
    answer, slugs, _hit = ask_mod.ask(ws, "good morning", be)
    assert answer == "Hi!" and slugs == []
    assert "REFERENCE MATERIAL" not in be.prompt


def test_a_greeting_skips_every_workspace_not_just_the_first(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch, "personal", "Greetings")
    team = _workspace(tmp_path, monkeypatch, "team", "Team greetings")
    assert _grounding_slugs(ws, "thanks!", extra_workspaces=[team]) == []
    assert sorted(_grounding_slugs(ws, "when are invoices due?", extra_workspaces=[team])) == [
        "greetings",
        "team-greetings",
    ]
    be = _StreamBackend()
    list(ask_mod.ask_stream(ws, "thanks!", be, extra_workspaces=[team]))
    assert "REFERENCE MATERIAL" not in be.prompt
    be2 = _StreamBackend()
    list(ask_mod.ask_stream(ws, "when are invoices due?", be2, extra_workspaces=[team]))
    assert "Invoices are due in 30 days" in be2.prompt


def test_a_greeting_with_web_search_on_sends_no_wiki_context_to_the_web_answer(
    tmp_path, monkeypatch
):
    ws = _workspace(tmp_path, monkeypatch)
    seen = {}

    def fake_search_and_answer(question, *, backend, wiki_context="", **kw):
        seen["wiki_context"] = wiki_context
        return "Hi!"

    monkeypatch.setattr("anthill.search.web.search_and_answer", fake_search_and_answer)
    answer, slugs, _hit = ask_mod.ask(ws, "thanks!", _Whole(), web_search=True)
    assert answer == "Hi!" and slugs == []
    assert seen["wiki_context"] == ""  # the blocking web answer gets no unrelated pages either


class _RecordingCache:
    """Stands in for the semantic cache: records what is read and written, and holds one old entry."""

    lookups: ClassVar[list] = []
    stores: ClassVar[list] = []

    class _Hit:
        answer = "an answer cached before small talk was left alone"
        slugs: ClassVar[list] = ["old-unrelated-page"]

    def __init__(self, **kwargs):
        pass

    def lookup(self, q):
        type(self).lookups.append(q)
        return self._Hit()

    def store(self, q, a, slugs=None):
        type(self).stores.append(q)


def test_small_talk_neither_reads_nor_writes_the_semantic_cache(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    _RecordingCache.lookups, _RecordingCache.stores = [], []
    monkeypatch.setattr(ask_mod, "SemanticCache", _RecordingCache)

    answer, slugs, hit = ask_mod.ask(ws, "thanks!", _Whole())
    assert (answer, slugs, hit) == ("Hi!", [], False)  # the old cached entry is not replayed
    list(ask_mod.ask_stream(ws, "good morning", _StreamBackend()))
    assert _RecordingCache.lookups == [] and _RecordingCache.stores == []

    answer, slugs, hit = ask_mod.ask(ws, "when are invoices due?", _Whole())
    assert hit is True and slugs == ["old-unrelated-page"]  # a real question still uses the cache
    assert _RecordingCache.lookups == ["when are invoices due?"]


def test_the_chat_route_does_not_serve_a_cached_answer_to_small_talk(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    session = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    session.add(user)
    session.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    session.add(conv)
    session.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))

    _RecordingCache.lookups, _RecordingCache.stores = [], []
    monkeypatch.setattr("anthill.cache.SemanticCache", _RecordingCache)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["Hi!"]))

    def events(message):
        r = client.get(f"/chat/{conv.id}/stream", params={"message": message})
        return [
            json.loads(line[6:])
            for line in r.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]

    small = events("thanks!")
    assert [e["token"] for e in small if "token" in e] == [
        "Hi!"
    ]  # answered, not replayed from the cache
    assert not any(e.get("meta", {}).get("cache_hit") for e in small)
    assert _RecordingCache.lookups == []

    real = events("when are invoices due?")
    assert any(
        e.get("meta", {}).get("cache_hit") for e in real
    )  # a real question still hits the cache
    assert _RecordingCache.lookups == ["when are invoices due?"]


# ── acknowledgements answer the assistant's last turn ───────────────────────────────────────────────


def test_an_acknowledgement_after_a_question_needs_the_conversation_so_it_is_not_small_talk():
    asked = [("user", "refunds?"), ("assistant", "Shall I look into the refund policy?")]
    said = [("user", "refunds?"), ("assistant", "Refunds take 30 days.")]
    for word in ("yes", "no", "sure", "ok", "okay", "fine", "yep", "nope", "yeah", "ok, sure"):
        assert not is_small_talk(word, asked), word  # a reply to a question: retrieve as before
        assert is_small_talk(word, said), word  # a reply to a statement: nothing to look up
        assert is_small_talk(word), word  # no history at all
    # thanks and greetings do not depend on what was asked
    assert is_small_talk("thanks!", asked) and is_small_talk("how are you?", asked)
    # the last assistant turn decides, and trailing markup does not hide the question mark
    assert not is_small_talk("yes", [("assistant", "**Want more detail?**")])
    assert is_small_talk(
        "yes", [("assistant", "Want more detail?"), ("user", "x"), ("assistant", "Done.")]
    )


def test_a_streamed_acknowledgement_is_retrieved_only_when_it_answers_a_question(
    tmp_path, monkeypatch
):
    ws = _workspace(tmp_path, monkeypatch)
    retrieved: list = []
    real = ask_mod._merge_relevant
    monkeypatch.setattr(
        ask_mod, "_merge_relevant", lambda *a, **k: retrieved.append(1) or real(*a, **k)
    )

    asked = [("assistant", "Do you want the invoice terms?")]
    list(ask_mod.ask_stream(ws, "yes", _StreamBackend(), history=asked))
    assert retrieved == [1]  # "yes" answers a question, so the turn is retrieved as before

    retrieved.clear()
    list(ask_mod.ask_stream(ws, "ok", _StreamBackend(), history=[("assistant", "All set.")]))
    list(ask_mod.ask_stream(ws, "ok", _StreamBackend()))
    assert retrieved == []  # after a statement, or with no history, there is nothing to look up


_COMPOUND = [
    "yes thanks",
    "yes, thank you",
    "sure, thanks!",
    "ok thanks",
    "hi yes",
    "yes?!",
    "yes!?",
    "ok thanks?",
]


def test_an_acknowledgement_with_thanks_or_a_greeting_still_answers_the_question_before_it():
    asked = [("assistant", "Want me to search for the latest pricing?")]
    said = [("assistant", "Pricing starts at 10 euros.")]
    for message in _COMPOUND:
        assert not is_small_talk(message, asked), message
        assert not is_small_talk(message, [("assistant", "Anything else? ")]), message
    for message in _COMPOUND[:5]:
        assert is_small_talk(message, said), message  # after a statement there is nothing to answer
        assert is_small_talk(message), message  # and with no history there is nothing either
    # "yes?!" asks something whatever the history says
    assert not is_small_talk("yes?!", said) and not is_small_talk("yes?!")
    # thanks and greetings on their own never depend on what was asked
    assert is_small_talk("thanks a lot", asked) and is_small_talk("hi there", asked)


def test_the_question_is_read_from_the_final_sentence_without_the_go_deeper_suggestion():
    nudge = ask_mod._go_deeper_suggestion(False)
    both = ask_mod._go_deeper_suggestion(True)
    # the suggestion ends the answer but is not what the assistant asked
    assert not is_small_talk("yes", [("assistant", "Shall I search for pricing?" + nudge)])
    assert not is_small_talk("yes", [("assistant", "Shall I search for pricing?" + both)])
    assert is_small_talk("yes", [("assistant", "Pricing starts at 10 euros." + nudge)])
    assert is_small_talk("yes", [("assistant", "Done. Want a recap? No need." + nudge)])
    # a question earlier in the turn is answered or moot; only the last sentence counts
    assert not is_small_talk("yes", [("assistant", "Here you go. Shall I search?!")])
    assert not is_small_talk("yes", [("assistant", "**Want more detail?**\n")])
    assert not is_small_talk("sure", [("assistant", "Options:\n- A\n- B\nWhich one do you want?")])


def test_an_acknowledgement_after_a_question_neither_reads_nor_writes_a_cache(
    tmp_path, monkeypatch
):
    import numpy as np

    from anthill.cache import embedder as emb

    ws = _workspace(tmp_path, monkeypatch)
    asked_org: list = []
    published: list = []
    monkeypatch.setattr(emb, "safe_embed", lambda text: np.ones(1024, dtype=np.float32))
    monkeypatch.setattr(ask_mod, "_merge_relevant", lambda *a, **k: [])
    monkeypatch.setattr(ask_mod, "_central_lookup", lambda *a, **k: asked_org.append(1) or None)
    monkeypatch.setattr(ask_mod, "_central_publish", lambda *a, **k: published.append(1))
    _RecordingCache.lookups, _RecordingCache.stores = [], []
    monkeypatch.setattr(ask_mod, "SemanticCache", _RecordingCache)
    asked = [("assistant", "Want me to search for the latest pricing?")]

    for message in ("yes", "yes thanks"):
        answer, _slugs, hit = ask_mod.ask(
            ws, message, _Whole(), history=asked, org_url="http://org.invalid"
        )
        assert (answer, hit) == ("Hi!", False)  # the old "yes" entry is not replayed
        list(ask_mod.ask_stream(ws, message, _StreamBackend(), history=asked))
    assert _RecordingCache.lookups == [] and _RecordingCache.stores == []
    assert asked_org == [] and published == []

    # a real question still reads the cache
    ask_mod.ask(ws, "when are invoices due?", _Whole(), org_url="http://org.invalid")
    assert _RecordingCache.lookups == ["when are invoices due?"]


def test_the_chat_route_does_not_read_the_cache_for_an_acknowledgement_that_answers_a_question():
    from anthill.agent.intent import is_acknowledgement_reply

    asked = [("assistant", "Want me to search?")]
    said = [("assistant", "All set.")]
    for message in ("yes", "yes thanks", "ok, sure!", "hi yes", "ok?"):
        assert is_acknowledgement_reply(message, asked), message
    for message in ("yes", "yes thanks", "thanks!", "hello"):
        assert not is_acknowledgement_reply(message, said), message  # small talk instead
    assert not is_acknowledgement_reply("thanks!", asked)  # no acknowledgement in it
    assert not is_acknowledgement_reply("when are invoices due?", asked)


# ── no planner call, no search, no embedding for small talk ─────────────────────────────────────────


def test_decide_web_does_not_plan_or_search_for_small_talk(monkeypatch):
    from anthill.agent import intent

    def boom(*a, **k):
        raise AssertionError("the planner must not be called for small talk")

    monkeypatch.setattr(intent, "plan_web_query", boom)
    monkeypatch.setattr(intent, "model_can_plan", lambda *a, **k: True)
    for msg in ("thanks!", "hello", "good morning", "how are you?", "ok"):
        assert intent.decide_web(msg, [], object(), "qwen3.5:9b", web_on=True) == (False, msg)
    # a real question still reaches the planner
    monkeypatch.setattr(intent, "plan_web_query", lambda m, h, b: (True, "planned"))
    assert intent.decide_web("population of Reykjavik?", [], object(), "m", web_on=True) == (
        True,
        "planned",
    )
    # and an acknowledgement after a question is not skipped
    asked = [("assistant", "Want me to search?")]
    assert intent.decide_web("yes", asked, object(), "m", web_on=True) == (True, "planned")
    asked_pricing = [("assistant", "Want me to search for the latest pricing?")]
    for msg in ("yes thanks", "yes, thank you", "sure, thanks!", "ok thanks", "hi yes", "yes?!"):
        assert intent.decide_web(msg, asked_pricing, object(), "m", web_on=True) == (
            True,
            "planned",
        ), msg


def test_small_talk_embeds_nothing_and_asks_neither_cache_nor_org_index(tmp_path, monkeypatch):
    import numpy as np

    from anthill.cache import embedder as emb

    ws = _workspace(tmp_path, monkeypatch)
    embedded: list = []
    asked_org: list = []
    published: list = []

    def embed(text):
        embedded.append(text)
        # a real vector, so only the small-talk check can skip the org lookup
        return np.ones(1024, dtype=np.float32)

    monkeypatch.setattr(emb, "safe_embed", embed)
    monkeypatch.setattr(ask_mod, "_merge_relevant", lambda *a, **k: [])
    monkeypatch.setattr(
        ask_mod, "_central_lookup", lambda *a, **k: asked_org.append(1) or None, raising=True
    )
    monkeypatch.setattr(ask_mod, "_central_publish", lambda *a, **k: published.append(1))
    ask_mod.ask(ws, "thanks!", _Whole(), org_url="http://org.invalid")
    list(ask_mod.ask_stream(ws, "good morning", _StreamBackend()))
    assert (
        embedded == [] and asked_org == [] and published == []
    )  # no embedding, no org lookup, no publish

    ask_mod.ask(ws, "when are invoices due?", _Whole(), org_url="http://org.invalid")
    assert embedded and set(embedded) == {"when are invoices due?"}  # a question is still embedded
    assert asked_org == [1] and published == [
        1
    ]  # and still asked of, and published to, the org index
