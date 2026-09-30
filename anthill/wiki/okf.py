"""OKF (Open Knowledge Format) serialization for wiki pages + Anthill's governance extensions.

OKF (GoogleCloudPlatform/knowledge-catalog) represents knowledge as markdown files with YAML
frontmatter: portable, human- and agent-readable, diffable in git, no central registry. Anthill
adopts OKF as the wiki INTERCHANGE format so a page is readable by any OKF-aware tool, and layers its
governance (review state, scope/tier, provenance, signature) on top as ``x-anthill-*`` frontmatter
extensions - which OKF requires consumers to preserve. So an exported page stays standard-conformant
*and* carries Anthill's lifecycle, the layer OKF deliberately omits.

OKGF frontmatter is now the wiki's native ON-DISK format, not export-only: ``Workspace.write_page``
(``anthill/wiki/workspace.py``) writes every page through ``to_okf`` directly, and
``Workspace.migrate_to_okgf`` upgrades any pre-OKGF (frontmatter-less) page it finds. ``[[slug]]``
wikilinks stay Anthill's authoring shorthand in the body and are rewritten to OKF bundle-absolute links
only on export (this module's ``export``/``parse`` surface), which is the one remaining export/import-
specific layer - everything else described here already reflects how the live wiki is stored.
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml

from ..common.text import first_h1, slugify

OKF_VERSION = "0.1"  # the adopted spec version (stamped into a bundle's root index.md)
_DELIM = "---"
_DEFAULT_TYPE = "Concept"  # OKF requires a non-empty `type`; an Anthill topic page is a Concept
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")

# OkfPage attribute -> the `x-anthill-*` frontmatter key it serializes to. These are the governance
# extensions that make up the published "OKF + governance" profile; OKF tolerates + preserves them.
_EXT = {
    "scope": "x-anthill-scope",  # personal | team | org
    "review": "x-anthill-review",  # draft | proposed | approved
    "tier": "x-anthill-tier",  # bronze | silver | gold (training quality)
    "sources": "x-anthill-sources",  # provenance: list of source URIs
    "signature": "x-anthill-signature",  # Ed25519 promotion signature (base64)
}
_KNOWN_FM = {"type", "title", "description", "timestamp", "tags"} | set(_EXT.values())

# Reserved bundle/workspace files: metadata, not content pages - skipped on import.
RESERVED_BUNDLE_FILES = {"index.md", "log.md", "principles.md", "schema.md"}
_MAX_BUNDLE_FILES = 2000  # cap on pages read from one bundle
_MAX_FILE_BYTES = 1_000_000  # 1 MB per page - guards against tar bombs


@dataclass
class OkfPage:
    """An OKF page: the core OKF fields plus Anthill's governance extensions."""

    type: str = _DEFAULT_TYPE  # OKF required
    title: str = ""  # OKF recommended
    description: str = ""  # OKF recommended (one sentence)
    timestamp: str = ""  # OKF recommended (ISO 8601)
    tags: list[str] = field(default_factory=list)  # OKF recommended
    body: str = ""
    # Anthill governance extensions (serialized as x-anthill-*):
    scope: str = ""
    review: str = ""
    tier: str = ""
    sources: list[str] = field(default_factory=list)
    signature: str = ""
    extra: dict = field(
        default_factory=dict
    )  # any other frontmatter, preserved verbatim (OKF rule)


def wikilinks_to_okf(body: str) -> str:
    """Convert Anthill ``[[slug]]`` wikilinks to OKF bundle-absolute links ``[slug](/slug)``."""
    return _WIKILINK.sub(lambda m: f"[{m.group(1).strip()}](/{slugify(m.group(1).strip())})", body)


def to_okf(page: OkfPage) -> str:
    """Serialize an OkfPage to OKF markdown (YAML frontmatter + body). Always conformant: emits a
    non-empty ``type`` and valid YAML frontmatter."""
    fm: dict = {"type": page.type or _DEFAULT_TYPE}
    if page.title:
        fm["title"] = page.title
    if page.description:
        fm["description"] = page.description
    if page.timestamp:
        fm["timestamp"] = page.timestamp
    if page.tags:
        fm["tags"] = list(page.tags)
    for attr, key in _EXT.items():
        val = getattr(page, attr)
        if val:
            fm[key] = val
    fm.update(page.extra)  # pass through any preserved keys (e.g. OKF `resource`)
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"{_DELIM}\n{front}\n{_DELIM}\n\n{page.body.strip()}\n"


def parse_okf(text: str) -> OkfPage:
    """Parse OKF markdown back into an OkfPage. Unknown frontmatter keys are preserved in ``extra``
    (OKF requires consumers to tolerate + preserve unrecognized fields)."""
    fm, body = _split_frontmatter(text)
    page = OkfPage(
        type=str(fm.get("type") or "").strip(),
        title=str(fm.get("title") or "").strip(),
        description=str(fm.get("description") or "").strip(),
        timestamp=str(fm.get("timestamp") or "").strip(),
        tags=list(fm.get("tags") or []),
        body=body,
    )
    for attr, key in _EXT.items():
        if key in fm:
            setattr(page, attr, fm[key])
    page.extra = {k: v for k, v in fm.items() if k not in _KNOWN_FM}
    return page


def is_conformant(text: str) -> bool:
    """OKF conformance for a single page: valid YAML frontmatter present AND a non-empty ``type``."""
    try:
        fm, _ = _split_frontmatter(text)
    except Exception:
        return False
    return bool(isinstance(fm, dict) and str(fm.get("type") or "").strip())


def from_wiki_page(
    markdown: str,
    *,
    slug: str = "",
    mtime: float | None = None,
    type_: str = _DEFAULT_TYPE,
    scope: str = "",
    review: str = "",
    tier: str = "",
    sources: list[str] | None = None,
) -> str:
    """Export a wiki page to OKF markdown, carrying governance as extensions and rewriting ``[[slug]]``
    to OKF links. Handles both legacy pages (``# H1`` + body, no frontmatter) and native OKGF pages
    (already frontmattered): existing frontmatter is parsed and preserved, and explicit keyword
    arguments override it. Idempotent on an OKGF page - the body is never double-wrapped."""
    existing = parse_okf(markdown)  # ({}, whole-text) for a legacy page; parsed for an OKGF page
    body = existing.body
    ts = (
        datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        if mtime is not None
        else datetime.now(timezone.utc).isoformat()
    )
    page = OkfPage(
        type=existing.type or type_,
        title=existing.title or first_h1(body) or slug,
        description=existing.description or _first_paragraph(body),
        timestamp=existing.timestamp or ts,
        tags=existing.tags,
        body=wikilinks_to_okf(body),
        scope=scope or existing.scope,
        review=review or existing.review,
        tier=tier or existing.tier,
        sources=sources or existing.sources,
        signature=existing.signature,
        extra=existing.extra,
    )
    return to_okf(page)


def export_bundle(
    pages: list[tuple[str, str, float]],
    *,
    scope: str = "",
    okf_version: str = OKF_VERSION,
    log_md: str = "",
    principles_md: str = "",
) -> dict[str, str]:
    """Build an OKGF bundle (path -> file content) from wiki pages. Each ``(slug, markdown, mtime)``
    becomes an OKF-conformant ``<slug>.md`` carrying the governance extensions; a root ``index.md``
    declares ``okf_version`` and lists the pages; optional ``log.md`` / ``PRINCIPLES.md`` carry through.
    Flat layout, so a page's OKF Concept ID is its slug and ``[[slug]]`` links resolve to ``/slug``."""
    review = "approved" if scope == "org" else ""  # the org wiki is the published, approved set
    files: dict[str, str] = {}
    listing: list[tuple[str, str]] = []
    for slug, markdown, mtime in pages:
        files[f"{slug}.md"] = from_wiki_page(
            markdown, slug=slug, mtime=mtime, scope=scope, review=review
        )
        listing.append((first_h1(markdown) or slug, slug))
    idx = [f'---\nokf_version: "{okf_version}"\n---\n', "# Index\n"]
    idx += [f"- [{title}](/{slug})" for title, slug in sorted(listing)]
    files["index.md"] = "\n".join(idx) + "\n"
    if log_md.strip():
        files["log.md"] = log_md.rstrip() + "\n"
    if principles_md.strip():
        files["PRINCIPLES.md"] = principles_md.rstrip() + "\n"
    return files


def bundle_to_tgz(files: dict[str, str]) -> bytes:
    """Pack an OKGF bundle (path -> content) into a gzipped tar, sorted with a fixed mtime so the
    same wiki state yields stable bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in sorted(files.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def parse_bundle(data: bytes) -> list[tuple[str, OkfPage]]:
    """Parse an OKGF/OKF bundle (gzipped tar) into ``(slug, OkfPage)`` pairs - the inverse of
    ``export_bundle`` + ``bundle_to_tgz``, for importing foreign knowledge.

    Reads only regular ``.md`` files, skipping the reserved bundle/workspace files
    (``index.md`` / ``log.md`` / ``PRINCIPLES.md`` / ``SCHEMA.md``). Each page's slug is the slugified
    leaf name (path-based OKF Concept IDs flatten to the leaf). Unknown ``x-*`` frontmatter keys are
    preserved (``parse_okf`` keeps them in ``extra``), so an importer can re-emit them. Members with
    unsafe names (absolute or containing ``..``) or oversized files are skipped, so reading an untrusted
    bundle cannot escape or exhaust. Raises if ``data`` is not a readable tar archive."""
    out: list[tuple[str, OkfPage]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        for member in tf.getmembers():
            if len(out) >= _MAX_BUNDLE_FILES:
                break
            if not member.isfile():
                continue
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue  # path-traversal guard
            base = name.rsplit("/", 1)[-1]
            if not base.lower().endswith(".md") or base.lower() in RESERVED_BUNDLE_FILES:
                continue
            if member.size > _MAX_FILE_BYTES:
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            slug = slugify(base[:-3])  # strip ".md"
            if not slug:
                continue
            out.append((slug, parse_okf(fh.read().decode("utf-8", errors="replace"))))
    out.sort(key=lambda t: t[0])
    return out


def _first_paragraph(markdown: str) -> str:
    """The first non-empty, non-heading line - a one-sentence description for OKF (best-effort)."""
    for line in markdown.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---``-delimited YAML frontmatter from the markdown body. No frontmatter -> ({}, body)."""
    t = text.lstrip("﻿").lstrip()
    if not t.startswith(_DELIM):
        return {}, text.strip()
    rest = t[len(_DELIM) :].lstrip("\n")
    end = rest.find(f"\n{_DELIM}")
    if end == -1:
        return {}, text.strip()
    fm = yaml.safe_load(rest[:end]) or {}
    body = rest[end + len(_DELIM) + 1 :].lstrip("\n").rstrip()
    return (fm if isinstance(fm, dict) else {}), body
