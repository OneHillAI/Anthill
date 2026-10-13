# Chat rail: fold a long list of chats to the 10 most recent

## Problem

The left menu lists every recent chat. With a few dozen chats the list pushes Tasks, Agents and the rest of
the menu off the screen, and the chat you want is a long scroll away.

## Fix

A group of chats in the rail (Unfiled, Organization, one project, or the single plain list a Solo account
sees) shows its 10 most recent chats. The rest fold behind a quiet "Show N more" row; "Show fewer" folds them
again.

- The chat you are in always stays visible, even if it is older than the tenth.
- Pinned chats are never folded.
- While you type in "Search chats", every match shows, folded or not.
- The A-Z / Recent switch keeps working, and the fold applies to the order you chose.
- Whether a group is opened is remembered for the session, so opening a chat from the list does not fold it
  again. Nothing is saved on the server.
- The full archive is still one click away under "Search / see all chats".

## Acceptance criteria

- An account with 14 unpinned chats sees 10 in the rail and "Show 4 more"; one click shows all 14 and the row
  reads "Show fewer".
- Opening the 13th chat shows the first 10 plus that chat, with "Show 3 more".
- Searching for the 14th chat shows only it, with no "Show more" row; clearing the search restores the fold.
- A group of 10 or fewer chats shows no "Show more" row.
