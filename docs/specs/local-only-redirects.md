# Spec: Redirects stay on this site

Status: implemented. Lane: `pillar:platform`.

## Problem

Four places send a member to a page named in the request: the `next` field of the wiki review and skill
forms (`_local_or`), the `next_url` field of the password form (which had its own inline copy of the check),
and the `Referer` of the pin and rename actions (`pin_conversation`, `_back_to_local`). The check only
required a leading slash and no second slash. The case that really left the site was the `Referer` of pin and
rename: its path was kept and only the host dropped, so a `Referer` path of `//host` redirected to `//host`.
A backslash or a control character did not reach the browser as such (Starlette percent-encodes the
`Location` header, so `/\host` is sent as `/%5Chost`), but the helper now refuses both, so no such value is
ever stored, templated or encoded into a redirect at all, and the password form no longer carries its own copy
of the check.

## Requirements

- One helper decides what a local path is (`_is_local_path`): it starts with a single `/`, has no scheme and no
  host when parsed, no backslash and no control character (code below 0x20 or 0x7f).
- `_local_or(next, default)` uses it, and the password form calls `_local_or` instead of its own check.
- `pin_conversation` and `_back_to_local` redirect to the `Referer`'s path and query only when that path is a
  local path; otherwise to the fallback (the query is dropped with it). `pin_conversation` uses
  `_back_to_local`.
- Nothing changes for an ordinary local path with a query string or fragment.

## Acceptance criteria

- A table of hostile values (`//host`, `///host`, `/\host`, `\host`, tab or newline or NUL or DEL inside,
  `http://`, `https://`, `javascript:`, `data:`, a bare host, empty) is rejected by `_is_local_path` and
  `_local_or` returns the default.
- The password form with each hostile `next_url` redirects to the account page.
- Pin and rename with a hostile `Referer` (`//host`, a backslash, a bare host, none) redirect to `/chat`.
- Pin and rename with `https://host/chat/history?q=plan&page=2` redirect to `/chat/history?q=plan&page=2`.
- The tasks `_back_to_local` caller uses the same rule.

## Tests

`tests/test_local_redirects.py`.
