# Spec: an item's own page

Status: implemented. Lane: `pillar:knowledge`. Issue: #315. Builds on `library-list.md` (#312) and
`answer-manifest.md`. Part of the knowledge update, epic #270 (step 6, smallest version). It only reads.

## Problem

A person could see what a wiki holds (the Library) and what one answer was given (the panel under an answer),
but not the other way round: for one page or document, what it is, where it came from, what was made from it, and
which of their chats used it.

## Fix

1. A page "About this page" or "About this document" at `/wiki/item?kind=page|document&name=...&scope=...`, opened
   from a "Details" link on each Library row and an "About this page" link on the page view.
2. It shows the title, the kind and the wiki (Your wiki, Team wiki, Organisation wiki), the Library's chips, where
   it came from in words (uploaded or written by you, written by a model, edited by a person, imported), the
   version with the plain note that the earlier text is not kept, the day it was learned, and a short content
   fingerprint.
3. "Made from" (a page's document) or "Pages made from it" (a document's pages), as links.
4. "Used in your chats": the chats whose answers were given this item, newest first, each a link with the number
   of answers, the first 20 and then "and N more chats". Only the viewer's own chats are read, and at most their
   newest 3,000 answers. An item no chat used says so. The wording is "used in", never "caused".
5. The actions the person already had: open the page or the original, "text read from it", Edit (pages) and
   Remove, shown only to someone who may edit that wiki.
6. An item with no record, or a damaged one, has a page too, with what is known and no version.
7. Access is that of the page view: a team wiki needs membership (403), the organisation wiki is not open to a Solo
   account, and a missing item, a wrong kind or a path is 404.

## Acceptance

- A page and a document each show their facts, their links and the right chats, newest first, with answer counts.
- Another person's chat is never listed.
- The list is cut at 20 and counted.
- A page with no record still has a page.
- The page view and the Library lead to it.
- Measured: with 1,000 stored answers the page takes about 16 ms on a laptop.
- Looked at on a running copy at desktop widths, light and dark.

## Not here

A version list, restore of an earlier version and supersede (the earlier text is not kept); training status;
connector status and expiry; project and space; other people's chats, tasks, agents and the widget.
