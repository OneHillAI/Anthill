# Chat: switches that look like switches, and answers that say where they ran

Lane: `pillar:platform`

## Problem

Under the chat box, Web search and Thinking are on/off controls but looked like plain outlined buttons, unlike the
switches used in Settings, so the pressed state was easy to miss. Under each answer the place it ran was a laptop or
cloud emoji followed by "local" or "remote", which is easy to misread and does not match the plain wording used elsewhere.

## Fix

1. **The two on/off buttons** (Web search and Thinking) draw a small switch (track and knob) before the label, using
   the accent colour when on, and keep the state in the label ("Web search off", "Thinking on"). They keep their
   ids, labels and `aria-pressed`. The knowledge picker is not an on/off control and has no switch. The knob does not
   animate when the viewer prefers reduced motion.
2. **Where an answer ran** is a small chip under the answer, on a saved conversation and on an answer that has just
   streamed: "On this device" (accent) when nothing left the machine, "Sent over the network" otherwise. The model
   name is in the hover text of the second. An escalated answer keeps its "expert" badge.

Not done here: a "Show sources" link and a per-answer model chip. The page already lists the wiki pages the model was
given ("Pages in context"); naming the pages an answer actually relied on needs the backend to report it.

## Acceptance criteria

- The chat page styles `.opt-toggle[aria-pressed]` as a switch that changes with `aria-pressed`, and the labels
  still read "Web search off/on" and "Thinking off/on".
- A local answer shows "On this device"; a remote one shows "Sent over the network"; neither uses an emoji.
- `tests/test_chat_switches_and_answer_source.py`, `tests/test_chat_mlx_stream.py`.
