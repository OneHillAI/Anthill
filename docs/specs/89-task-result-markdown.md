# Spec: Task results render as safe Markdown

Status: implemented. Lane: `pillar:feature`. Issue: #89.

## Problem

The task result page shows the latest result and every history entry as raw pre-wrapped text. Task output is
usually Markdown, so headings, lists, tables and links show as punctuation.

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
- A failed run, and a latest result that starts with `ERROR:`, stays plain text in an error block with the
  `error` badge, so it is never read as Markdown or as a success.
- The stored result is never modified. "Save as snippet" sends the stored text, not the rendered text.
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
- A failed run shows its text literally, with the `error` badge.
- The snippet request carries the stored Markdown source exactly as stored.

## Proof

- `tests/browser/test_task_result_markdown.py` covers formatted output for the latest result and history,
  injection attempts, the strict policy (no outside request, no image, form, style or id), plain error output, and the snippet payload.
- `tests/test_task_run_history.py` covers a latest result that starts with `ERROR:`.

## Not in this change

A result that contains a doubled `\\uXXXX` escape (for example `\\u00b7`) is shown as it is stored, apart from
Markdown's own rule that a doubled backslash is one backslash. Decoding it is left for a separate change at the
source, which is probably the `json.dumps` serialisation of tool results in `anthill/agent/executor.py`. That
change would alter what the model is given for every tool call, so it is not made here, and no date is
promised for it.
