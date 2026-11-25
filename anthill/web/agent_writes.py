"""What an agent's ``write_wiki_draft`` does in the web app: it proposes the page to the review queue of the wiki the
run belongs to, and a person decides. Nothing an agent writes reaches the wiki, or an answer, before that."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def wiki_proposer(
    db, *, org_id: int, user_id: int | None, ws_path: str, who: str
) -> Callable[[str, str], str] | None:
    """The function ``make_tools(propose_write=...)`` takes, for a run that works in the wiki at ``ws_path`` for the
    person ``user_id``. None when there is nobody to attribute the proposal to or to review it (an agent-to-agent
    call): the tool then writes nothing."""
    if user_id is None:
        return None
    from ..lifecycle.version import tag_page
    from ..wiki.workspace import space_of_root

    scope, ref = space_of_root(Path(ws_path))
    team_id = int(ref.removeprefix("team-")) if scope == "team" else None

    def propose(slug: str, content: str) -> str:
        from .app import propose_wiki_write

        propose_wiki_write(
            db,
            org_id=org_id,
            proposed_by=user_id,
            slug=slug,
            content=tag_page(content, "agent"),
            target_scope=scope,
            team_id=team_id,
            source=f"proposed by {who}",
            always_review=True,
        )
        return f"Proposed '{slug}' for review. It is not in the wiki until a person approves it."

    return propose


def core_line_proposer(
    db, *, org_id: int, user_id: int | None, who: str
) -> Callable[[str], str] | None:
    """What the agent's ``remember`` does in the web app: it proposes one line for the owner's core page to their review queue, and a
    person decides (docs/specs/remember-this.md). None when there is nobody to attribute it to or to review it."""
    if user_id is None:
        return None

    def propose(fact: str) -> str:
        from ..wiki import core_page
        from ..wiki.workspace import workspace_for
        from .app import propose_wiki_write

        try:
            shown = core_page.preview(workspace_for("personal", user_id=user_id), fact)
        except ValueError as exc:
            return str(exc)
        if shown["already"]:
            return "That is already on the core page."
        if shown["over"]:
            return core_page.TooLong(shown["over"]).args[0]
        propose_wiki_write(
            db,
            org_id=org_id,
            proposed_by=user_id,
            slug=core_page.SLUG,
            content=shown["page_after"],
            target_scope="personal",
            source=f"proposed by {who}",
            always_review=True,
        )
        return "Proposed that line for the core page. It is not on the page until a person approves it."

    return propose
