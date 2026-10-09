"""Send the best passages of a long wiki page to the model, not the whole page (part 2 of #109).

A page goes into the prompt whole today, and the model reads every token of it before it answers: about
165 tokens a second on a laptop, so each 1,000 tokens costs about 6 seconds before the first word. A page over
2,000 characters is therefore cut to the passages that best match the question:

* The page is split into passages of about 800 characters, packed from whole paragraphs. A fenced code block
  is never split at a blank line (a block longer than a passage is cut at line ends, and each piece keeps its fences), and a passage that does not start with a heading gets the heading above it
  as its first line, so it stands alone.
* Passages are ranked against the question by word overlap (BM25). That is plain arithmetic with no model
  call, about a millisecond for a page.
* The best two are kept, in their original order, after the page's title line. A line ``[...]`` marks where
  text was left out; two passages that were next to each other in the page are joined as they were.
* A page of 2,000 characters or less goes in whole, unchanged. So does a page that has no more than two
  passages.

Only pages over 2,000 characters are affected. A wiki of short pages sees no change.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path

from ..common.text import strip_frontmatter

PASSAGE_CHARS = 800  # the size a passage is packed up to (about 200 tokens)
WHOLE_BELOW = 2000  # a page this long or shorter goes in whole
KEEP = 2  # passages kept per long page
OMITTED = "[...]"  # marks text left out between kept passages
PAGE_SEPARATOR = "\n\n---\n\n"  # between pages in the reference (as before)

_WORD = re.compile(r"\w{2,}")
# Scripts written without spaces between words (CJK, Thai, Lao, Myanmar, Khmer, Hangul): ranked by pairs of
# neighbouring characters, since there are no word breaks to split on.
_UNSPACED = (
    "\u0e00-\u0eff\u1000-\u109f\u1100-\u11ff\u1780-\u17ff\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff"
)
_UNSPACED_RUN = re.compile(f"([{_UNSPACED}]+)")
_STOP_WORDS = (
    "the and for are but not you all can had her was one our out has have this that with from they what when "
    "which where who how why does did will would should could into than then them their there these those "
    "about your its is of to in on at by or an as be do if so we"
)
_STOP = frozenset(_STOP_WORDS.split())
_FENCE = re.compile(r"^\s*(```|~~~)")


def _words(text: str) -> list[str]:
    """The ranking terms of ``text``: lower-cased words of two or more letters or digits (minus a short list of
    English stop words), and pairs of neighbouring characters for scripts written without spaces."""
    terms: list[str] = []
    for part in _UNSPACED_RUN.split(text.lower()):
        if not part:
            continue
        if _UNSPACED_RUN.fullmatch(part):
            terms.extend(part[i : i + 2] for i in range(max(1, len(part) - 1)))
        else:
            terms.extend(w for w in _WORD.findall(part) if w not in _STOP)
    return terms


def _blocks(body: str) -> list[str]:
    """Paragraph blocks of ``body``. A blank line splits a block only outside a fenced code block."""
    blocks: list[list[str]] = [[]]
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        if not line.strip() and not fenced:
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    return ["\n".join(b) for b in blocks if b]


def _pieces(block: str, size: int) -> list[str]:
    """``block`` as pieces of at most ``size`` characters, cut at line ends (a lone very long line stays whole).
    The pieces of a fenced code block each carry the opening and closing fence, so none is left open."""
    if len(block) <= size:
        return [block]
    lines = block.splitlines()
    opening = lines[0] if _FENCE.match(lines[0]) else ""
    closing = lines[-1] if opening and len(lines) > 1 and _FENCE.match(lines[-1]) else ""
    body = lines[1 : len(lines) - 1 if closing else len(lines)] if opening else lines
    fence = opening.strip()[:3] if opening else ""
    out, cur = [], ""
    for line in body:
        if cur and len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    if opening:
        out = [f"{opening}\n{piece}\n{closing or fence}" for piece in out]
    return out


def split_passages(body: str, size: int = PASSAGE_CHARS) -> list[str]:
    """Pack consecutive paragraphs into passages of about ``size`` characters.

    A passage that does not begin with a heading gets the nearest heading above it as a first line.
    """
    packed: list[tuple[str, str]] = []  # (heading in force where the passage starts, passage text)
    heading, cur, cur_head = "", "", ""
    for block in (piece for b in _blocks(body) for piece in _pieces(b, size)):
        if cur and len(cur) + len(block) + 2 > size:
            packed.append((cur_head, cur))
            cur = ""
        if not cur:
            cur_head = heading
        cur = f"{cur}\n\n{block}" if cur else block
        fenced = False
        for line in block.splitlines():
            if _FENCE.match(line):
                fenced = not fenced
            elif not fenced and line.startswith("#"):
                heading = line.strip()
    if cur:
        packed.append((cur_head, cur))
    out = []
    for head, text in packed:
        first = text.lstrip().splitlines()[0]
        out.append(text if (not head or first.startswith("#")) else f"{head}\n{text}")
    return out


def _bm25(question: str, passages: list[str]) -> list[float]:
    """BM25 score of each passage against ``question`` (k1 1.5, b 0.75). All zeros when nothing overlaps."""
    docs = [_words(p) for p in passages]
    n = len(docs)
    avg = sum(len(d) for d in docs) / max(1, n)
    df: dict[str, int] = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    terms = set(_words(question))
    scores = []
    for d in docs:
        tf: dict[str, int] = {}
        for w in d:
            tf[w] = tf.get(w, 0) + 1
        score = 0.0
        for w in terms & tf.keys():
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            score += idf * tf[w] * 2.5 / (tf[w] + 1.5 * (0.25 + 0.75 * len(d) / max(1.0, avg)))
        scores.append(score)
    return scores


def best_passages(body: str, question: str, keep: int = KEEP) -> str:
    """The text of a page to send for ``question``: the page whole when it is short, else its title and
    its ``keep`` best passages in original order. With no word in common the first passages are kept."""
    if len(body) <= WHOLE_BELOW:
        return body
    passages = split_passages(body)
    if len(passages) <= keep:
        return body
    scores = _bm25(question, passages)
    top = sorted(sorted(range(len(passages)), key=lambda i: (-scores[i], i))[:keep])
    text = passages[top[0]]
    for before, i in pairwise(top):
        # Passages that were next to each other in the page are joined as they were; a gap gets the marker.
        text += ("\n\n" if i == before + 1 else f"\n\n{OMITTED}\n\n") + passages[i]
    title = next((ln for ln in body.splitlines() if ln.startswith("# ")), "")
    if title and not text.lstrip().startswith(title):
        text = f"{title}\n\n{OMITTED}\n\n{text}"
    return text


def build_reference(pages: Iterable[Path], question: str) -> str:
    """The wiki reference text for a prompt: each page (front matter removed) cut to its best passages when it
    is long, joined with the usual page separator. One helper for the blocking and the streaming answer."""
    return PAGE_SEPARATOR.join(
        best_passages(strip_frontmatter(p.read_text()), question) for p in pages
    )
