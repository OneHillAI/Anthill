# Chat: one working indicator, in the reply bubble, with the ants wearing capes for the provider

## Problem

While a reply was being written the chat showed two indicators at once: a trail of pulsing dots and a small
anthill inside the empty reply bubble, and a full-width "ant street" strip above the message box with six
tiny ants, a second anthill and the only line that said what was really happening ("Thinking, locally.",
"Searching the web...", "Checking with Infercom..."). The two used different rhythms and different hills, the
words sat away from where the answer appears, and the strip appeared and disappeared each turn, moving the
message box.

## Fix

One indicator, inside the reply bubble, where the answer will appear:

- Three small ants run toward a small anthill, with the honest status line beside them. The line is still set
  only from real signals (the stream's stage events), never a timed script.
- While the attached inference provider answers, the same ants swap to the caped symbol and glow warm, and
  the line turns warm and names the provider ("Checking with Infercom..."). This covers a turn that escalates
  on its own, "Ask {Provider} instead" and the confirm card; where there is no streaming bubble (the confirm
  card) the row appears inside that card.
- As words arrive they appear above the row. The row is not re-created, so the ants keep running while text
  streams in. When the answer is finished the row is gone.
- The strip above the message box and the pulsing dots are removed.
- With reduced motion the ants stand still in a row; the cape and the warm colour still show.
- The Thinking button is highlighted while Thinking is on, matching Web search (it was highlighted while off).

## Acceptance criteria

- There is no element above the message box while a reply is being written.
- During a reply the assistant bubble holds one row: three ants, a small anthill and the status line.
- When the stream reports the provider is answering, the ants use the caped symbol, the row gets the warm
  style and the line reads "Checking with {Provider}...".
- Streaming text does not replace the row: the same ant elements stay in the page while words arrive.
- After the answer is complete there is no working row in the page.
- `Thinking on` is highlighted, `Thinking off` is not, as with Web search.
- Each working row keeps its own state (capes and status line): a provider call waiting in a confirmation card and a local turn being written never read or overwrite each other's row.
- After "Always" or "Just this once", the confirmation card's line changes to "Sending this question to {Provider}" before the working row appears; it no longer says nothing has left the device.
