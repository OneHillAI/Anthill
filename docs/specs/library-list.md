# Spec: the Library, one list of everything in a wiki

Status: implemented. Lane: `pillar:knowledge`. Issue: #312. Part of the knowledge update, epic #270 (step 6,
smallest version). It only reads what the item records (`docs/specs/item-records-and-inventory.md`) already hold.

## Problem

A wiki's contents were shown in two places, a Pages list and a Files list. A page and the original it was made from
were in different tabs and not linked, and nothing showed which pages a model wrote that nobody has checked.

## Fix

1. The Wiki menu has two sub-tabs, Library and Principles. The Library is the one list of the wiki's pages and its
   kept original documents, by title. `?tab=pages` and `?tab=files` open it, so old links keep working.
2. A row shows the title (a link), the kind (Page or Document), the day it was learned, a document's size, chips,
   and Edit and Remove where the person may edit. Chips: "AI-written, unchecked" for a page a model wrote that
   nobody has checked, "Replaced" or "Archived" for a lifecycle state, "Waiting for a decision" or "Rejected" for
   a review state. An ordinary current item has no chip.
3. A page row says which document it was "Made from"; a document row says "Pages made from it" and, when the text
   Anthill read out of it was kept, offers "text read from it". Both directions are links.
4. Filters, on the address so they survive a reload: search by title, Pages or Documents, and "AI-written,
   unchecked". The count says "2 of 10 items" when a filter hides some.
5. A page or original with no record yet, or a damaged one, is listed all the same, without chips. Nothing that
   the Pages and Files lists showed is hidden: an item waiting for a decision or rejected is listed with its chip,
   so it can still be removed. What is waiting for a decision is also in Needs review, as before.
6. Removed items stay their own card under the list, with Restore and Delete now. The upload card, Recent uploads
   and the advanced section stay where they were.

## Acceptance

- The list holds every page and every kept original of the wiki, once each, by title.
- An AI-written unchecked page shows the chip; a checked page and a person's own page show none.
- A page lists its document and a document lists its pages, as links.
- A page with no record or a damaged record is listed without chips.
- The three filters work, can be combined, and stay filled in after the page reloads.
- `?tab=files` and `?tab=pages` show the Library; the Principles tab is unchanged.
- Remove and Restore work from the Library as before.
- A page is read again only when its file changed. Measured with 1,000 pages and 500 originals: the first view
  after a start takes about 1 s (every page's record is parsed once), later views about 22 ms; the old list of
  pages took 30 ms.
- Looked at on a running copy at desktop widths, light and dark.
