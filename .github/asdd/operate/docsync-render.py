#!/usr/bin/env python3
"""Lay out the documentation agent's structured proposal as paste-ready blocks, and check them.

The agent returns data (an impact entry, a changelog line, doc sentences that are now untrue). It never
writes a heading, a diff, a PR number or a date: this script builds those from facts GitHub and git
supply, and checks everything the model said against the project's real rules:

- the impact-log heading uses the real PR number, title and merge date;
- the entry has the template's four fields, `user_visible` starts yes or no, `footprint` starts with one of
  additive, refactor, migration, plan-only;
- a changelog fragment is only wanted when product code (anthill/) changed, is named
  changelog.d/<PR>.<category>.md with an allowed category, and has no heading or leading dash;
- a doc edit is kept only if its file exists and its "now untrue" sentence is really in that file;
- any number in the text that does not appear in the PR (title, description, diff stat, paths) is flagged;
- en and em dashes (banned by the style gate) become plain hyphens.

The result file is model output: only known fields are read, each is length-capped, nothing is executed.

Usage: docsync-render.py --result FILE --ref SHA --pr N --title T --date YYYY-MM-DD
           --corpus FILE --changed FILE --impact-log FILE --model M [--root DIR]
Exit: 0 rendered, 3 the result had no usable proposal (nothing is printed to stdout).
"""

import argparse
import json
import os
import re
import sys

# Built from code points so this file never contains the characters the style gate bans.
EN_DASH, EM_DASH = chr(0x2013), chr(0x2014)
CATEGORIES = ("added", "changed", "deprecated", "removed", "fixed", "security")
FOOTPRINTS = ("additive", "refactor", "migration", "plan-only")
LIMITS = {"system_impact": 700, "surface": 320, "user_visible": 200, "footprint": 260}


def clip(value, limit=400):
    """Collapse whitespace, turn en and em dashes into hyphens (the style gate bans them), and cap length."""
    text = " ".join(str(value).split()).replace(EN_DASH, "-").replace(EM_DASH, "-")
    return text[:limit]


def load_payload(path):
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    payload = obj.get("payload")
    return payload if isinstance(payload, dict) else {}


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def numbers_not_in(texts, corpus):
    """Numbers the model wrote that the PR never mentions (a sign it made them up)."""
    missing = []
    for tok in sorted({t for text in texts for t in re.findall(r"\d+(?:[.,]\d+)*", text)}):
        if not re.search(r"(?<![\w.])" + re.escape(tok) + r"(?![\w])", corpus):
            missing.append(tok)
    return missing


def newest_month_heading(impact_log_text):
    for line in impact_log_text.splitlines():
        if re.match(r"^## \d{4}-\d{2}\s*$", line):
            return line[3:].strip()
    return None


def needs_changelog(changed_paths):
    """A fragment is wanted only when product code changed (tests, docs and CI alone do not)."""
    return any(p.startswith("anthill/") for p in changed_paths)


def build_impact(payload, pr, title, date, flags, corpus):
    imp = payload.get("impact") if isinstance(payload.get("impact"), dict) else {}
    fields = {k: clip(imp.get(k, ""), LIMITS[k]) for k in LIMITS}
    if not all(fields.values()):
        return None
    if not re.match(r"^(yes|no)\b", fields["user_visible"], re.I):
        flags.append(
            "The user-visible line should start with 'yes' or 'no'; fix it before pasting."
        )
    if not re.match(r"^(" + "|".join(FOOTPRINTS) + r")\b", fields["footprint"], re.I):
        flags.append("The footprint line must start with one of: " + ", ".join(FOOTPRINTS) + ".")
    bad = numbers_not_in(list(fields.values()), corpus + f" {pr} {date}")
    if bad:
        flags.append(
            "These numbers are not in the PR, so the agent may have invented them: "
            + ", ".join(bad)
            + "."
        )
    heading = f"### PR #{pr} - {clip(title, 100) or 'merged change'} - merged {date}"
    return "\n".join(
        [
            heading,
            f"**System impact:** {fields['system_impact']}",
            f"**Surface:** {fields['surface']}",
            f"**User-visible:** {fields['user_visible']}",
            f"**Footprint:** {fields['footprint']}",
        ]
    )


def build_changelog(payload, pr, changed_paths, flags):
    if not needs_changelog(changed_paths):
        return "No changelog fragment needed: no product code (`anthill/`) changed.", None
    cl = payload.get("changelog") if isinstance(payload.get("changelog"), dict) else {}
    category = str(cl.get("category", "")).strip().lower()
    text = str(cl.get("text", "")).strip()
    if category not in CATEGORIES or not text:
        return (
            "A changelog fragment is needed (product code changed) but the agent did not propose a usable one.",
            None,
        )
    body = clip(text, 700)
    if body[:1] in "-#*":
        flags.append("The changelog text started with a heading or bullet marker; it was stripped.")
        body = body.lstrip("-#* ").strip()
    return (
        f"Create `changelog.d/{pr}.{category}.md` containing exactly this (no heading, no leading dash):",
        body,
    )


def build_doc_edits(payload, root, flags):
    out = []
    edits = payload.get("doc_edits") if isinstance(payload.get("doc_edits"), list) else []
    for e in edits[:5]:
        if not isinstance(e, dict):
            continue
        path = str(e.get("path", "")).strip()
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/") or ".." in path.split("/"):
            flags.append("Dropped a proposed edit: its path was not inside the repository.")
            continue
        sentence = clip(e.get("sentence", ""), 400)
        fix = clip(e.get("fix", ""), 400)
        if not path.endswith(".md") or ".." in path.split("/") or not sentence or not fix:
            continue
        text = " ".join(read(os.path.join(root, path)).split())
        if sentence not in text:
            flags.append(f"Dropped a proposed edit to `{path}`: its sentence is not in that file.")
            continue
        out.append((path, sentence, fix))
    return out


def render(payload, a, flags=None):
    flags = [] if flags is None else flags
    corpus = read(a.corpus) + " " + a.title
    changed = [ln.strip() for ln in read(a.changed).splitlines() if ln.strip()]
    entry = build_impact(payload, a.pr, a.title, a.date, flags, corpus)
    if entry is None:
        return None
    month = newest_month_heading(read(a.impact_log))
    where = (
        f"directly under the `## {month}` heading"
        if month
        else "at the top of the newest month section"
    )
    cl_note, cl_text = build_changelog(payload, a.pr, changed, flags)
    edits = build_doc_edits(payload, a.root, flags)
    ref7 = a.ref[:7]
    lines = [
        f"## Documentation agent - proposed doc updates for PR #{a.pr} (`{ref7}`)",
        "",
        f"Apply by hand: each block is plain text to copy in. The agent edited nothing. Role: documentation, model `{a.model}`.",
        "",
        "### 1. Impact log (`docs/SYSTEM_IMPACT_LOG.md`)",
        f"Paste this {where}, with a blank line after it:",
        "",
        "```",
        entry,
        "```",
        "",
        "### 2. Changelog",
        cl_note,
    ]
    if cl_text:
        lines += ["", "```", cl_text, "```"]
    lines += ["", "### 3. Docs that are now untrue"]
    if edits:
        for path, sentence, fix in edits:
            lines += [
                f"In `{path}`, replace:",
                "",
                "```",
                sentence,
                "```",
                "",
                "with:",
                "",
                "```",
                fix,
                "```",
                "",
            ]
    else:
        lines.append("None found.")
    if flags:
        lines += ["", "### Check before pasting"] + [f"- {f}" for f in flags]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in (
        "result",
        "ref",
        "pr",
        "title",
        "date",
        "corpus",
        "changed",
        "impact-log",
        "model",
    ):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    out = render(load_payload(a.result), a)
    if out is None:
        sys.stderr.write("docsync-render: the result had no usable impact entry\n")
        return 3
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
