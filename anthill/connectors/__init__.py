"""Curated connector catalog (the admin-facing gallery of MCP servers).

Anthill ships a small, vetted subset of the public MCP ecosystem so an admin can add a
popular connector (Google Drive, Slack, GitHub, ...) from a gallery instead of hand-typing
a transport + URL. The catalog is a vendored JSON file (offline-safe; no runtime registry
dependency); `scripts/sync_catalog.py` reconciles it against the official MCP Registry and
opens a PR. Every entry is reviewed via a normal PR (the curation step).
"""

from .catalog import (
    catalog_by_id,
    doc_source_ids,
    load_catalog,
    validate_entry,
)

__all__ = ["catalog_by_id", "doc_source_ids", "load_catalog", "validate_entry"]
