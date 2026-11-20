# A catalogue row names the exact build it pulls; old plain-tag choices keep working

Full spec: `docs/specs/catalogue-replaced-tags.md`. Issue: #324. Lane: `pillar:model`.

## Why

A plain tag means different builds (and memory costs) on different engines; a row must be able to name the exact build
without breaking installs that chose the plain tag.

## What changes

An optional `replaces` list per row, one helper set (`row_for_tag`, `row_tags`, `row_installed_tag`) used wherever a tag is
matched against a row, a first-claim-wins rule on clashing tags. No shipped row changes.

## Guardrails (do NOT touch)

Engine pin and version, the pull path, cloud and organisation sizing, the scores, any shipped catalogue row. No new text.
