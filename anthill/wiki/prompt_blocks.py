"""The parts of an answer prompt as typed blocks, and the record of what the prompt held, made from the same blocks.
Spec: docs/specs/typed-context-builder.md (build plan step 2.1, slice a: the answer path).

The reference for a question is a list of blocks: a passage of a kept original, or a page (or the best parts of it), each with the
item it came from. The text the model is given is rendered from those blocks and is the text it was before; the record is made from
the same blocks, so it cannot say something the prompt did not hold. Item ids are carried here and are not written into the prompt.
A knowledge block that cannot name its item is refused and logged: it is not given to the model.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..common.text import strip_frontmatter
from . import answer_record
from .doc_context import BLOCK_HEADER, passage_label, summary_labels
from .doc_index import Hit
from .passages import PAGE_SEPARATOR, best_passages

log = logging.getLogger(__name__)

KNOWLEDGE_KINDS = (
    "page",
    "document_passage",
    "core_page",
)  # blocks that must name the item they came from


class MissingItemId(ValueError):
    """A knowledge block was made without the id of its item."""


@dataclass
class Block:
    kind: str  # "page" or "document_passage" here; the other kinds of the spec arrive with their slices
    text: str
    item: dict | None = None  # the item it came from: id, version, hash, space, and where in it

    def __post_init__(self) -> None:
        if self.kind in KNOWLEDGE_KINDS and not (self.item and self.item.get("id")):
            raise MissingItemId(f"a {self.kind} block needs the id of its item")


def _try(kind: str, text: str, item: dict | None, what: str) -> Block | None:
    try:
        return Block(kind, text, item)
    except MissingItemId:
        log.warning("%s was left out of the prompt: it has no item id", what)
        return None


def knowledge_blocks(spaces, pages: list[Path], doc_hits: list[Hit], question: str) -> list[Block]:
    """The blocks of the reference for ``question``: a block per document passage first (labelled as the original), then a
    block per page, where the page that is the summary of one of those documents carries a note saying so."""
    blocks: list[Block] = []
    for hit in doc_hits:
        ws = answer_record._workspace_of_text(hit.doc, spaces)
        item = (
            answer_record._document_item(hit.doc, [hit.page] if hit.page else [], ws)
            if ws
            else None
        )
        block = _try(
            "document_passage",
            f"[{passage_label(hit)}]\n{hit.text}",
            item,
            f"a passage of {hit.doc}",
        )
        if block:
            blocks.append(block)
    labels = summary_labels(pages, doc_hits)
    for path in pages:
        path = Path(path)
        ws = answer_record._workspace_of_page(path, spaces)
        item = answer_record._page_item(path, ws) if ws else None
        note = labels.get(path)
        text = (f"{note}\n" if note else "") + best_passages(
            strip_frontmatter(path.read_text()), question
        )
        block = _try("page", text, item, f"the page {path.stem}")
        if block:
            blocks.append(block)
    return blocks


def web_blocks(results) -> list[Block]:
    """A block per web result the model is given, as the line it is given. Web text is untrusted evidence, not an item of the
    knowledge base: the block carries its address and the time it was fetched and needs no item id."""
    from ..search.web import _result_line

    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        Block("web_evidence", _result_line(r), {"url": r.url, "fetched": fetched})
        for r in results
        if r.snippet  # the lines the prompt holds (see search.web.web_messages)
    ]


def whole_page_blocks(ws, pages: list[Path]) -> list[Block]:
    """A block per page, whole and as written, for a route that gives pages to the model without choosing passages. A page that
    cannot name its item is left out and logged."""
    blocks: list[Block] = []
    for path in pages:
        path = Path(path)
        block = _try(
            "page", path.read_text(), answer_record._page_item(path, ws), f"the page {path.stem}"
        )
        if block:
            blocks.append(block)
    return blocks


def render_reference(blocks: list[Block]) -> str:
    """The reference text the model is given: the document passages under their header, then the pages, as before."""
    docs = [b.text for b in blocks if b.kind == "document_passage"]
    pages = PAGE_SEPARATOR.join(b.text for b in blocks if b.kind == "page")
    if not docs:
        return pages
    return f"{BLOCK_HEADER}\n\n" + "\n\n".join(docs) + (PAGE_SEPARATOR + pages if pages else "")


def _entry(kind: str, text: str, **more: str) -> dict:
    return {
        "type": kind,
        **more,
        "hash": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
        "length": len(text),
    }


def record_of(
    blocks: list[Block],
    *,
    rules: str = "",
    profile: str = "",
    memory: str = "",
    context: str = "",
    history: list[tuple[str, str]] | None = None,
    route: str = "",
    model: str = "",
) -> dict:
    """The record of what the prompt held: every knowledge block as an item (a document once, with the pages its passages came from),
    and the rules, the profile, the memory, the context an agent run was given and each earlier turn as blocks of their own kind with a hash and a length, never the text."""
    listed: list[dict] = []
    by_doc: dict[str, dict] = {}
    seen_ids: set = set()
    web: list[dict] = []
    for b in blocks:
        item = dict(b.item or {})
        if b.kind == "web_evidence":
            web.append(
                _entry(
                    "web_evidence", b.text, url=item.get("url", ""), fetched=item.get("fetched", "")
                )
            )
        elif b.kind == "document_passage":
            seen = by_doc.get(str(item.get("name")))
            if seen is None:
                by_doc[str(item.get("name"))] = item
                listed.append(item)
            else:
                seen["pages"] = sorted(set(seen.get("pages", [])) | set(item.get("pages", [])))
        elif item.get("id") and item["id"] in seen_ids:
            continue  # the same page given twice (found, then read) is one item
        else:
            seen_ids.add(item.get("id"))
            listed.append(item)
    record = answer_record._cap(listed)
    others = [
        _entry(kind, text)
        for kind, text in (
            ("rules", rules),
            ("profile", profile),
            ("memory", memory),
            ("context", context),
        )
        if text.strip()
    ]
    others += web
    others += [_entry("history_turn", content, role=role) for role, content in history or []]
    if others:
        record["blocks"] = others
    if route:
        record["route"] = route
    if model:
        record["model"] = model
    return record
