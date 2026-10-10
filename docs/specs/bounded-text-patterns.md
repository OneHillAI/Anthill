# Spec: Text patterns that read member-supplied text stay linear

Status: implemented. Lane: `pillar:platform`.

## Problem

Several regular expressions read text a member controls (a chat message, a wiki page, a skill, an uploaded
document). Four of them had a cost that grows with the square of the input: a long run of whitespace or of
`[[` made one request keep the server busy for seconds. The cause in each case is two parts of the pattern that
can claim the same characters, so the engine retries every split.

## Requirements

- `looks_like_remember` and `parse_remember` (`agent/intent.py`): the message is stripped before matching, so
  the pattern's two whitespace runs cannot overlap on trailing whitespace.
- The "how do/does/did/can/would ... relate/affect/..." branch of the deep-question pattern: `\s+.+` becomes
  `\s+\S.{0,200}?`, so the gap between the two phrases is at most 200 characters. This is a behaviour change:
  a question whose gap is longer no longer routes to the deep agent (it is answered by the fast path). A test
  pins the limit.
- Wiki links `[[...]]` share one pattern, `WIKILINK_RE` in `common/text.py`: one line, 1 to 200 characters, no
  brackets inside. It replaces the unbounded copies in the page review (`wiki/review.py`), the OKF bundle
  writer (`wiki/okf.py`), ingest (`wiki/ingest.py`, four uses), the shared text helper itself and the skill
  validator (`agent/skills.py validate_skill`, reachable by any member through `/skills/validate`).
- Markdown links `[label](target.md)` in `outbound_links` (`_MDLINK`, `common/text.py`): the label is one line
  of at most 200 characters with no brackets, the target at most 500 characters with no parentheses or
  newline. The unbounded version took 3.9 s at 80 KB of `[`.
- The skill front matter pattern (`agent/skills.py parse_skill_md`): `^---\s*\n` becomes `^---[ \t]*\r?\n`, so
  newlines can no longer be claimed by both the opening and the body; the closing line accepts `\r\n`.
- Behaviour on ordinary input is unchanged. A link longer than 200 characters, spanning lines, or containing a
  bracket is no longer treated as a link (none of those is a page title).

## Acceptance criteria

- Each pattern answers in under a second on 40 000 to 100 000 characters of hostile input (whitespace, `[[`,
  repeated lead-ins, newlines); the unbounded versions take many seconds on the same inputs.
- Ordinary messages, links, skills and CRLF front matter parse as before.
- The page review, the OKF writer, ingest and the skill validator use the shared pattern (a test scans every module of the package and fails on any other pattern that mentions `[[`).
- `/skills/validate` answers a member's hostile text promptly.

## Tests

`tests/test_bounded_patterns.py`.
