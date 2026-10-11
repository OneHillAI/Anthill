# Chat: the "check with your inference provider" offer is one quiet button

Lane: `pillar:model`
Relates to: `docs/specs/manual-escalation-always-offered.md` (R1 stays; R2 changes as below).

## Problem

After every answer, for an account with an inference provider attached that has not yet chosen "Always", the
chat opened a card: "This needs more than your local model. Send this question to Infercom?" with three buttons.
It appeared after every reply, including "hi" and a capital-city question, where the claim is plainly untrue.
Nothing left the device (the person declined each time), but the card is noise, and it asked for a decision
about trusting the provider on every single turn.

## Fix

- The server is unchanged: it still offers the check after every eligible local answer (R1 of the spec above),
  and the offer still says whether the person has already chosen "Always".
- The page now shows that offer as ONE quiet button under the answer, "Check with {Provider}", in the same
  low-key style as "Ask {Provider} instead". It looks the same whether or not the person has chosen "Always".
- A click on it behaves as before: if the person already chose "Always", the answer is checked at once; if
  not, the question that asks to approve sending anything onward (Always / Just this once / Not now) opens in
  place of the button, and keyboard focus moves to its first choice. Nothing is sent before that click.
  Until the person has chosen "Always", the question comes back on the next answer.
- The card no longer says "This needs more than your local model": the offer does not claim the answer was not
  good enough.
- **One check per offer.** Once the check starts the button is removed (the working row takes its place) and a
  second activation sends nothing more, so a double click or Enter-then-Space cannot send two requests to the
  provider while it answers.
- **After a successful check:** the expert answer is added to the answer and the offer goes away. If the person
  chose "Always" in the question, the page remembers it (as the other provider controls do). The answer's
  "Redo with {Provider}" control is removed, since it would only repeat the check; and checking through "Redo
  with {Provider}" removes this answer's offer.
- **Automated mode's grader.** In Automated mode, an account that has not chosen "Always" used to get this
  offer, with a "needs more" card, only when the grader flagged the answer. The offer now looks the same whether
  or not the grader flagged it, so the grader's flag has no visible effect for such an account. (An account that
  chose "Always" in Automated mode still has the grader escalate silently, as before.)
- The button is a real `<button>`: keyboard activation (Enter and Space) works, it has an underline and a
  24 px minimum height, its emoji is hidden from screen readers, its hover colour uses `--warn-text`, and the
  consent question is a labelled group. In the dark theme the "Always" button uses dark text for contrast.
- The offer lives in the answer's own message and is removed on its own, so checking or dismissing it never
  removes the answer.

## Known limits (pre-existing, not changed here)

- `/chat/{id}/escalate-confirm` does not re-check that the account has consented, or that the provider still
  matches `offer.provider`, when it is called; and a click on the button of an already-consenting account sends
  `remember=true`.

## Acceptance criteria

- After an answer with an offer, the page shows one button "Check with {Provider}" and no card, no three
  buttons and no "needs more than your local model" text, and no request has been sent.
- Before the person has chosen "Always": clicking it opens the three-choice question and moves focus into it;
  "Not now" removes the button and sends nothing, and the answer stays.
- "Just this once" sends `remember=false`; "Always" sends `remember=true` and the page then treats the account as
  having consented.
- After the person has chosen "Always": clicking it checks the answer straight away (`remember=true`) and the
  expert answer is added; the answer stays.
- Activating the button twice before the provider answers sends exactly one request.
- A failed check shows the reason (or "could not reach the server") and leaves the answer in place.
- The server still sends the offer after every eligible answer (existing tests unchanged).
