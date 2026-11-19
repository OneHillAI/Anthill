# Spec: an upload that looks like a newer version of a page asks the person

Status: implemented. Lane: `pillar:knowledge`. Builds on `149-duplicate-uploads.md`.

## Problem

People upload "Pricing 2026" when the wiki already has "Pricing 2025". The titles differ, so nothing matched and
the wiki ended up with two near-identical pages and no one deciding which one was current.

## Fix

1. An uploaded page is compared with the existing pages of the wiki it is going to. When no page has the same
   slug, a page is a possible newer version when its title is the same after removing numbers, version words
   (final, draft, copy, new, old, updated, revised, version, rev, latest, `vN`) and bracketed text, and its text
   (front matter and comments ignored) is at least 60 percent the same. "Roadmap Q3" and "Roadmap Q4" with
   different text are not asked about.
2. A match is held like a duplicate: nothing is written, the page becomes a pending review under the existing
   page's slug with the flag `looks_newer` and a one-sentence proposal.
3. The review card says "Is this a newer version of X?", shows the old and the new text side by side with the
   changed words marked, and offers, before the text box: "Yes, replace X" (approve), "No, keep both" (keep
   both) and "Keep the old page, discard this" (reject). The uploaded file stays under Files either way.
4. "Keep both" files the new page under its own title, without a date, because its title is already different.
5. The suggestions inbox and the upload status use the same words ("Held upload - may be a newer version").

## Acceptance

- Same title apart from numbers and mostly the same text: held as `looks_newer`, not written.
- Different text under a similar title, or the same text under another title: not held.
- The comparison marks the changed words (2025 to 2026, 19 to 24).
- Replace writes the new text over the old page; keep both leaves two pages; discard leaves the old page.
