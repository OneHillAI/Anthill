"""Passages of the user's own documents, as part of what an answer is given. Spec:
docs/specs/answers-use-document-text.md, issue #226 (step 2).

A wiki page made from an upload is the model's summary of it, and a summary drops and sometimes changes facts. This
finds the passages of the kept document texts (``doc_index``) that match the question, so the model can be given
the document's own words: at most ``MAX_PASSAGES``, only when they contain at least ``MIN_RARE`` rare words of the
question, from the same workspaces that ground the chat, and nothing at all when none qualifies. No model is
called. The passages go first in the reference, labelled as the original; the summary page of the same document,
if it is also there, is labelled as a summary that may contain errors.

Answers carry the documents they were given as ``doc:`` entries in the list of sources next to the page slugs, so
the answer cache and the chat page keep them without a second channel (``split_sources`` separates them).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from . import items
from .doc_index import DocIndex, Hit

log = logging.getLogger(__name__)

ENABLED = os.environ.get("ANTHILL_DOC_CONTEXT", "1") != "0"
MAX_PASSAGES = 2
MIN_RARE = 2  # distinct rare words of the question a passage must contain
DOC_PREFIX = "doc:"
SUMMARY_NOTE = "[AI summary of {name}. It may contain errors: prefer the original text above.]"
BLOCK_HEADER = (
    "Passages from the user's original documents (their exact words; prefer these to any summary of the same "
    "document):"
)

_HASHED = re.compile(r"-[0-9a-f]{12}(?=\.[^./\\]*$|$)")


def original_name(raw_name: str) -> str:
    """The name the person uploaded, from the name the original is kept under (``<name>-<12 hex>.<ext>``)."""
    return _HASHED.sub("", raw_name or "")


def document_passages(spaces, question: str, *, limit: int = MAX_PASSAGES) -> list[Hit]:
    """The passages of the kept texts of ``spaces`` (the workspaces that ground this chat) that match
    ``question``, best first, at most ``limit``. Empty when switched off, when no workspace has an index, or
    when nothing has ``MIN_RARE`` rare words of the question. Never raises."""
    if not ENABLED or not question.strip():
        return []
    hits: list[Hit] = []
    try:
        for w in spaces:
            if w is None:
                continue
            idx = DocIndex(w.root / ".cache", w.root / "text")
            if idx.exists():
                # an original that is waiting, rejected or removed is never read, whatever the index holds
                hits.extend(
                    idx.search(
                        question, limit, min_rare=MIN_RARE, exclude=items.hidden_sources(w.root)
                    )
                )
    except Exception as e:  # noqa: BLE001 - a problem here must never cost the person their answer
        log.debug("document passages skipped: %s", e)
        return []
    hits.sort(key=lambda h: (-h.rare, h.score))
    return hits[:limit]


def passage_label(hit: Hit) -> str:
    name = original_name(hit.doc)
    return f"{name}, page {hit.page}" if hit.page else name


def format_block(hits: list[Hit]) -> str:
    """The passages as a block of the reference, each under the document it came from."""
    body = "\n\n".join(f"[{passage_label(h)}]\n{h.text}" for h in hits)
    return f"{BLOCK_HEADER}\n\n{body}"


def summary_labels(pages, hits: list[Hit]) -> dict[Path, str]:
    """For a retrieved wiki page that is the summary of a document some passage came from, the note to put above
    it. The page says which original it was written from in its provenance tag."""
    from ..lifecycle.version import read_provenance

    docs = {h.doc for h in hits}
    labels: dict[Path, str] = {}
    if not docs:
        return labels
    for page in pages:
        try:
            source = (read_provenance(page.read_text()) or {}).get("source", "")
        except OSError:
            continue
        if source in docs:
            labels[page] = SUMMARY_NOTE.format(name=original_name(source))
    return labels


def source_tags(hits: list[Hit]) -> list[str]:
    """One ``doc:`` entry per document the passages came from, under the name its text is kept by, with the pages:
    ``doc:report-1a2b3c4d5e6f.pdf (page 2)``. The answer cache follows that name; ``split_sources`` turns it into
    the name the person knows."""
    pages: dict[str, list[int]] = {}
    for h in hits:
        nums = pages.setdefault(h.doc, [])
        if h.page and h.page not in nums:
            nums.append(h.page)
    out = []
    for doc, nums in pages.items():
        label = f"{doc} (page {', '.join(str(n) for n in sorted(nums))})" if nums else doc
        out.append(DOC_PREFIX + label.replace("\n", " "))
    return out


def stored_name(entry: str) -> str:
    """The name a text is kept by, from a ``doc:`` entry of the sources list (with or without the prefix)."""
    return entry.removeprefix(DOC_PREFIX).split(" (page ")[0]


def split_sources(sources) -> tuple[list[str], list[str]]:
    """Separate an answer's list of sources into (wiki page slugs, document labels). A document label is the name
    the person uploaded, without the hash, with its pages: ``report.pdf (page 2)``."""
    pages: list[str] = []
    docs: list[str] = []
    for s in sources or []:
        if str(s).startswith(DOC_PREFIX):
            name = stored_name(str(s))
            docs.append(str(s).removeprefix(DOC_PREFIX).replace(name, original_name(name), 1))
        else:
            pages.append(str(s))
    return pages, docs
