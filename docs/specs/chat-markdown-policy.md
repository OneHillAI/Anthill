# Spec: Chat renders answers under a strict Markdown policy

Status: implemented. Lane: `pillar:privacy`. Follow-up to the review of #89.

## Problem

Chat turns an answer's Markdown into HTML with DOMPurify's default settings. Answers mix model text with web
and document content, so injected text can use what the defaults still allow: a remote image loads the moment
the answer renders and carries data to any host in its address, and a form, button, style element or inline
style can restyle the page, fake a prompt or submit a same-origin request. An image tag can also request any
route of the app itself with the user's session. This breaks the rule that prompts, answers and documents stay
inside the organisation's perimeter.

## Requirements

- Sanitise rendered answers with an allow-list. Tags: `p`, `br`, `hr`, `h1` to `h6`, `strong`, `em`, `del`,
  `code`, `pre`, `blockquote`, `ul`, `ol`, `li`, `table`, `thead`, `tbody`, `tr`, `th`, `td`, `a`, `img`.
  Attributes: `href`, `title`, `align`, `start`, `src`, `alt`. `align` and `start` are plain words and numbers,
  so they are named as URI-safe; otherwise DOMPurify tests them against the link pattern and strips them.
  There is no `id`, `class`, `name`, `style`, `data-*` or `aria-*`, and no `form`, `input`, `button`,
  `textarea`, `select`, `style`, `iframe`, `video` or `audio`.
- An image is kept only when its address is exactly a file this app serves: `/files/<name>`, where the name
  uses letters, digits, `.`, `_` and `-` and is not `.` or `..`, and the address resolves to this origin. Any
  other address is not an image: a Markdown image shows as a link to its address, so nothing is fetched until
  the reader clicks, and a raw `<img>` is removed. This closes remote hosts, `//host`, `/\host` (browsers read
  the backslash as a slash) and every other app route, such as `/logout` or an export, which an image tag
  would otherwise request with the user's session.
- Links keep only `http:`, `https:`, `mailto:` and site paths with a single leading slash that is not followed
  by `/` or `\`. They open in a new tab with `rel="noopener noreferrer"`. In-page `#` links are dropped because
  answers have no ids to point at.
- Bare `/files/<name>` paths in the text become links before sanitising, so the sanitiser always sees the final
  markup and nothing edits the HTML after it has been cleaned.
- The inline file preview and the Canvas open only for an address that is exactly `/files/<name>`. The
  preview and its button are built with DOM calls, not from strings that contain the address.
- The policy applies from the first render of a loaded conversation, a streamed answer, the final render and
  the echoed user message.
- If `marked` or DOMPurify fails to load, the text is shown escaped, as before.
- No change to what is stored or sent to the model.

## Acceptance criteria

- A conversation whose answers hold a remote Markdown image, a raw remote `<img>`, `/\host` images in both
  forms, an image of `/logout`, a form with a button and an input, a `<style>` element, a `style` attribute and
  an element with an `id` makes no request to another host and no request to an app route for an image, shows
  none of those elements, and leaves the real chat message list as the element with id `messages`.
- A link whose address is `/files/` followed by quote characters or dot segments gets no preview and runs no
  script. Markup inside an attribute value cannot split that attribute.
- A local image `/files/pic.png` still shows. Headings, lists, tables, code and links still render, an
  ordered list that starts at 3 starts at 3, and a right-aligned column stays right-aligned.
- A streamed answer, its final render and the echoed user message follow the same rules.
- Previews for history appear once the separate fix `chat-history-file-links` is in place; before it, a
  history that links a `/files/` path stops the page script at start-up (an existing problem on main).

## Proof

- `tests/browser/test_chat_markdown.py` seeds the cases above in separate answers, serves a stream for the
  live case, and asserts the requests made, the elements that remain and the absence of script errors other
  than the existing start-up error named above.
