"""Agent Skills - packaged, reusable capabilities the agent loads on demand.

Anthill skills are conformant **agentskills.io** skills (the open Agent Skills format), extended with
Anthill's governance/routing fields - the same adopt-and-extend move OKGF makes over OKF. A skill is a
folder `skills/<name>/SKILL.md` (name = the folder, lowercase-hyphenated) whose YAML frontmatter carries
the standard base fields (`name`, `description`, optional `license`/`compatibility`/`allowed-tools`) plus
Anthill's extensions under an `x-anthill-*` namespace (`x-anthill-title` display name,
`x-anthill-when-to-use`, `x-anthill-tier`, `x-anthill-scopes`) that base-standard tools ignore. The body
after the frontmatter is the instructions. See `docs/AGENT_SKILLS.md`.

Backward-compatible: the reader still accepts the pre-standard frontmatter (a title-cased `name`,
top-level `when_to_use`/`scopes`/`tier`), and a loose `skills/<slug>.md` single file.

At run time the executor matches the task against the available skills (cheap keyword overlap, no model
call) and injects the matched skills' instructions into its system prompt - so only relevant skills enter
the context, keeping it lean (agentskills.io "progressive disclosure": discover on name+description,
load the full body on activation).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..common.jsonchat import coerce_str, extract_json, json_chat
from ..common.text import WIKILINK_RE


@dataclass
class Skill:
    slug: str  # the agentskills.io `name` (lowercase-hyphenated, == the folder name)
    name: str  # display title (agentskills.io `x-anthill-title`, or a title-cased slug)
    description: str = ""
    when_to_use: str = ""
    instructions: str = ""
    scopes: list = field(default_factory=list)
    path: str = ""
    tier: str = "builtin"  # builtin | personal | team | org | gallery (origin, for provenance/UI)
    assets: list = field(default_factory=list)  # bundled files (relative paths in the skill folder)
    # Optional agentskills.io base fields, preserved on read/write so a skill round-trips cleanly.
    license: str = ""
    compatibility: str = ""
    allowed_tools: str = ""
    # Set by write_skill() when it also synced the DB registry (#683 phase 7) - lets a caller that
    # already has this Skill in hand attach the row to an audit-log entry without an extra query.
    registry_id: int | None = None


# agentskills.io `name`: 1-64 chars, lowercase alnum + single hyphens, no leading/trailing/consecutive
# hyphens, and it must equal the skill's folder name.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_MAX = 64
_DESC_MAX = 1024


def conform_name(text: str) -> str:
    """Normalise any label to a conformant agentskills.io skill name: lowercase, alnum + single hyphens,
    no leading/trailing/consecutive hyphens, <= 64 chars. Used for the skill folder and the `name:` field
    so they always match and validate."""
    from ..common.text import slugify

    s = re.sub(r"-{2,}", "-", slugify(text or "")).strip("-")[:_NAME_MAX].strip("-")
    return s or "skill"


def validate_skill_name(name: str) -> str:
    """'' if `name` is a valid agentskills.io skill name, else a human-readable reason."""
    if not name or not (1 <= len(name) <= _NAME_MAX):
        return f"name must be 1-{_NAME_MAX} characters"
    if not _NAME_RE.match(name):
        return "name must be lowercase letters/numbers with single hyphens (no leading/trailing/consecutive)"
    return ""


def skills_dir(directory: str | None = None) -> Path:
    return Path(directory or os.environ.get("ANTHILL_SKILLS_DIR", "skills"))


# The vendored template gallery ships INSIDE the package (anthill/skills_gallery/), deliberately
# separate from ANTHILL_SKILLS_DIR/a workspace's skills dir - a gallery entry is inert (never loaded
# by load_skills/match_skills/the executor) until a user explicitly adopts it, so it can never
# silently start matching or executing. See anthill/skills_gallery/SOURCES.md for each entry's real
# origin and license.
_GALLERY_DIR = Path(__file__).resolve().parent.parent / "skills_gallery"
# Open licenses this gallery accepts. Matched case-insensitively against a skill's own `license:`
# field - an entry with no `license:`, or a source-available/proprietary one, is silently dropped
# rather than shipped, so this can't quietly start including non-open content.
_APPROVED_GALLERY_LICENSES = frozenset({"apache-2.0", "apache 2.0", "apache license 2.0"})


def gallery_dir(directory: str | None = None) -> Path:
    return Path(directory or os.environ.get("ANTHILL_SKILLS_GALLERY_DIR", "") or _GALLERY_DIR)


def parse_skill_md(text: str, *, slug: str = "", path: str = "") -> Skill:
    """Parse a SKILL.md into a Skill. Reads the agentskills.io base frontmatter (`name`, `description`,
    `license`, `compatibility`, `allowed-tools`) plus Anthill's `x-anthill-*` extensions, and still
    accepts the pre-standard frontmatter (a title-cased `name`, top-level `when_to_use`/`scopes`/`tier`)
    for backward compatibility. Frontmatter is optional."""
    fm: dict = {}
    body = text
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---\s*\n?(.*)$", text, re.DOTALL)
    if m:
        front, body = m.group(1), m.group(2)
        for line in front.splitlines():
            # Skip indented lines: they continue a nested block (e.g. a `metadata:` map), not a top-level
            # key - splitting them would mis-read the nested keys.
            if not line.strip() or line[:1].isspace() or ":" not in line:
                continue
            k, v = line.split(":", 1)
            fm[k.strip().lower()] = v.strip()

    def _first(*keys: str) -> str:
        for k in keys:
            if fm.get(k):
                return fm[k]
        return ""

    raw_name = _first("name")
    # Display title: the explicit extension, else the (pre-standard) title-cased `name`, else the
    # slug. A genuinely conformant agentskills.io `name` (no x-anthill-title extension - e.g. a
    # skill from the vendored gallery, or any other third-party skill) is a lowercase-hyphenated
    # slug, not a display title - prettify it instead of showing "brand-guidelines" verbatim. A
    # legacy, already-title-cased Anthill `name` (e.g. "Effective Wiki Page") is left unchanged.
    if raw_name and _NAME_RE.match(raw_name):
        raw_name = raw_name.replace("-", " ").replace("_", " ").strip().title()
    title = _first("x-anthill-title", "title") or raw_name or slug.replace("-", " ").strip().title()
    scopes_v = _first("x-anthill-scopes", "scopes")
    return Skill(
        slug=slug,
        name=title,
        description=_first("description"),
        when_to_use=_first("x-anthill-when-to-use", "when_to_use", "when"),
        instructions=body.strip(),
        scopes=[s for s in re.split(r"[,\s]+", scopes_v) if s],
        path=path,
        tier=_first("x-anthill-tier", "tier") or "builtin",
        license=_first("license"),
        compatibility=_first("compatibility"),
        allowed_tools=_first("allowed-tools", "allowed_tools"),
    )


def _folder_assets(folder: Path) -> list:
    """Files bundled with a folder skill (everything under it except SKILL.md),
    as POSIX relative paths, sorted. [] for a loose single-file skill."""
    try:
        return sorted(
            f.relative_to(folder).as_posix()
            for f in folder.rglob("*")
            if f.is_file() and f.name != "SKILL.md"
        )
    except Exception:
        return []


def _write_assets(folder: Path, assets: dict) -> None:
    """Write bundled asset files under the skill folder. Skips any path that would
    escape the folder (no traversal) and the reserved SKILL.md name."""
    root = folder.resolve()
    for rel, data in (assets or {}).items():
        rel = str(rel).strip().lstrip("/")
        if not rel or Path(rel).name == "SKILL.md":
            continue
        target = (folder / rel).resolve()
        if root != target and root not in target.parents:
            continue  # path traversal attempt
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data if isinstance(data, str) else str(data))


def _load_dir(d: Path) -> list[Skill]:
    """Parse all skills in a single directory ([] if missing)."""
    if not d.exists():
        return []
    skills: list[Skill] = []
    seen: set[str] = set()
    for p in sorted(d.glob("*/SKILL.md")):
        sk = parse_skill_md(p.read_text(), slug=p.parent.name, path=str(p))
        sk.assets = _folder_assets(p.parent)
        skills.append(sk)
        seen.add(sk.slug)
    for p in sorted(d.glob("*.md")):
        if p.stem in seen:
            continue
        skills.append(parse_skill_md(p.read_text(), slug=p.stem, path=str(p)))
    return skills


def load_skills(directory: str | None = None, scoped=None) -> list[Skill]:
    """Built-in skills (ANTHILL_SKILLS_DIR) plus, when `scoped` is given, the skills
    owned by each in-scope workspace. `scoped` is a list of (tier, workspace); each
    loaded Skill is tagged with its origin tier (builtin | personal | team | org)."""
    out: list[Skill] = []
    for sk in _load_dir(skills_dir(directory)):
        sk.tier = "builtin"
        out.append(sk)
    for tier, ws in scoped or []:
        try:
            for sk in _load_dir(ws.skills):
                sk.tier = tier
                out.append(sk)
        except Exception:
            pass
    return out


def load_gallery(directory: str | None = None) -> list[Skill]:
    """The vendored template gallery, filtered to entries with an approved open license (see
    _APPROVED_GALLERY_LICENSES / anthill/skills_gallery/SOURCES.md). Uses the same `_load_dir()`
    every other skill source loads through, so a gallery entry is read exactly like any other
    folder skill - no separate parsing path to drift from the real format."""
    out = []
    for sk in _load_dir(gallery_dir(directory)):
        sk.tier = "gallery"
        if (sk.license or "").strip().lower() in _APPROVED_GALLERY_LICENSES:
            out.append(sk)
    return out


def _tokens(s: str) -> set:
    return {t for t in re.split(r"\W+", (s or "").lower()) if len(t) > 2}


def match_skills(skills: list[Skill], query: str, *, k: int = 2, min_score: int = 1) -> list[Skill]:
    """Return up to k skills most relevant to the query (keyword overlap)."""
    q = _tokens(query)
    if not q or not skills:
        return []
    scored = []
    for sk in skills:
        hay = _tokens(f"{sk.name} {sk.description} {sk.when_to_use}")
        score = len(q & hay)
        if score >= min_score:
            scored.append((score, sk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [sk for _, sk in scored[:k]]


def skills_system_prompt(skills: list[Skill]) -> str:
    """Format matched skills as a system-prompt addition. '' if none."""
    if not skills:
        return ""
    blocks = [f"## Skill: {sk.name}\n{sk.instructions}" for sk in skills if sk.instructions]
    if not blocks:
        return ""
    return (
        "You have the following skills available for this task. When a skill "
        "applies, follow its instructions:\n\n" + "\n\n".join(blocks)
    )


_DRAFT_SYS = (
    "Turn the user's request into an agent SKILL. Return ONLY a JSON object with "
    'keys: "name" (short label), "description" (one line), "when_to_use" (one line '
    'describing when this skill applies), and "instructions" (clear step-by-step '
    "guidance the agent should follow when the skill applies)."
)


def draft_skill(description: str, backend) -> dict:
    """Draft a skill's fields from a plain-language description (for the UI builder)."""
    desc = (description or "").strip()
    data = {}
    try:
        from ..inference.base import Message

        raw = json_chat(backend, [Message("system", _DRAFT_SYS), Message("user", desc[:2000])])
        data = extract_json(raw)
    except Exception:
        data = {}
    return {
        "name": coerce_str(data.get("name"))[:80] or desc[:60],
        "description": coerce_str(data.get("description")),
        "when_to_use": coerce_str(data.get("when_to_use")),
        "instructions": coerce_str(data.get("instructions")) or desc,
    }


_DISTIL_SYS = (
    "You turn a SUCCESSFUL agent run into a reusable agent SKILL: a general procedure the agent could "
    "reuse next time a similar goal comes up. Read the goal and the result. ONLY if there is a "
    'genuinely reusable, generalisable procedure, return a JSON object with keys: "name" (short '
    'label), "description" (one line), "when_to_use" (one line), "instructions" (clear, GENERAL '
    "step-by-step guidance - the reusable method, NOT the one-off specifics of this run). If the run "
    'is too trivial or one-off to generalise, return {"skip": true}. Return ONLY the JSON object.'
)


def distil_skill(goal: str, result: str, backend) -> dict | None:
    """Distil a reusable SKILL from a completed agent run (its goal + result). Returns the skill
    fields, or ``None`` when there is nothing worth generalising (or on any error - best-effort, so a
    distillation hiccup never affects the run it came from)."""
    try:
        from ..inference.base import Message

        body = f"Goal:\n{(goal or '')[:1500]}\n\nResult:\n{(result or '')[:2500]}"
        raw = json_chat(backend, [Message("system", _DISTIL_SYS), Message("user", body)])
        data = extract_json(raw)
    except Exception:
        return None
    if not data or data.get("skip"):
        return None
    name = coerce_str(data.get("name"))[:80]
    instructions = coerce_str(data.get("instructions"))
    if not (name and instructions):
        return None
    return {
        "name": name,
        "description": coerce_str(data.get("description")),
        "when_to_use": coerce_str(data.get("when_to_use")),
        "instructions": instructions,
    }


_REFINE_SYS = (
    "You are helping a user build (or edit) an agent SKILL through a short "
    "conversation. Read the dialogue so far. If ONE more detail would make the skill "
    "clearer or more useful, ask a single concise question and set ready=false. "
    "Otherwise produce the finished skill and set ready=true. Always keep a best "
    'current draft of every field. Return ONLY a JSON object with keys: "name", '
    '"description", "when_to_use", "instructions", "question" (your next question, '
    'or "" when ready), and "ready" (boolean).'
)


def refine_skill(messages: list, backend) -> dict:
    """One turn of a multi-turn skill builder. `messages` is the dialogue so far, a
    list of {"role": "user"|"assistant", "content": str}. Returns the current draft
    of every field plus a follow-up `question` and a `ready` flag. Never raises."""
    from ..inference.base import Message

    convo = [Message("system", _REFINE_SYS)]
    for m in (messages or [])[-12:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        convo.append(Message(role, str(m.get("content", ""))[:4000]))
    data = {}
    try:
        raw = json_chat(backend, convo)
        data = extract_json(raw)
    except Exception:
        data = {}
    return {
        "name": coerce_str(data.get("name"))[:80],
        "description": coerce_str(data.get("description")),
        "when_to_use": coerce_str(data.get("when_to_use")),
        "instructions": coerce_str(data.get("instructions")),
        "question": coerce_str(data.get("question")),
        "ready": bool(data.get("ready")),
    }


def skill_md(
    name: str,
    description: str,
    when_to_use: str,
    instructions: str,
    *,
    scopes: list | None = None,
    tier: str = "builtin",
    license: str = "",
    compatibility: str = "",
    allowed_tools: str = "",
) -> str:
    """Assemble a conformant agentskills.io SKILL.md (frontmatter + body). `name` is the display title;
    the conformant `name:` field is its normalised, folder-matching form. Anthill's governance/routing
    fields go under the `x-anthill-*` extension namespace. Used by write_skill and the wiki review gate.

    Conformance note: the current agentskills.io spec treats "when to use" as part of `description`,
    not a separate field - a skill-discovery tool that only reads the base `description` (ignoring
    Anthill's `x-anthill-when-to-use` extension) would otherwise never learn when a skill applies. So
    when both `description` and `when_to_use` are given, `when_to_use` is folded into the base
    `description` field ("<description> Use when: <when_to_use>"). `x-anthill-when-to-use` is still
    ALSO emitted - unchanged - since Anthill's own cheap keyword matcher (`match_skills`) and the UI
    both read it directly."""
    display = (name or "skill").strip()
    # agentskills.io requires a non-empty description (<= 1024 chars); fall back so we never emit an
    # invalid skill.
    description = (description or "").strip()
    when_to_use = (when_to_use or "").strip()
    if description and when_to_use:
        desc = f"{description} Use when: {when_to_use}"
    else:
        desc = description or when_to_use or f"The {display} skill."
    desc = desc[:_DESC_MAX]
    front = [f"name: {conform_name(display)}", f"description: {desc}"]  # standard base fields
    if license:
        front.append(f"license: {license.strip()}")
    if compatibility:
        front.append(f"compatibility: {compatibility.strip()[:500]}")
    if allowed_tools:
        front.append(f"allowed-tools: {allowed_tools.strip()}")
    # Anthill governance/routing extensions (base-standard tools ignore x-anthill-*).
    front.append(f"x-anthill-title: {display}")
    if when_to_use:
        front.append(f"x-anthill-when-to-use: {when_to_use.strip()}")
    front.append(f"x-anthill-tier: {tier or 'builtin'}")
    if scopes:
        front.append(f"x-anthill-scopes: {', '.join(scopes)}")
    return "---\n" + "\n".join(front) + "\n---\n\n" + (instructions or "").strip() + "\n"


def write_skill(
    name: str,
    description: str,
    when_to_use: str,
    instructions: str,
    *,
    scopes: list | None = None,
    directory: str | None = None,
    scope: str | None = None,
    team_id=None,
    user_id=None,
    assets: dict | None = None,
    license: str = "",
    compatibility: str = "",
    allowed_tools: str = "",
    db=None,
    org_id: int | None = None,
) -> Skill:
    """Create (or overwrite) a conformant agentskills.io skill as <dir>/<name>/SKILL.md - the folder is
    the normalised, folder-matching skill name. With `scope` given (personal|team|org) it is written into
    that scope's workspace skills dir and tagged with that tier; otherwise into the built-in skills dir.

    ``db``/``org_id`` are optional (#683 phase 7): when both are given AND the write is org-scoped
    (``scope`` truthy - a builtin skill has no org and is never registered), the DB registry
    (`anthill.web.knowledge_registry`) is kept in sync as the single choke point every skill write goes
    through. Best-effort: a registry hiccup never blocks the skill write, which has already succeeded
    on disk by that point (files are the source of truth; the registry is only an index)."""
    slug = conform_name(name)  # folder == the frontmatter `name:` (agentskills.io requires this)
    tier = "builtin"
    base = skills_dir(directory)
    if scope:
        from ..wiki.workspace import workspace_for

        ws = workspace_for(scope, team_id=team_id, user_id=user_id)
        if not ws.exists():
            ws.init()
        base, tier = ws.skills, scope
    folder = base / slug
    folder.mkdir(parents=True, exist_ok=True)
    text = skill_md(
        name,
        description,
        when_to_use,
        instructions,
        scopes=scopes,
        tier=tier,
        license=license,
        compatibility=compatibility,
        allowed_tools=allowed_tools,
    )
    path = folder / "SKILL.md"
    path.write_text(text)
    _write_assets(folder, assets or {})
    sk = parse_skill_md(text, slug=slug, path=str(path))
    sk.assets = _folder_assets(folder)
    if db is not None and org_id is not None and scope:
        try:
            from ..web.knowledge_registry import sync_skill

            item = sync_skill(
                db,
                org_id=org_id,
                scope=scope,
                team_id=team_id,
                slug=slug,
                title=sk.name,
                path=str(path),
                user_id=user_id,
            )
            sk.registry_id = int(item.id)
        except Exception:
            pass
    return sk


# A rough ceiling on a skill's instructions. Every skill `match_skills` selects for a task gets its
# full `instructions` body injected into that task's prompt (`skills_system_prompt`), so an
# oversized skill adds real, recurring cost to everything it matches. This is a WORD count, not a
# real tokenizer count - skills.py has no model-specific tokenizer dependency, so this is
# deliberately a rough heuristic (surfaced as a warning, never an error) rather than a precise budget.
INSTRUCTIONS_WORD_BUDGET = 1500


def validate_skill(sk: Skill) -> dict:
    """Local, in-app validation for a skill - the open agentskills.io validation rules are the
    reference (see docs/AGENT_SKILLS.md), not a shipped dependency (the spec's reference
    implementation is demonstration-only and is deliberately not vendored here). Checks naming,
    description, a rough instructions token-budget heuristic, and dangling [[asset]]-style
    references. Returns ``{"errors": [...], "warnings": [...]}``; only ``errors`` should block a
    save - callers decide that, this never raises and never writes anything itself.

    ``errors`` are limited to what the agentskills.io spec actually requires (a conformant name,
    since anything reaching here should already have been through ``conform_name()``) - deliberately
    NOT an empty description, since ``skill_md()`` already falls back to a generic one and existing
    skills created without a description must keep working. Everything softer is a ``warning``."""
    errors: list[str] = []
    warnings: list[str] = []

    name_reason = validate_skill_name(sk.slug)
    if name_reason:
        errors.append(f"Name: {name_reason}")

    if not (sk.description or "").strip():
        warnings.append(
            "No description set - agentskills.io wants one, and a generic fallback "
            "('The <name> skill.') will be used instead."
        )

    words = len((sk.instructions or "").split())
    if words > INSTRUCTIONS_WORD_BUDGET:
        warnings.append(
            f"Instructions are long ({words} words; a rough {INSTRUCTIONS_WORD_BUDGET}-word "
            "guideline, not an exact token count). Every task this skill matches pays that cost "
            "in its prompt - consider trimming, or moving reference detail into a bundled asset."
        )

    assets = set(sk.assets or [])
    refs = sorted(set(WIKILINK_RE.findall(sk.instructions or "")))
    dangling = [r for r in refs if r not in assets]
    if dangling:
        warnings.append(
            "References a bundled file this skill doesn't have attached: "
            + ", ".join(dangling[:5])
            + ". A skill has no other pages to link to - [[...]] here should name one of its own "
            "bundled assets, not a wiki page."
        )

    return {"errors": errors, "warnings": warnings}


_EXAMPLE_SKILL = {
    "name": "Effective wiki page",
    "description": "Write a clear, reusable knowledge-base page.",
    "when_to_use": "when asked to write, draft, or improve a wiki / knowledge-base page",
    "instructions": (
        "1. Open with a one-line summary of exactly what this page answers.\n"
        "2. Use short sections with descriptive headings so readers can scan.\n"
        "3. Prefer concrete steps, commands, and examples over prose.\n"
        "4. State assumptions and link related pages instead of repeating them.\n"
        "5. End with a short 'gotchas' or 'see also' list when useful.\n"
        "Keep it skimmable: a teammate should find the answer in under 30 seconds."
    ),
}


def seed_example(directory: str | None = None) -> bool:
    """Write one starter skill into the skills dir if it has none, so a fresh install isn't
    empty. Idempotent (skips when any skill already exists) and best-effort (never raises).
    Returns True if it seeded."""
    base = skills_dir(directory)
    try:
        if base.exists() and _load_dir(base):
            return False
        write_skill(
            _EXAMPLE_SKILL["name"],
            _EXAMPLE_SKILL["description"],
            _EXAMPLE_SKILL["when_to_use"],
            _EXAMPLE_SKILL["instructions"],
            directory=str(base),
        )
        return True
    except Exception:
        return False
