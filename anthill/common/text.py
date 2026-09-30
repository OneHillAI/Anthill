from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_MDLINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def slugify(text: str, max_len: int = 60) -> str:
    s = _NON_ALNUM.sub("-", text.lower()).strip("-")
    return s[:max_len].strip("-") or "untitled"


def strip_frontmatter(markdown: str) -> str:
    """Return the markdown body with a leading YAML frontmatter block (``--- ... ---``) removed.

    Wiki pages carry OKGF frontmatter on disk (scope/review/etc.); any reader that wants the *prose* -
    rendering, model grounding, embeddings, title/summary - calls this so the metadata never leaks in.
    No leading frontmatter (a normal ``# H1`` page, or a body that just starts with a ``---`` rule with
    no closing delimiter) is returned unchanged. Mirrors ``wiki.okf._split_frontmatter``'s body split,
    without the YAML parse."""
    t = markdown.lstrip("﻿").lstrip()
    if not t.startswith("---"):
        return markdown
    rest = t[3:].lstrip("\n")
    end = rest.find("\n---")
    if end == -1:
        return markdown
    return rest[end + 4 :].lstrip("\n").rstrip()


def first_h1(markdown: str) -> str | None:
    for line in strip_frontmatter(markdown).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def normalize_wiki_page(markdown: str) -> str:
    """Enforce page structure: H1 → body → ## Related, regardless of model output order."""
    lines = markdown.splitlines()
    h1_lines: list[str] = []
    body_lines: list[str] = []

    in_related = False
    related_content: list[str] = []
    for line in lines:
        if line.startswith("# ") and not h1_lines:
            h1_lines.append(line)
            in_related = False
        elif line.lower().startswith("## related"):
            in_related = True
        elif in_related:
            related_content.append(line)
        else:
            body_lines.append(line)

    parts = []
    if h1_lines:
        parts += h1_lines
    body = "\n".join(body_lines).strip()
    if body:
        parts.append("")
        parts.append(body)
    if related_content:
        merged = " ".join(related_content).strip()
        if merged:
            parts.append("")
            parts.append("## Related")
            parts.append(merged)

    return "\n".join(parts).strip()


def outbound_links(markdown: str) -> set[str]:
    """Other wiki pages this page references: [[slug]] wiki-links and [label](slug.md) links.

    External URLs are ignored; only intra-wiki .md targets count.
    """
    body = strip_frontmatter(markdown)
    links = set(_WIKILINK.findall(body))
    for target in _MDLINK.findall(body):
        if "://" not in target and target.endswith(".md"):
            links.add(target[:-3].split("/")[-1])
    return links
