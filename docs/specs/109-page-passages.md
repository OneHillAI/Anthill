# Spec: Send the best passages of a long wiki page, not the whole page

Status: implemented. Lane: `pillar:knowledge`. Part 2 of issue #109. Builds on `docs/specs/109-model-window-fits-reference.md`.

## Problem

A retrieved page goes into the prompt whole, and the local model reads every token of it before the first word
appears. On a laptop that is about 165 tokens a second, so each 1,000 tokens costs about 6 seconds. Most of a long
page is not about the question.

## Requirements

- A page of 2,000 characters or less goes into the reference whole and unchanged. So does a long page that splits
  into no more than two passages.
- A longer page is split into passages of about 800 characters, packed from whole paragraphs. A fenced code block
  is not split at a blank line. A paragraph or block longer than a passage is cut at line ends, and each piece of
  a long code block keeps the opening and closing fence. A passage that does
  not begin with a heading gets the nearest heading above it as its first line, so it stands alone.
- Passages are ranked against the question by word overlap (BM25): no model call, and about a millisecond for a
  page. Terms are words of two or more letters or digits (so `HR` and `QA` count), lower-cased, without a short
  list of English stop words; text in scripts written without spaces (Chinese, Japanese, Korean, Thai, Lao,
  Myanmar, Khmer) is ranked by pairs of neighbouring characters, since it has no word breaks. Other alphabets
  (Cyrillic, Greek, Arabic and so on) rank by their words.
- The two best passages are kept, in their original order, after the page's `# ` title line, with a line `[...]`
  where text was left out. Two passages that were next to each other in the page are joined as they were, with no
  marker between them. When no word of the question appears in any passage, the first passages of the page
  are kept.
- One helper (`anthill/wiki/passages.py`, `build_reference`) builds the reference text for both the blocking
  `ask()` and the streaming `ask_stream()`. Pages are joined with the same separator as before, front matter is
  removed as before, and the pages chosen and the slugs reported are unchanged.
- The fit guard of Part 1 still applies afterwards, so a prompt that is still too long is shortened further.

## Limits, stated on purpose

- It only helps pages over 2,000 characters. A wiki of short pages (for example model-written summaries of a few
  hundred characters) sees no change, and no saving.
- Ranking is by shared words only. A question that uses different words from the page can keep the wrong
  passages. Passage vectors in the page index would rank better and can follow; page vectors today cover only the
  first 2,000 characters of a page.
- A page is not marked with its slug inside the reference, as before.
- Multi-turn chats: the passages depend on the question, so the reference changes from turn to turn, and a model
  server cannot reuse its cached start of the prompt across turns the way it can for an unchanged whole page.
  Measured (below), this did not make a follow-up slower than whole pages. Part 1 of #109 still applies on top.

## Multi-turn measurement

Five consecutive questions about the same long page, with the earlier turns passed as history, through the real
`ask_stream` path (qwen3.5:9b, Thinking off, Apple M4 16 GB, Ollama 0.24.0, the model kept loaded between turns,
the first word timed):

| Wiki | Whole pages (Part 1 alone) | Best passages (this change) |
|---|---|---|
| 187 documents (the companion pages change from turn to turn) | 16.6 to 24.4 s per turn, median 22.0 s | 8.6 to 11.6 s per turn, median 9.6 s |
| 3 documents (the same three pages come back each turn) | 11.4 to 24.1 s per turn, median 22.4 s | 8.5 to 12.6 s per turn, median 9.5 s |

Whole pages were reused from the server's cache only on two turns of the second run (11.4 s and 12.4 s), when the
same pages came back in the same order. On every turn the passages were as fast or faster. Main, which sent no
window, dropped the reference on most turns (prompts of 369 to 1,006 tokens) and answered in 2 to 4 s without the
wiki. One run per variant, one model, one page: read it as a trend.

## Acceptance criteria

- A page of 2,000 characters or less reaches the prompt byte for byte unchanged.
- For a long page, a fact placed deep inside it reaches the prompt when the question asks about it.
- The title line and the order of the kept passages are preserved; the pages chosen and the slugs reported to the
  interface are unchanged.
- The prompt for a long page is shorter, with the before and after prompt tokens and first-word times measured on
  the real code path with qwen3.5:9b and Thinking off (in the pull request).
- The pull request states the limit above.

## Proof

- `tests/test_wiki_passages.py`: short pages unchanged byte for byte (with front matter removed); a deep fact
  reaches the reference; title and order kept; two passages by default; the fallback when no word matches;
  another alphabet; code fences not split; headings carried; an oversized paragraph split; empty and untitled
  pages; and the real `ask()` and `ask_stream()` paths, including the slugs reported.
