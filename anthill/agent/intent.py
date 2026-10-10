"""Classify a chat message so one chat box can route it.

    answer    a question / request for information -> the normal Q&A path
    do        make or produce something now (a doc, spreadsheet, deck, chart, file) or
              take a concrete one-off action -> run the agent, produce the artifact
    schedule  recurring or time-deferred work ("every morning", "at 9am") -> a task

`looks_actionable()` is a cheap regex pre-filter so a plain question never pays the
classifier's model call. `classify()` is the model-backed decision (json_chat), with a
safe fallback to "answer" so a question is never blocked by a flaky model.
"""

from __future__ import annotations

import re

from ..common.jsonchat import extract_json, json_chat
from ..inference.base import Message

# Cheap signals that a message might be more than a question. Tuned to over-trigger a
# little (the classifier is the real decision); a pure question matches none of these.
_ACTION = re.compile(
    r"\b(make|create|build|generate|draft|produce|export|compile|write (me|a|an|the)|"
    r"turn (this|it|that) into|save (this|it|that) as)\b",
    re.I,
)
_ARTIFACT = re.compile(
    r"\b(doc|document|file|pdf|word|docx|spreadsheet|excel|xlsx|csv|presentation|"
    r"powerpoint|pptx|slide|slides|deck|report|chart|"
    # Deliverable nouns that name a written output even with no format word ("a one-page summary of
    # X"): these should reach the classifier as a candidate 'do', not be answered inline. The noun
    # `summary` does not match the verb `summarize`, so "summarize the meeting" stays a plain answer.
    r"summary|one[- ]?pager|one[- ]?page|briefing|memo|write[- ]?up|white[- ]?paper|"
    r"fact[- ]?sheet|handout)\b",
    re.I,
)
_SCHEDULE = re.compile(
    r"\b(every|each|daily|weekly|hourly|nightly|recurring|remind me|schedule|"
    r"at \d{1,2}(:\d{2})?\s*(am|pm)?)\b",
    re.I,
)
# An EXPLICIT deep-research ask: the user wants a multi-source, cited report - the Deep-Research flow,
# not a quick answer. Kept narrow so a plain "what is X, cite sources" stays a normal cited answer
# (that path already fetches + cites); only clear "do deep research / write a comprehensive report"
# language routes to the heavier research mode.
_RESEARCH = re.compile(
    r"\b("
    r"deep[- ]?dive|deep research|"
    r"in[- ]?depth (research|report|analysis|look|study)|"
    r"comprehensive (report|overview|analysis|review|summary)|"
    r"thorough(ly)? (research|investigate|analy[sz]e)|"
    r"(do|run|conduct|carry out) (some |a )?(deep |thorough )?research (on|about|into)|"
    r"research and (write|compile|summari[sz]e|produce|draft)|"
    r"(write|compile|put together|produce) (me )?(a |an )?(cited |sourced |detailed )?"
    r"(research )?(report|briefing|brief|overview|dossier) (on|about)|"
    r"literature review|research memo (on|about)"
    r")\b",
    re.I,
)

# A plain question that clearly needs *live* information (so the web is worth adding on top of
# the wiki, automatically - P4). Kept precise so internal questions don't needlessly hit the web.
_NEEDS_WEB = re.compile(
    r"\b(latest|current(ly)?|today'?s?|tonight|right now|this (week|month|year)|"
    r"recent(ly)?|news|headlines?|weather|forecast|(stock|share) price|price of|"
    r"exchange rate|who won|release date|just released|trending|up[- ]to[- ]date|"
    r"as of (today|now))\b",
    re.I,
)

# A research/citation ask: the user wants an answer grounded in real sources, WITH citations, not the
# model's parametric memory. These carry no temporal cue (so _NEEDS_WEB misses them) yet should still
# turn the web on so the answer path actually fetches and cites (real links, since the citation fix).
_WANTS_SOURCES = re.compile(
    r"\b("
    r"cite (your |the )?sources?|"
    r"with (sources?|citations?|references?|links?)|"
    r"(include|provide|add|give|list|show|need|want)( me)? "
    r"(a |some |the )?(sources?|citations?|references?|links?)|"
    r"find (?:me )?(?:[\w-]+ ){0,3}(sources?|references?|articles?|papers?|studies|research)|"
    r"back (it|this|that) up with (sources?|evidence|citations?|references?)|"
    r"according to (recent |current )?(sources?|studies|research|reports?)|"
    r"look (it|this|that|them) up|search (the web|online)|"
    r"do (some )?research|research (on|about|into|the)\b|"
    r"sources? for"
    r")\b",
    re.I,
)

# Map words to a concrete file format when the model didn't name one.
_FMT_HINTS = (
    (re.compile(r"\b(spreadsheet|excel|xlsx|csv)\b", re.I), "xlsx"),
    (re.compile(r"\b(presentation|powerpoint|pptx|slide deck|slides|\bdeck\b)\b", re.I), "pptx"),
    (re.compile(r"\b(word doc|word document|docx)\b", re.I), "docx"),
    (re.compile(r"\b(pdf)\b", re.I), "pdf"),
)
_FORMATS = ("pdf", "docx", "xlsx", "pptx", "md", "txt", "html")

# Requests to PRODUCE content that materially enables harm (a phishing artifact, malware, credential
# theft, a weapon). Refused at intent routing so the create-artifact ("do") path cannot launder a
# harmful ask past the answer-path safety. Deterministic backstop for the clear cases; `classify` also
# asks the model. Narrow, and only consulted when the message is already actionable (a produce verb via
# `looks_actionable`), so ordinary work - "a Q2 sales report", "a login form" - is never flagged.
# It names the threat as a bare noun; the `_DEFENSIVE` guard below then exempts a request that names
# the SAME threat for a legitimate protective purpose (spotting phishing, hardening against theft).
_HARMFUL = re.compile(
    r"\b("
    r"phishing|spear[- ]?phish|smishing|"
    r"malware|ransomware|spyware|keylogger|rootkit|botnet|trojan|"
    r"(computer\s+)?virus|backdoor|zero[- ]day|"
    r"credential[- ]?(harvest|steal|theft|stealer)|"
    r"steal\s+(?:\w+\s+){0,3}(?:passwords?|credentials?|logins?|credit\s*cards?)|"
    r"crack\s+(?:a\s+|the\s+|their\s+)?passwords?|"
    r"ddos|denial[- ]of[- ]service|"
    r"bomb|explosive|bioweapon|nerve\s+agent"
    r")\b",
    re.I,
)

# Streamed when a harmful create request is caught. Phrased as a genuine refusal (and it starts with
# "I can't", which the refusal detectors recognize).
REFUSAL = (
    "I can't help with that - it asks me to produce content that could be used to harm people (for "
    "example phishing, malware, or fraud), and I won't do that. If you have a legitimate, defensive "
    "need - spotting phishing, hardening a system, security training - I'm glad to help with that instead."
)


# Defensive / educational framing around a threat. A request that NAMES a harmful topic (phishing,
# malware, ...) for a legitimate protective purpose - spotting it, hardening against it, responding to
# it, training staff to recognise it - uses the threat noun as the SUBJECT of defence, not as an
# artifact to build. When this framing is present the deterministic layer defers to the model rather
# than refuse, so genuine security work ("a report on our phishing risks", "a memo on our ransomware
# response plan") is not blocked. Precision over recall by design: the model `harmful` flag in
# `classify()` still catches an actually harmful ask that happens to wear defensive words.
_DEFENSIVE = re.compile(
    r"\b("
    r"recogni[sz]e|spot(?:ting|s)?|detect(?:ing|ion)?|identif(?:y|ying|ication)|"
    r"prevent(?:ing|ion|ative)?|protect(?:ing|ion|ive)?|defend(?:ing)?|defen[cs]es?|"
    r"harden(?:ing|ed)?|mitigat(?:e|ing|ion)|respond(?:ing)?|response|recover(?:y|ing)|"
    r"safeguard(?:ing|s)?|remediat(?:e|ion)|resilience|"
    r"awareness|train(?:ing)?|educat(?:e|ing|ion)|best practices?|"
    r"risks?|threats?|posture|hygiene|readiness|incident response"
    r")\b",
    re.I,
)


def looks_harmful(message: str) -> bool:
    """Cheap, model-free signal that a message asks to PRODUCE clearly harmful content (a phishing
    artifact, malware, credential theft, a weapon). Precision-biased: a threat named for a defensive
    or educational purpose (spotting phishing, hardening against theft, an incident-response plan) is
    NOT flagged - it is left to the model layer - so legitimate security work is never blocked here.
    Consult it only for an already actionable message."""
    m = message or ""
    if not _HARMFUL.search(m):
        return False
    return not _DEFENSIVE.search(m)


# A message spoken TO the assistant about the conversation itself - a reaction, a correction, or a
# meta question ("why didn't you tell me that", "I'm talking about you", "that's not what I asked").
# These are NOT topics to look up: web-searching them makes the model answer about the *phrase*
# instead of the user. Tuned to catch the clear second-person-at-the-assistant cases without flagging
# ordinary questions that merely contain "you".
_CONVERSATIONAL = re.compile(
    r"\bi'?m\s+talking\s+about\s+you\b"
    r"|\bwhy\s+(did|do|are|would|wouldn'?t|don'?t|didn'?t|did\s+not)\s+you\b"
    r"|\byou\s+(did\s*n'?t|did\s+not|never|should\s+have|just|already|keep|kept)\b"
    r"|\byou\s+(told|said|gave|wrote|mentioned|answered|responded|replied)\b"
    r"|\byour\s+(previous|last|earlier|first|prior)\s+(answer|response|reply|message|point)\b"
    r"|\b(when\s+)?i\s+asked\s+you\b"
    r"|\bthat'?s\s+not\s+what\s+i\s+(asked|meant|said)\b"
    r"|\byou'?re\s+(wrong|right|missing|forgetting|repeating)\b"
    r"|\bas\s+i\s+(said|mentioned|told\s+you)\b",
    re.I,
)


def is_conversational(message: str) -> bool:
    """True for a message directed at the assistant about the conversation itself (a reaction, a
    correction, or a meta question like 'why didn't you tell me that') rather than a topic to look up.
    Used to keep such turns from triggering a web search - the assistant answers them from the
    conversation, not the web."""
    return bool(_CONVERSATIONAL.search(message or ""))


# ── agentic web-search planning (model decides whether to search + crafts the query) ──
# Default is agent-first: a capable model reasons about the turn and writes a focused query, the way
# a good agent would, instead of searching the raw message text. Small/unknown-weak local models fall
# back to the deterministic rule above. Models keep improving, so the bar is deliberately low and the
# default leans "let the model try" - the fallback catches a bad plan either way.
_PLAN_MIN_PARAMS_B = (
    7.0  # local models below this size use the deterministic fallback, not the planner
)

_PLAN_SYS = (
    "You decide whether the user's latest message needs a WEB SEARCH, and if so the best query.\n"
    "- Many messages need NO search: a remark, reaction, or question directed at YOU about the "
    "conversation itself (e.g. 'why didn't you tell me that', 'I'm talking about you', 'what did I "
    "just ask') - those are answered from the conversation, not the web.\n"
    "- A request for information about the world DOES need a search. When it does, write ONE focused, "
    "self-contained search query using the conversation for context - reformulate it for a search "
    "engine; do not just copy the user's words.\n"
    'Return ONLY a JSON object: {"search": true or false, "query": "<the query, or empty>"}.'
)


def model_can_plan(model: str, backend_kind: str = "ollama") -> bool:
    """Whether the served model is capable enough to plan its own web search (agent-first).

    Cloud / org models always are. A local model is gated by size - the deterministic rule is used
    below ``_PLAN_MIN_PARAMS_B`` - but an UNKNOWN size defaults to capable, because the goal is
    agent-by-default and the planner has a safe fallback if its plan can't be parsed.
    """
    if backend_kind == "openai":
        return True
    from ..hosting.source import params_from_name

    params = params_from_name(model or "")
    return params is None or params >= _PLAN_MIN_PARAMS_B


def plan_web_query(message: str, history, backend) -> tuple[bool, str] | None:
    """Ask the model to decide whether to search and to craft the query (agent-style). Returns
    ``(should_search, query)``, or ``None`` if the plan could not be parsed (caller falls back)."""
    msgs = [Message("system", _PLAN_SYS)]
    for role, content in (history or [])[-6:]:
        msgs.append(Message(role if role in ("user", "assistant") else "user", content))
    msgs.append(Message("user", message))
    try:
        # No hidden thinking for this call: it only decides whether to search and writes the query.
        data = extract_json(json_chat(backend, msgs, think=False))
    except Exception:
        return None
    if not isinstance(data, dict) or "search" not in data:
        return None
    return bool(data.get("search")), str(data.get("query") or "").strip()


def decide_web(
    message: str,
    history,
    backend,
    model: str,
    *,
    backend_kind: str = "ollama",
    web_on: bool,
) -> tuple[bool, str]:
    """Resolve ``(run_web_search, search_query)`` for a chat turn.

    Agent-first: when web is in play and the model is capable, the model plans whether to search and
    writes the query. Otherwise (or if the plan can't be parsed) fall back to the deterministic rule -
    skip a conversational turn, and search the message text as-is.
    """
    if not web_on:
        return False, message
    # Fast path: an obvious turn spoken to the assistant never needs a search - skip it without
    # paying for a planner call (matters now that web is in play by default on every turn).
    if is_conversational(message):
        return False, message
    if model_can_plan(model, backend_kind):
        planned = plan_web_query(message, history, backend)
        if planned is not None:
            do_search, query = planned
            return do_search, (query or message)
    return True, message  # small model (or unparseable plan): search the non-conversational message


# An EXPLICIT ask to create a persistent Agent (the third surface), e.g. "create an agent that keeps
# the wiki current". Deterministic on purpose: it matches only an explicit lead-in, so a normal chat
# is never misrouted, and it is checked BEFORE the model classifier (so it does not depend on the
# model getting the intent right). The user still confirms the proposal before anything is created.
_AGENT_CREATE = re.compile(
    r"^\s*(?:please\s+)?(?:create|make|set\s*up|spin\s*up|build|add)\s+(?:me\s+)?(?:an?\s+)?"
    r"(?:new\s+|persistent\s+|standing\s+)?agent\b",
    re.I,
)
_AGENT_LEADIN = re.compile(
    r"^\s*(?:please\s+)?(?:create|make|set\s*up|spin\s*up|build|add)\s+(?:me\s+)?(?:an?\s+)?"
    r"(?:new\s+|persistent\s+|standing\s+)?agent\b\s*(?:that|which|who|to|for|:|,|-)?\s*",
    re.I,
)


def looks_like_agent(message: str) -> bool:
    """True for an explicit 'create an agent ...' request (a persistent worker), which is routed to
    an agent proposal rather than a one-off 'do' or a recurring task. Deterministic - see above."""
    return bool(_AGENT_CREATE.match(message or ""))


def parse_agent(message: str) -> tuple[str, str]:
    """Split 'create an agent that/to <mandate>' into (name, mandate), deterministically. The mandate
    is the text after the lead-in; the name is a short title derived from it (the user can rename)."""
    m = (message or "").strip()
    mandate = _AGENT_LEADIN.sub("", m).strip() or m
    words = re.findall(r"[A-Za-z0-9]+", mandate)[:5]
    name = " ".join(words).strip().title()[:60] or "New agent"
    return name, mandate


# An EXPLICIT suggestion ABOUT Anthill itself (the product): a feature/bug/improvement idea the
# customer wants the project to build. Routed to the contribution intake (a spec object), not a task
# or an answer. Deliberately narrow and deterministic, like the agent detector: it fires only on an
# explicit suggestion label or an Anthill-referencing wish, so a normal request ("make me a summary",
# "add a column to this file") is NEVER misrouted into a product suggestion. The user still confirms.
_SUGGEST = re.compile(
    r"^\s*(?:"
    r"(?:feature\s+request|feature\s+suggestion|feature\s+idea|suggestion|bug\s+report|feedback)\s*[:\-]"
    r"|i\s+wish\s+(?:anthill|this\s+app|you)\b"
    r"|it\s+would\s+be\s+(?:great|nice|helpful|good|cool)\s+if\s+(?:anthill|this\s+app|you)\b"
    r"|i'?d\s+love\s+(?:it\s+)?if\s+(?:anthill|this\s+app|you)\b"
    r"|(?:anthill|this\s+app)\s+(?:should|could|needs?\s+to|ought\s+to)\b"
    r"|can\s+anthill\s+(?:please\s+)?(?:add|support|have|let|do)\b"
    r")",
    re.I,
)
_SUGGEST_LABEL = re.compile(
    r"^\s*(?:feature\s+request|feature\s+suggestion|feature\s+idea|suggestion|bug\s+report|feedback)"
    r"\s*[:\-]\s*",
    re.I,
)


def looks_like_suggestion(message: str) -> bool:
    """True for an explicit suggestion about Anthill the product (a feature/bug/improvement idea),
    which is routed to contribution intake rather than a task/answer. Deterministic - see above."""
    return bool(_SUGGEST.match(message or ""))


def parse_suggestion(message: str) -> str:
    """The idea text for the intake agent: strip a leading explicit label ('Feature request:'), else
    keep the whole message (the wish forms read fine as-is)."""
    m = (message or "").strip()
    return _SUGGEST_LABEL.sub("", m).strip() or m


# An EXPLICIT request to store a durable fact ("remember this: ...", "note that ...", "keep in mind
# ..."). Routed to an immediate memory save, bypassing the chat-distillation throttle (a single such
# message would otherwise be dropped because distillation only runs after several exchanges). Narrow +
# deterministic like the other detectors: the store phrase must LEAD the message, and bare "remember"
# only counts with "this" or a colon/comma - so "do you remember ...", "I don't remember", and
# "remember when we ..." (reminiscing, not a command) are NOT misrouted. The trailing group also eats a
# following separator/"that" so parse_remember returns just the fact. Sibling of the agent/suggestion
# detectors above.
_REMEMBER = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"remember\s+this(?:\s+that)?"  # remember this[: X] / remember this that X
    r"|remember\s*[:,]"  # remember: X / remember, X  (bare "remember" needs a colon/comma)
    r"|note\s+that|note\s+to\s+self|note\s*[:,]"  # note that X / note to self / note: X
    r"|make\s+a\s+note(?:\s+that)?"  # make a note [that] X
    r"|keep\s+in\s+mind(?:\s+that)?"  # keep in mind [that] X
    r"|don'?t\s+forget(?:\s+that)?"  # don't forget [that] X
    r"|for\s+(?:future\s+reference|the\s+record)"  # for future reference, X
    r"|jot\s+(?:this\s+)?down(?:\s+that)?"  # jot [this] down [that] X
    r")\s*[:,]?\s+(?=\S)",
    re.I,
)


def looks_like_remember(message: str) -> bool:
    """True for an explicit 'remember this: X' / 'note that X' request to store a durable fact, which
    is saved to memory immediately rather than waiting for chat distillation. Deterministic - the store
    phrase must lead the message, so 'do you remember ...', 'I don't remember', and 'remember when ...'
    do not match."""
    # Stripped first: with trailing whitespace gone the pattern's two whitespace runs cannot overlap on a
    # long run of spaces (which made it quadratic).
    return bool(_REMEMBER.match((message or "").strip()))


def parse_remember(message: str) -> str:
    """The fact to store: strip the leading 'remember this:' / 'note that' lead-in. Falls back to the
    whole message if stripping would leave nothing."""
    m = (message or "").strip()
    match = _REMEMBER.match(m)
    return (m[match.end() :].strip() if match else m) or m


def looks_actionable(message: str) -> bool:
    """Cheap pre-filter: True if the message might be a 'do' or 'schedule' (worth classifying)."""
    m = message or ""
    return bool(_SCHEDULE.search(m) or _ARTIFACT.search(m) or _ACTION.search(m))


def looks_research(message: str) -> bool:
    """Cheap pre-filter: True if the message is an EXPLICIT deep-research ask (a multi-source cited
    report), which should route to the research mode rather than a quick answer."""
    return bool(_RESEARCH.search(message or ""))


# A question that needs MULTI-STEP, multi-source work (the deep agent) rather than a single-pass RAG
# answer. Deterministic + conservative on purpose (issue #421): the fast single-pass answer is the
# default, so the *system* escalates depth automatically only on clear multi-hop signals - the user is
# never asked to click "a better answer". Simple/factual questions stay fast.
_DEEP = re.compile(
    r"\b(?:"
    r"compare|comparison|versus|vs\.?|difference(?:s)?\s+between|pros\s+and\s+cons|"
    r"trade[-\s]?offs?|step[-\s]?by[-\s]?step|walk\s+me\s+through|"
    r"across\s+(?:all|our|the|multiple|several|every)|for\s+each\b|each\s+of\s+(?:the|our|these)|"
    r"relationship\s+between|reconcile|cross[-\s]?reference|synthesi[sz]e|"
    r"how\s+(?:do|does|did|can|would)\s+\S.{0,200}?\b(?:relate|affect|impact|interact|compare|differ)\b"
    r")\b",
    re.I,
)


def looks_deep(message: str) -> bool:
    """True when answering well needs multi-step, multi-source reasoning (route to the deep agent),
    rather than a quick single-pass answer. Conservative: the fast answer is the default; this only
    catches clear multi-hop signals (a comparison, a cross-source synthesis, several questions at
    once), so simple questions stay fast. See issue #421."""
    m = (message or "").strip()
    if not m:
        return False
    if m.count("?") >= 2:  # several questions in one message -> multi-part -> deep
        return True
    return bool(_DEEP.search(m))


# Explicit hedging WITHIN an answer (PR #661 Tier 2) - a different signal from looks_deep (which reads
# the QUESTION, pre-emptively) and from wiki.ask._NON_ANSWER (an outright refusal, not hedging within a
# substantive answer). Deterministic + conservative on purpose, same reasoning as _DEEP: a false
# positive here would train users to ignore the "go deeper" suggestion, so this only catches explicit
# hedging phrases, never merely the presence of words like "may"/"possible"/"think" in ordinary
# technical prose (e.g. "the result may contain several records" must NOT trigger this).
_UNCERTAIN = re.compile(
    r"\b(?:"
    r"i'?m not (?:entirely |fully |completely )?(?:sure|certain)"
    r"|i am not (?:entirely |fully |completely )?(?:sure|certain)"
    r"|it'?s (?:possible|unclear) (?:that|whether|if)"
    r"|it is (?:possible|unclear) (?:that|whether|if)"
    r"|i (?:believe|think|suspect),? (?:but|though|however)"
    r"|i (?:could|might|may) be (?:wrong|mistaken)"
    r"|this (?:may|might|could) vary"
    r"|without (?:more|additional|further) (?:context|information|details?)"
    r"|hard to say (?:for sure|with certainty)"
    r"|(?:i'?m|i am) not entirely clear"
    r")\b",
    re.I,
)


def looks_uncertain(answer: str) -> bool:
    """True when an answer explicitly hedges ("I'm not sure, but...", "it's possible that...").

    Distinct from a flat refusal (wiki.ask._NON_ANSWER, e.g. "I don't have that information") - this
    detects genuine hedging language WITHIN an otherwise substantive answer. Conservative: only explicit
    hedging phrases trigger it, never the mere presence of words like "may"/"possible"/"think" in
    ordinary technical prose, so a confident answer is never second-guessed."""
    text = (answer or "").strip()
    if not text:
        return False
    return bool(_UNCERTAIN.search(text))


# Mean-per-token-logprob bands for the expert-tier escalation trigger (compound-compute-tiers spec).
# Negative floats, closer to 0 = more confident. NOT empirically calibrated against real model outputs
# - a starting point in the same spirit as sizing.py's _CLUSTER_NETWORK_EFFICIENCY/_MOE_OVERHEAD_FACTOR
# comments, i.e. an honest placeholder, not a measurement. Revisit once real call data exists.
# Below CONFIDENCE_UNCERTAIN: treat as uncertain outright, same weight as an explicit text hedge.
# At/above CONFIDENCE_CONFIDENT: treat as confident, skip both escalation and the grader call.
# In between: "borderline" - only Automated mode spends a call (a local grader) to decide; Ask mode
# has no borderline state, since it never spends anything beyond the call it already made.
CONFIDENCE_UNCERTAIN = -0.9
CONFIDENCE_CONFIDENT = -0.3


def seems_uncertain_for_ask(answer: str, confidence: float | None) -> bool:
    """Ask mode's suggestion signal (compound-compute-tiers spec): OR the existing text-hedge check
    with a low-confidence logprob signal, since chat_with_confidence() already returns confidence on
    the SAME call that produced ``answer`` - no extra round trip. ``confidence=None`` (older Ollama,
    a provider without logprob support, or a parse failure) means "no signal from this call", so it
    contributes nothing here; only an explicit hedge or a genuinely low reported confidence suggests
    the redo-with-more-effort follow-up, never the mere absence of a confidence figure."""
    if looks_uncertain(answer):
        return True
    return confidence is not None and confidence < CONFIDENCE_UNCERTAIN


# A short FOLLOW-UP that asks to redo the previous answer with more effort - the words that replace the
# retired "agent" / "redo with web" re-run buttons (#421). Three modes: "deep" (run the multi-step agent
# on the thread), "web" (search the web this turn), and "provider" (re-answer on the account's connected
# org/RunPod/inference-provider backend instead of the local default, #278). Deliberately narrow +
# length-bounded so a real new question that merely contains "more" or "look" is never hijacked.
_REDO_WEB = re.compile(
    r"\b(?:check|search|google|look\s+it\s+up(?:\s+on)?)\s+(?:the\s+)?(?:web|internet|online)\b|"
    r"\bsearch\s+(?:online|the\s+web|the\s+internet)\b|\blook\s+(?:this|that|it)\s+up\s+online\b|"
    r"\b(?:can\s+you\s+)?(?:web[-\s]?search|google)\s+(?:this|that|it)\b",
    re.I,
)
_REDO_DEEP = re.compile(
    r"\b(?:go|dig|dive)\s+deeper\b|\blook\s+into\s+(?:this|it|that)\s+(?:more|further|deeper)\b|"
    r"\b(?:in\s+)?(?:much\s+)?more\s+deta(?:il|iled)\b|\belaborate(?:\s+on\s+(?:this|that|it))?\b|"
    r"\bexpand\s+on\s+(?:this|that|it)\b|\bin\s+(?:more|greater)\s+depth\b|\bmore\s+thorough(?:ly)?\b|"
    r"\bdeeper\s+(?:dive|look)\b|\breally\s+dig\s+in\b",
    re.I,
)
_REDO_PROVIDER = re.compile(
    r"\buse\s+(?:the\s+)?(?:cloud|org|organization|connected|provider)\s+model\b|"
    r"\b(?:try|use)\s+the\s+(?:connected\s+)?(?:backend|provider|cloud)\b|"
    r"\bescalate\b(?:\s+(?:this|it))?|\bans?wer\s+(?:this\s+)?with\s+the\s+(?:cloud|provider|org)\b",
    re.I,
)


def redo_mode(message: str) -> str:
    """For a follow-up on an existing thread, classify a short "do it harder" request into ``"deep"``
    (escalate to the multi-step agent), ``"web"`` (search the web this turn), or ``"provider"`` (re-run
    on the connected org/RunPod/inference-provider backend instead of local, #278) - or ``""`` when it is
    not one. Replaces the retired re-run buttons with words (#421). Narrow + length-bounded on purpose so
    a genuine new question is never mistaken for a redo cue; the caller only consults it when prior
    context exists and the message is not harmful, and (for "provider") only acts on it when a backend
    is actually connected - see ``plane_routing.org_endpoint_connected``."""
    m = (message or "").strip()
    if not m or len(m) > 120:  # a redo cue is short; a long message is a real new question
        return ""
    if _REDO_PROVIDER.search(m):
        return "provider"
    if _REDO_WEB.search(m):
        return "web"
    if _REDO_DEEP.search(m):
        return "deep"
    return ""


def needs_web_hint(message: str) -> bool:
    """Cheap, model-free signal that a question should auto-enable the web (P4): either it asks for
    live/current information (``_NEEDS_WEB``), OR it asks for sourced/cited research (``_WANTS_SOURCES``)
    - which should fetch and cite real sources instead of answering from the model's memory. A capable
    model still gets the final say via the planner in ``decide_web``; this only turns the web ON."""
    m = message or ""
    return bool(_NEEDS_WEB.search(m) or _WANTS_SOURCES.search(m))


def format_from_text(message: str) -> str:
    """The file format implied by the wording (spreadsheet->xlsx, deck->pptx, ...), or ''."""
    for rx, fmt in _FMT_HINTS:
        if rx.search(message or ""):
            return fmt
    return ""


_SYS = (
    "Classify the user's message into exactly one intent and return ONLY a JSON object:\n"
    '{"intent": "answer" | "do" | "schedule" | "research", '
    '"format": "<pdf|docx|xlsx|pptx|md|txt|html or empty>", '
    '"depth": "quick" | "deep", '
    '"summary": "<one short sentence>", "harmful": true or false}\n'
    "- answer: a question or request for information to read in the chat.\n"
    "- do: asks you to MAKE or PRODUCE something now (a document, spreadsheet, slide deck, file, "
    "chart) or take a concrete one-off action. A written deliverable someone would open or keep - a "
    "summary, report, one-pager, briefing, or memo - is 'do' even when no file type is named; but "
    "summarizing or explaining something to read in the chat is 'answer'. Set format to the file type "
    "if named or implied (spreadsheet->xlsx, deck/presentation->pptx, Word doc->docx, PDF->pdf), else "
    "empty.\n"
    "- schedule: asks for recurring or time-deferred work ('every morning', 'daily', 'at 9am', "
    "'remind me').\n"
    "- research: asks for a DEEP, multi-source, cited write-up on a topic ('do deep research on X', "
    "'write a comprehensive report on Y') - more than a quick answer. A short factual question is "
    "'answer', even if it wants a source.\n"
    "- depth: 'deep' when answering WELL needs multi-step, multi-source reasoning (comparing or "
    "analysing several things, multi-hop questions, combining sources); 'quick' for a direct, "
    "single-source, or factual answer. MOST messages are 'quick' - only mark 'deep' when a single "
    "pass would clearly fall short.\n"
    "harmful: true ONLY if the message asks you to PRODUCE content that materially enables serious harm "
    "- phishing or scam messages, malware or other attack code, credential theft, fraud, weapons or "
    "explosives, or instructions for violence or a serious crime. Ordinary work - a report, a normal "
    "document, code for a legitimate app, an analysis - is NOT harmful even if it names a sensitive topic.\n"
    "summary: a short human sentence describing what you would produce or schedule.\n"
    "When unsure about the intent, choose answer."
)


def classify(message: str, backend, *, think: bool | None = None) -> dict:
    """Message -> {intent, format, summary, harmful}. Always valid; falls back to 'answer'.

    ``harmful`` is a safety flag for a request to PRODUCE content that materially enables harm - True if
    the deterministic pattern matches OR the model flags it. The caller must refuse a harmful request
    rather than route it to the create-artifact path (a protected safety invariant)."""
    try:
        msgs = [Message("system", _SYS), Message("user", message)]
        # think is passed only when given, so a call without it is exactly what it was before.
        data = extract_json(
            json_chat(backend, msgs) if think is None else json_chat(backend, msgs, think=think)
        )
    except Exception:
        data = {}
    intent = str(data.get("intent", "")).strip().lower()
    if intent not in ("answer", "do", "schedule", "research"):
        intent = "answer"
    if looks_research(
        message
    ):  # explicit deep-research language -> research mode, regardless of model
        intent = "research"
    fmt = str(data.get("format", "")).strip().lower().lstrip(".")
    if fmt not in _FORMATS:
        fmt = ""
    if intent == "do" and not fmt:  # backfill from the wording
        fmt = format_from_text(message)
    harmful = looks_harmful(message) or (isinstance(data, dict) and data.get("harmful") is True)
    # Depth (issue #421): the deterministic multi-hop signal OR the model's read. Escalation is the
    # exception, so a bad/missing model value defaults to "quick".
    depth = (
        "deep"
        if (looks_deep(message) or str(data.get("depth", "")).strip().lower() == "deep")
        else "quick"
    )
    return {
        "intent": intent,
        "format": fmt,
        "depth": depth,
        "summary": str(data.get("summary", "")).strip()[:200],
        "harmful": bool(harmful),
    }
