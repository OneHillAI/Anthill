"""Issue #428 (bug A): the wiki review gate over-flagged clean uploads, stranding them in review.

A weak local model was allowed to set mechanical flags (e.g. broken_links on a link-free page) and
was consulted even when there was nothing to compare against (a first upload / an empty wiki), so a
solo user's "add a document -> usable knowledge" flow was non-functional. These tests pin the fixed
behavior: personal wikis and first pages skip the model pass; the model can never set a mechanical
flag; the deterministic mechanical checks still work."""

from __future__ import annotations

import json

from anthill.wiki.review import outline_change
from anthill.wiki.workspace import Workspace


class FakeBackend:
    """A model that returns canned review JSON and counts how often it is consulted."""

    def __init__(self, flags, summary="looks fine"):
        self._reply = json.dumps({"summary": summary, "flags": flags})
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return self._reply


class DeadBackend:
    """A model that can't be reached."""

    def chat(self, messages):
        raise RuntimeError("no model installed")


def _ws(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    return ws


def _seed(ws, title, body="some content"):
    ws.write_page(title, f"# {title}\n\n{body}\n")
    ws.rebuild_index()


def test_personal_first_clean_upload_auto_applies(tmp_path):
    """The reported repro: a clean first upload to a personal wiki is not flagged, and the model is
    not even consulted (nothing to compare against)."""
    ws = _ws(tmp_path)
    be = FakeBackend(["contradiction", "broken_links"])  # a model that WOULD over-flag
    o = outline_change(be, ws, "my-notes", "# My notes\n\n- one\n- two\n", scope="personal")
    assert o.flags == []
    assert o.recommendation == "approve"
    assert be.calls == 0  # personal scope skips the model pass entirely


def test_personal_upload_with_peers_ignores_model_flags(tmp_path):
    """Issue #428 comment (doc #2): even with a peer page present, a personal upload is gated only by
    mechanical checks; a weak model's contradiction/broken_links must not strand it."""
    ws = _ws(tmp_path)
    _seed(ws, "doc-one")
    be = FakeBackend(["contradiction", "broken_links"])
    o = outline_change(be, ws, "doc-two", "# Doc two\n\n- clean\n- content\n", scope="personal")
    assert o.flags == []
    assert be.calls == 0  # personal never runs the model pass


def test_personal_real_broken_link_still_flags(tmp_path):
    """Mechanical checks still protect a personal wiki: a genuine dangling [[link]] is flagged."""
    ws = _ws(tmp_path)
    o = outline_change(
        FakeBackend([]), ws, "notes", "# Notes\n\nSee [[Missing Page]].\n", scope="personal"
    )
    assert "broken_links" in o.flags


def test_model_cannot_set_a_mechanical_flag(tmp_path):
    """Shared scope with peers: a hallucinated mechanical flag from the model is dropped; only the
    model's own judgement flags stick."""
    ws = _ws(tmp_path)
    _seed(ws, "existing")
    be = FakeBackend(["broken_links", "pii", "contradiction"])
    o = outline_change(be, ws, "newpage", "# New page\n\nno links here\n", scope="org")
    assert be.calls == 1
    assert "broken_links" not in o.flags and "pii" not in o.flags
    assert o.flags == ["contradiction"]


def test_shared_wiki_still_runs_the_model(tmp_path):
    """A shared (team/org) wiki keeps the full gate - even its first page is model-reviewed, so a
    genuine contradiction is caught before it becomes team canon (unlike a personal wiki)."""
    ws = _ws(tmp_path)
    be = FakeBackend(["contradiction"])
    o = outline_change(be, ws, "charter", "# Charter\n\nrules\n", scope="org")
    assert be.calls == 1
    assert o.flags == ["contradiction"] and o.recommendation == "needs_edit"


def test_shared_fails_safe_when_model_unavailable(tmp_path):
    """A shared write with peers still fails safe to needs_edit when the model can't be reached."""
    ws = _ws(tmp_path)
    _seed(ws, "peer")
    o = outline_change(DeadBackend(), ws, "new", "# New\n\ncontent\n", scope="org")
    assert o.flags == ["needs_edit"]


def test_personal_does_not_block_when_model_unavailable(tmp_path):
    """The solo-no-model case: a personal write never blocks on an unavailable model."""
    ws = _ws(tmp_path)
    _seed(ws, "peer")
    o = outline_change(DeadBackend(), ws, "new", "# New\n\ncontent\n", scope="personal")
    assert o.flags == []


def test_duplicate_title_still_flags_personal(tmp_path):
    """The other mechanical gate (near-duplicate title) still applies to a personal new page."""
    ws = _ws(tmp_path)
    _seed(ws, "Quarterly Plan")
    o = outline_change(
        FakeBackend([]),
        ws,
        "quarterly-plan-2",
        "# Quarterly Plan\n\nother body\n",
        scope="personal",
    )
    assert "duplicate" in o.flags
