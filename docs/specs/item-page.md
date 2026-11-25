# Spec: an item's own page

Status: implemented. Lane: `pillar:knowledge`. Issue: #315. Builds on `library-list.md` (#312). Part of the knowledge update, epic #270 (step 6, smallest version). It only reads.

## Problem

A person could see what a wiki holds (the Library), but not the item as one thing: what it is, where it came from,
and what was made from it. The record of each item already says all of that.

## Fix

1. A page "About this page" or "About this document" at `/wiki/item?kind=page|document&name=...&scope=...`, opened
   from a "Details" link on each Library row and an "About this page" link on the page view.
2. It shows the title, the kind and the wiki (Your wiki, Team wiki, Organisation wiki), the Library's chips, where
   it came from in words (uploaded or written by you, written by a model, edited by a person, imported), the
   version (for a page, with a link to its History when earlier versions are kept, and "no earlier versions yet" when not), the day it was learned, and a short content
   fingerprint.
3. "Made from" (a page's document) or "Pages made from it" (a document's pages), as links.
4. The actions the person already had: open the page or the original, "text read from it", Edit (pages) and
   Remove, shown only to someone who may edit that wiki.
5. An item with no record, or a damaged one, has a page too, with what is known and no version.
6. Access is that of the page view: a team wiki needs membership (403), the organisation wiki is not open to a Solo
   account, and a missing item, a wrong kind or a path is 404.

## Acceptance

- A page and a document each show their facts and their links.
- A page with no record still has a page.
- The page view and the Library lead to it.
- Looked at on a running copy at desktop widths, light and dark.

## Not here

Which of the person's chats used the item: that needs an index of answers to items, not a search of the stored
answer records, and comes with that index. Training status; connector status and expiry; project and space.
