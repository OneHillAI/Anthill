"""Mint a GitHub issue from an accepted contribution proposal (ASDD public surface P4a).

This is the hand-off from the internal pipeline to GitHub: an accepted proposal becomes a public issue
attributed to the proposer, carrying the drafted spec. Guardrails it preserves:

- **Reference code is data, never a diff.** Any attached code goes into the issue body inside a fenced
  block clearly labelled a reference, so a developer (or the P4b developer agent) re-derives from the
  spec rather than merging the submitted code verbatim (ASDD membrane, STANDARD 3.9).
- **Attribution.** The proposer's handle/provider is recorded on the issue.
- **Disclosure.** The issue body carries the agent-authored disclosure trailer (STANDARD 1.2).

Configuration is owner-supplied (`ANTHILL_CONTRIB_REPO` = "owner/repo", `ANTHILL_CONTRIB_GITHUB_TOKEN`
= a token with `issues:write`). When unset, minting is a graceful no-op - the feature lights up when
the central instance is configured, and nothing is posted anywhere by default.
"""

from __future__ import annotations

import os

_DISCLOSURE = "Filed by Anthill from a contributor proposal (automated, on behalf of a maintainer)."


def contrib_repo_config() -> tuple[str | None, str | None]:
    """(repo, token) for the contribution repo, from the environment, or (None, None) if not set."""
    repo = (os.environ.get("ANTHILL_CONTRIB_REPO") or "").strip() or None
    token = (os.environ.get("ANTHILL_CONTRIB_GITHUB_TOKEN") or "").strip() or None
    return repo, token


def build_issue_body(
    *,
    spec: str,
    kind: str,
    proposer_handle: str,
    proposer_provider: str,
    reference_code: str | None,
) -> str:
    """The issue body: the drafted spec, then attribution, then reference code as DATA (never a diff),
    then the agent-authored disclosure. Kept plain so it reads well on GitHub."""
    who = proposer_handle or "a contributor"
    via = f" via {proposer_provider}" if proposer_provider and proposer_provider != "inapp" else ""
    parts = [
        (spec or "").strip(),
        "",
        "---",
        f"**Proposed by** {who}{via}. **Type:** {kind or 'feature'}.",
    ]
    if reference_code and reference_code.strip():
        parts += [
            "",
            "**Reference code** (a hint from the proposer - reference only, NOT a diff to merge; "
            "re-derive the implementation from the spec above):",
            "",
            "```",
            reference_code.strip()[:8000],
            "```",
        ]
    parts += ["", f"_{_DISCLOSURE}_"]
    return "\n".join(parts)


def mint_issue(
    repo: str, token: str, title: str, body: str, labels: list[str] | None = None
) -> dict:
    """Create a GitHub issue via the REST API. Returns ``{"number": int, "url": str}``. Raises on a
    non-201 response so the caller can surface the failure (it never silently claims success)."""
    import httpx

    payload: dict = {"title": title[:250] or "Contribution", "body": body}
    if labels:
        payload["labels"] = labels
    resp = httpx.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=payload,
        timeout=20.0,
    )
    if resp.status_code != 201:
        raise RuntimeError(f"GitHub issue create failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    return {"number": int(data.get("number", 0)), "url": str(data.get("html_url", ""))}
