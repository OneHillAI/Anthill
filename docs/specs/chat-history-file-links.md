# Spec: A chat with file links in its history loads and can send

Status: implemented. Lane: `pillar:feature`.

## Problem

Opening a conversation whose stored history links a `/files/` path (an answer that pointed at a file) stopped
the chat script part-way. The inline preview never appeared, and the message input was never initialised, so
the chat could not send after a reload. The script read the list of previewable file types before it was
declared. A file link that appeared while streaming was fine, because the list existed by then.

## Requirements

- Declare the list of previewable file types before the first preview pass over loaded history.
- A conversation whose history links `/files/` paths loads without a script error, shows an inline preview
  under each previewable link (none under a file type that is not previewable), and can send a message.
- No change to which file types are previewable or to how previews look.

## Acceptance criteria

- A conversation with a stored answer linking `/files/report.pdf` and `/files/pic.png` loads with no page
  error, shows a frame under the PDF link and an image under the other and no preview under a `.md` link, and a sent
  message appears with its answer.

## Proof

- `tests/browser/test_chat_history_file_links.py` seeds that conversation, fails with a TypeError before the
  change, and passes after it.
