"""Guiding principles: short, always-on directives per knowledge scope.

A `PRINCIPLES.md` per wiki workspace (like `SCHEMA.md`) holds plain-language rules
that apply to *every* answer/action in that scope (tone, house style, red lines).
Unlike wiki pages (retrieved when relevant) or skills (loaded when matched),
principles are injected into every response - so they are hard-capped to keep the
always-on context small, and the lowest-precedence scope (personal) is truncated
first when the budget is tight.
"""

from __future__ import annotations

import re

PRINCIPLES_FILE = "PRINCIPLES.md"
PER_SCOPE_CAP = 1500  # chars per scope
TOTAL_CAP = 3000  # chars across all scopes (always-on budget)


def read_principles(ws) -> str:
    """The principles text for one workspace, comment-template stripped + capped.
    Returns '' when the file is absent or only contains the seed template."""
    try:
        text = (ws.root / PRINCIPLES_FILE).read_text()
    except Exception:
        return ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return text[:PER_SCOPE_CAP]


def assemble_principles(scoped) -> str:
    """Combine principles across scopes into one bounded, always-on block.

    `scoped` is a list of (label, workspace) in PRECEDENCE order (organization first,
    personal last). Organization rules are authoritative; later scopes refine them.
    The total is hard-capped (TOTAL_CAP); the lowest-precedence scope is truncated or
    dropped first. Returns '' if no scope has principles.
    """
    out, budget, truncated = [], TOTAL_CAP, False
    for label, ws in scoped:
        body = read_principles(ws)
        if not body:
            continue
        chunk = f"[{label}]\n{body}"
        if len(chunk) > budget:
            chunk = chunk[:budget].rstrip()
            truncated = True
        if chunk:
            out.append(chunk)
        budget -= len(chunk)
        if budget <= 0:
            truncated = True
            break
    text = "\n\n".join(out).strip()
    if truncated and text:
        text += "\n\n[Some lower-precedence principles were truncated to fit the budget.]"
    return text
