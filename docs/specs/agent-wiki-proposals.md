# Spec: pages an agent proposes go to review before they reach the wiki

Status: implemented. Lane: `pillar:knowledge`. Part of the knowledge base update (epic #270): a page a model writes on its own
is a proposal, and a person decides. Architecture: `anthill-internal/knowledge-pillar/architecture.md`.

## Problem

The agent tool `write_wiki_draft` is described to the model, and answers with the words "pending review", as a proposal for a
person to review. It wrote the page straight into the wiki of the run instead. A page an agent wrote could replace an existing page
and be read by answers at once, with nobody having looked at it.

## Requirements

1. **The tool proposes; it never writes.** `write_wiki_draft(slug, content)` hands the page to a function given to `make_tools`
   (`propose_write`). Without one, the tool says that writing to the wiki is not available in this run and writes nothing. The
   slug is reduced to a plain name as before, and the tool's description says a person reviews the page and that the wiki does not
   change until they approve.
2. **In the web app the proposal goes through the review gate.** `anthill/web/agent_writes.wiki_proposer` files the page with
   `propose_wiki_write(..., always_review=True)` for the wiki the run works in (the person's own wiki, a project's wiki or the
   organisation's, read from the workspace folder) and for the person who started the run. `always_review` queues the write
   as a pending review whatever the checks say, flagged `agent-written`, with the page marked as written by an agent. Nothing is
   written until a person approves it in the review queue; approving uses the existing route, so the page lands as an AI-written
   page that a person checked.
3. **An existing page is never replaced by a proposal.** The reviewer sees the proposed text and decides.
4. **Who gets the tool.** Chat agent mode, scheduled tasks and persistent agents are given the proposer, attributed to the person
   who started them. An agent-to-agent call has no person to attribute or to review, so it is given none and cannot write.
5. **A person who does nothing is unaffected.** Nothing is asked of anyone until an agent proposes a page; then it appears in the
   review queue like any other proposal. No model is called by the proposal itself.

## Known limits

- A proposal waits in the queue until someone approves or rejects it. Nothing reminds the person.
- The review check that runs on a proposal is the existing one; it is not made stricter here.
- The other agent tools that save things (`remember`, `create_file`) are not changed here.

## Acceptance

All in `tests/test_agent_wiki_proposals.py`:

- Without a reviewer the tool writes nothing; with one the page goes to it and not to the wiki; a slug cannot leave the wiki; the
  description no longer promises more than it does.
- Through the web app: a proposal is a pending review with the `agent-written` flag, the right scope and team, and the person who
  started the run, and the page is not in the wiki, in every one of the personal, project and organisation wikis, even when the
  checks find nothing to flag.
- A proposal leaves an existing page as it was and out of what answers read; approving it writes an AI-written page marked as
  checked by a person.
- An agent-to-agent run cannot write; a persistent agent proposes instead of writing.

## Not verified here

Nothing has been run on a real install or with a real model driving the tool, only through the test client and with the tool called
directly.
