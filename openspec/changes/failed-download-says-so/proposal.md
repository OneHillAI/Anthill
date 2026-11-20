# A failed model download says so, in plain words, with a Try again

Full spec: `docs/specs/failed-download-says-so.md`. Issue: #330. Lane: `pillar:model`.

## Why

A failed download was silent and then showed "finished downloading and is now your local model" with the previous model's name.

## What changes

The server records a failed download (tag and a plain reason), `/models/pull-status` reports it, and `/models` and Settings show a
plain message with a Try again instead of the false "finished" banner. No tool output and no engine or version text is ever shown.

## Guardrails (do NOT touch)

Engine pin, catalogue rows, sizing, the pull command and progress parsing.
