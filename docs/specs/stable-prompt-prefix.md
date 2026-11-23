# Spec: the start of the prompt is the same from turn to turn

Status: implemented. Lane: `pillar:knowledge`. Issue: #343. The wiki answer path (`anthill/wiki/prompts.py`, used by `ask()` and
`ask_stream()`).

## Problem

A local model answers a follow-up quickly when the start of the prompt is identical to the last turn, because the engine keeps what it
already read. The prompt put one reference block, which holds memory, principles, profile and the pages found for this question,
in front of the earlier turns. That block changes on almost every question, so the engine read the earlier turns again each time.

## Requirements

1. **The order.** system message, the guidance block, the earlier turns, the reference for this question, the question last. The
   guidance block holds the principles and the profile; the reference holds memory, then the pages and passages. A block is left out
   when it is empty, so a turn with no guidance, no history or no reference keeps the order of those that remain.
2. **Only the order changes.** Every block has the same text and labels as before; both blocks stay user-role, fenced, untrusted
   blocks (nothing moves into the system message); retrieval, the pack budget, memory, history summarising and the web-search messages
   are untouched. The hijack retry still carries guidance and reference in one fenced block and ends with the fenced request.
3. **The guidance block is identical from turn to turn.** It holds nothing that changes with the question, so two turns that differ only
   in the question and the pages found share their first messages (system, guidance, earlier turns).
4. **The fit to the model's window counts the guidance as fixed cost.** The reference and the earlier turns are shortened as before; a
   prompt that fits is not shortened.
5. `ask()` and `ask_stream()` both use the new order.

## Known limits

- How much faster a follow-up is depends on the engine reusing a partly matching prompt. This is not measured here (see below).
- The text inside the hijack-retry block lists guidance before memory, where memory used to come first. That path is rare.

## Acceptance

`tests/test_stable_prompt_prefix.py`: the order with and without guidance, history and reference; the guidance text equals the text it
replaces and is absent when empty; both blocks are fenced user-role blocks and the system message holds neither; the hijack retry; the
fit counts the guidance and a prompt that fits is unchanged; through `ask()` and `ask_stream()` the guidance block is byte-identical
across two turns with other pages and a longer history, and the first messages of both prompts match. Two older tests that asserted the
old order were updated.

## Not verified here

The time to first word and the prompt tokens read per follow-up, before and after, on the shipped engine, and the answers to the
12-question bakery set before and after. Both need the engine running and are reported separately.
