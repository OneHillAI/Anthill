# Spec: Task results render as safe Markdown

Status: implemented. Lane: `pillar:feature`. Issue: #89.

## Problem

The task result page shows the latest result and every history entry as raw pre-wrapped text. Task output is
usually Markdown, so headings, lists, tables and links show as punctuation. Results that carry a
double-escaped Unicode sequence such as `\\u00b7` show it literally instead of as the character.

## Requirements

- Render the latest result and each successful history entry as Markdown (headings, emphasis, lists, code,
  tables, links) with the self-hosted `marked`, and always sanitise the HTML with the self-hosted DOMPurify.
  No new dependency.
- Task output is written unattended from web, email and other content and is shown to everyone who can see
  the task, so this page uses a stricter policy than Chat. Only these tags survive: `p`, `br`, `hr`,
  `h1` to `h6`, `strong`, `em`, `del`, `code`, `pre`, `blockquote`, `ul`, `ol`, `li`, `table`, `thead`,
  `tbody`, `tr`, `th`, `td`, `a`. Only `href`, `title`, `align` and `start` attributes survive (`align` and `start` are plain words and numbers, so they are named as URI-safe; otherwise DOMPurify tests them against the link pattern and strips them), so there is no
  `id`, `class`, `name`, `style`, `data-*` or `aria-*`. Nothing that loads a resource, restyles the page or
  takes input survives: no `img`, `video`, `audio`, `style`, `form`, `input`, `button` or `textarea`.
- A Markdown image is shown as a link to its address (its alt text is escaped once), so nothing is fetched until the reader clicks. Task
  list checkboxes are dropped.
- Links keep only `http:`, `https:`, `mailto:` and site paths with a single leading slash that is not followed
  by `/` or `\`. They open in a new tab with `rel="noopener noreferrer"`. In-page `#` links are dropped
  because results have no ids to point at.
- If `marked` or DOMPurify fails to load, the text stays as plain pre-wrapped text.
- Display a double-escaped `\\uXXXX` sequence (exactly two backslashes, a lowercase `u` and four hex
  digits, with a surrogate pair read as one character) as that character. Do not touch a single `\uXXXX`,
  `\\U`, three or more backslashes, or a lone surrogate: those are the author's own text. A code point that is
  a control, format, line-separator or paragraph-separator character (NUL, ESC, bidi overrides, zero-width
  space, byte order mark, a Unicode tag character written as a surrogate pair) stays as the visible escape, because decoding it would hide or reorder text. The
  zero-width joiner and non-joiner decode, because emoji sequences and some scripts need them. The decode
  runs on the whole result text, so it also applies inside code spans and code blocks.
- A failed run, and a latest result that starts with `ERROR:`, stays plain text in an error block with the
  `error` badge, so it is never read as Markdown or as a success.
- The stored result is never modified. "Save as snippet" sends the stored text, not the rendered text and not
  the Unicode-decoded text.
- Markdown reads `\\` as an escaped backslash, so a Windows network path such as `\\server\share` written in
  prose shows with one leading backslash. This is accepted. Inside a code block it is shown as written.
- No change to how results are stored or generated.

## Acceptance criteria

- The latest result and history entries show formatted headings, lists, tables, code and links.
- A result containing `<script>`, an `onerror` attribute and a `javascript:` link renders none of them as
  active content, and none of them runs.
- A result containing a Markdown image, a raw `<img>`, a form with a button and an input, a `<style>` element,
  a `style` attribute and an element with an `id` shows none of them as such. The page makes no request to any
  other host, keeps its own styling, and "Save as snippet" still sends the stored text.
- `\\u00b7` in a result shows as the middle dot, and `C:\users\me` and a single `\u00b7` show unchanged.
- A failed run shows its text literally, with the `error` badge.
- The snippet request carries the stored Markdown source with the escapes unchanged.

## Proof

- `tests/test_task_text.py` covers the decode rule: double-escaped sequences, surrogate pairs, a lone
  surrogate, single and triple backslashes, `\\U`, control and bidi characters, joiners and non-hex input.
- `tests/browser/test_task_result_markdown.py` covers formatted output for the latest result and history,
  injection attempts, the strict policy (no outside request, no image, form, style or id), the Unicode rule,
  plain error output, and the snippet payload.
- `tests/test_task_run_history.py` covers a latest result that starts with `ERROR:`.
