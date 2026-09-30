"""Deep Research -> Wiki: turn a topic into a verified, cited wiki draft from web sources.

Searches the web, fetches the top sources, and asks the org's OWN model to synthesize a structured
markdown page grounded in them, with a Sources section. The caller files the result through the normal
wiki review gate (`propose_wiki_write`), so nothing becomes wiki until a human approves it.

Sovereignty: only the (search) queries leave the perimeter, to the org's configured web-search provider;
the page is written by the local model and lands in the org's own wiki under review. Phase 2 adds an
optional verification pass (`verify=True`): each key claim is cross-checked against the sources and a
Verification section flags anything that could not be corroborated across two or more of them.

The search function is injectable so the orchestration is unit-tested without hitting the network.
"""

from __future__ import annotations

from dataclasses import dataclass

# Cap on the research synthesis generation. A research report is longer than a chat answer, so this is
# larger than ANSWER_MAX_TOKENS, but it still bounds a runaway so the (non-streamed) research path can
# never block for minutes producing zero bytes. See qa/chat-eval/DEV_FINDINGS.md.
RESEARCH_MAX_TOKENS = 4096


class ResearchError(RuntimeError):
    """Research could not produce a draft (no topic, no sources, or synthesis failed)."""


@dataclass
class Claim:
    """A factual claim extracted from the report, with the 1-based indices of the sources that
    corroborate it. A claim counts as verified only when two or more sources support it."""

    text: str
    support: list[int]

    @property
    def verified(self) -> bool:
        return len(self.support) >= 2

    @property
    def status(self) -> str:
        n = len(self.support)
        return "verified" if n >= 2 else ("weak" if n == 1 else "unverified")


@dataclass
class Verification:
    """The result of cross-checking the report's claims against the fetched sources (Phase 2)."""

    claims: list[Claim]

    @property
    def flagged(self) -> list[Claim]:
        """Claims a reviewer should check: corroborated by fewer than two sources."""
        return [c for c in self.claims if not c.verified]

    @property
    def summary(self) -> str:
        verified = sum(1 for c in self.claims if c.verified)
        return f"{verified} of {len(self.claims)} key claims corroborated by 2 or more sources."


@dataclass
class ResearchResult:
    title: str
    markdown: str  # the synthesized wiki page, including a "## Sources" section
    sources: list[str]  # the source URLs used
    queries: list[str]  # the web searches that were run
    verification: Verification | None = None  # set when research_topic(..., verify=True)


def _default_search(query: str, max_results: int):
    from .search.web import web_search

    return web_search(query, max_results=max_results, fetch_bodies=True)


def _plan_queries(topic: str, n: int) -> list[str]:
    """A few angles for breadth. Deterministic in Phase 1 (no model call for planning)."""
    angles = [
        topic,
        f"{topic} overview",
        f"{topic} key facts and details",
        f"{topic} best practices",
    ]
    return angles[: max(1, n)]


def _synthesize(backend, topic: str, sources: list) -> str:
    from .inference.base import Message

    blocks = []
    for i, s in enumerate(sources, 1):
        title = getattr(s, "title", "") or getattr(s, "url", "")
        text = (getattr(s, "body", "") or getattr(s, "snippet", "") or "")[:1500]
        blocks.append(f"[{i}] {title} - {getattr(s, 'url', '')}\n{text}")
    system = (
        "You are a research librarian writing a page for an organization's private wiki. Using ONLY the "
        "web sources provided, write a clear, well-structured markdown page about the topic: a one-line "
        "summary, then the key points as a bulleted list, then short sections with the important details. "
        "Ground every claim in the sources and do not invent facts; if the sources are thin or disagree, "
        "say so plainly. Do NOT add a sources list (it is appended for you). No preamble or meta-commentary."
    )
    user = f"Topic: {topic}\n\nSources:\n" + "\n\n".join(blocks)
    messages = [Message("system", system), Message("user", user)]
    # Bound the synthesis: a reasoning model writing a multi-source report can otherwise run away and
    # block the (non-streamed) research path for minutes, producing zero bytes until the client times
    # out. A generous cap can't truncate a real report but stops the runaway. See DEV_FINDINGS.md.
    try:
        return backend.chat(messages, num_predict=RESEARCH_MAX_TOKENS)
    except TypeError:  # a backend whose chat() doesn't accept num_predict
        return backend.chat(messages)


_MAX_CLAIMS = 10


def _extract_claims(backend, body: str) -> list[str]:
    """Ask the model to pull the key factual claims out of the synthesized report, one per line."""
    import re

    from .inference.base import Message

    system = (
        "You are a fact-checker. From the text below, extract the most important factual claims as a "
        "flat list, one claim per line, with no numbering or bullet characters. Each claim must be a "
        "single, self-contained, verifiable statement. Output ONLY the claims, nothing else."
    )
    raw = backend.chat([Message("system", system), Message("user", body)]) or ""
    claims: list[str] = []
    for line in raw.splitlines():
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if len(text) >= 8 and text not in claims:
            claims.append(text)
    return claims[:_MAX_CLAIMS]


def _verify_claim(backend, claim: str, sources: list) -> list[int]:
    """Return the 1-based source indices that DIRECTLY support ``claim`` (strict; empty if none)."""
    from .common.jsonchat import extract_json, json_chat
    from .inference.base import Message

    blocks = []
    for i, s in enumerate(sources, 1):
        title = getattr(s, "title", "") or getattr(s, "url", "")
        text = (getattr(s, "body", "") or getattr(s, "snippet", "") or "")[:800]
        blocks.append(f"[{i}] {title}\n{text}")
    system = (
        "You are a strict fact-checker. Given a claim and numbered sources, decide which sources "
        "DIRECTLY support the claim. Include a source only if it clearly states or implies the claim; "
        "do not guess or rely on outside knowledge. Reply with ONLY a JSON object of the form "
        '{"supporting": [source numbers]}. If no source supports the claim, reply {"supporting": []}.'
    )
    user = f"Claim: {claim}\n\nSources:\n" + "\n\n".join(blocks)
    data = extract_json(json_chat(backend, [Message("system", system), Message("user", user)]))
    out: list[int] = []
    raw_support = data.get("supporting", []) if isinstance(data, dict) else []
    for n in raw_support if isinstance(raw_support, list) else []:
        try:
            k = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= k <= len(sources) and k not in out:
            out.append(k)
    return out


def verify_report(backend, body: str, sources: list) -> Verification:
    """Extract the report's key claims and corroborate each against the sources (Phase 2). A claim
    needs two or more independent sources to count as verified; the rest are flagged for review."""
    claims = _extract_claims(backend, body)
    return Verification(
        claims=[Claim(text=c, support=_verify_claim(backend, c, sources)) for c in claims]
    )


def _verification_md(verification: Verification, sources: list) -> str:
    """Render the Verification section: a one-line summary plus a 'needs review' list of any claims
    that could not be corroborated across two or more sources."""
    lines = ["## Verification", "", verification.summary]
    flagged = verification.flagged
    if flagged:
        lines += ["", "Needs review (could not corroborate across 2 or more sources):"]
        for c in flagged:
            if c.support:
                cited = ", ".join(
                    getattr(sources[i - 1], "url", "") or f"source {i}" for i in c.support
                )
                detail = f"only 1 source ({cited}); find a second to confirm"
            else:
                detail = "no source corroborates this; verify before publishing"
            lines.append(f"- {c.text} - {detail}")
    return "\n".join(lines)


def _gather_sources(search, topic: str, max_sources: int, max_queries: int) -> list:
    """Run the planned web searches and collect up to ``max_sources`` unique-by-URL results."""
    seen: set[str] = set()
    sources: list = []
    for q in _plan_queries(topic, max_queries):
        try:
            results = search(q, max_sources)
        except Exception:
            results = []  # a flaky search never dead-ends the run; other angles may still find sources
        for r in results:
            url = getattr(r, "url", "") or ""
            if url and url not in seen:
                seen.add(url)
                sources.append(r)
        if len(sources) >= max_sources:
            break
    return sources[:max_sources]


def _title(topic: str) -> str:
    return topic if len(topic) <= 100 else topic[:100].rstrip()


def _sources_md(sources: list) -> str:
    src_list = "\n".join(
        f"- [{getattr(s, 'title', '') or s.url}]({s.url})" for s in sources if getattr(s, "url", "")
    )
    return f"## Sources\n{src_list}"


def research_topic(
    topic: str,
    *,
    backend,
    search_fn=None,
    max_sources: int = 5,
    max_queries: int = 3,
    verify: bool = False,
) -> ResearchResult:
    """Research ``topic`` on the web and synthesize a cited markdown wiki page. Raises ResearchError
    on no topic / no sources. The caller files the markdown through the wiki review gate. When
    ``verify`` is set, run the Phase 2 verification pass (cross-check each key claim against the
    sources) and append a Verification section that flags anything thin for the reviewer."""
    topic = (topic or "").strip()
    if not topic:
        raise ResearchError("Research needs a topic.")
    sources = _gather_sources(search_fn or _default_search, topic, max_sources, max_queries)
    if not sources:
        raise ResearchError(
            "No web sources found. Check that web search is configured (Settings) and the topic is searchable."
        )

    body = _synthesize(backend, topic, sources).strip()
    title = _title(topic)
    verification = verify_report(backend, body, sources) if verify else None
    sections = [f"# {title}", body]
    if verification is not None:
        sections.append(_verification_md(verification, sources))
    sections.append(_sources_md(sources))
    markdown = "\n\n".join(sections) + "\n"
    return ResearchResult(
        title=title,
        markdown=markdown,
        sources=[s.url for s in sources],
        queries=_plan_queries(topic, max_queries),
        verification=verification,
    )


def _synthesis_messages(topic: str, sources: list):
    from .inference.base import Message

    blocks = []
    for i, s in enumerate(sources, 1):
        title = getattr(s, "title", "") or getattr(s, "url", "")
        text = (getattr(s, "body", "") or getattr(s, "snippet", "") or "")[:1500]
        blocks.append(f"[{i}] {title} - {getattr(s, 'url', '')}\n{text}")
    system = (
        "You are a research librarian writing a page for an organization's private wiki. Using ONLY the "
        "web sources provided, write a clear, well-structured markdown page about the topic: a one-line "
        "summary, then the key points as a bulleted list, then short sections with the important details. "
        "Ground every claim in the sources and do not invent facts; if the sources are thin or disagree, "
        "say so plainly. Do NOT add a sources list (it is appended for you). No preamble or meta-commentary."
    )
    user = f"Topic: {topic}\n\nSources:\n" + "\n\n".join(blocks)
    return [Message("system", system), Message("user", user)]


def _synthesize_stream(backend, topic: str, sources: list):
    """Streaming sibling of ``_synthesize``: yield the report body token-by-token. Runs with thinking
    OFF (this is a formatting task, so the whole token budget goes to the report, not a ``<think>``
    block) and bounded; degrades to a single chunk on a backend that can't stream."""
    messages = _synthesis_messages(topic, sources)
    streamer = getattr(backend, "chat_stream", None)
    if streamer is None:
        yield _synthesize(backend, topic, sources)
        return
    for kwargs in ({"num_predict": RESEARCH_MAX_TOKENS, "think": False}, {}):
        try:
            yield from streamer(messages, **kwargs)
            return
        except TypeError:
            continue  # a streamer that doesn't accept those kwargs -> retry plain


def research_stream(
    topic: str, *, backend, search_fn=None, max_sources: int = 5, max_queries: int = 3
):
    """Streaming research: a generator that yields the cited report AS it is produced. It emits a status
    line before the (blocking) web search so the client gets bytes immediately - never a zero-byte window
    the client times out on - then streams the synthesized body token-by-token, then the Sources section.
    Raises ResearchError on no topic / no sources (the caller surfaces it). Same output shape as
    ``research_topic``, but it never blocks for the whole run (fixes the research-path chat hang)."""
    topic = (topic or "").strip()
    if not topic:
        raise ResearchError("Research needs a topic.")
    yield f"# {_title(topic)}\n\n"
    yield "_Searching the web..._\n\n"
    sources = _gather_sources(search_fn or _default_search, topic, max_sources, max_queries)
    if not sources:
        raise ResearchError(
            "No web sources found. Check that web search is configured (Settings) and the topic is searchable."
        )
    yield f"_Read {len(sources)} sources. Writing the report..._\n\n"
    yield from _synthesize_stream(backend, topic, sources)
    yield "\n\n" + _sources_md(sources) + "\n"
