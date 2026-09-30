# Chat sidebar: folders read as personal organisation

## Problem

Founder feedback on the chat sidebar (#414): it is hard to grasp.

1. **"New folder" is unclear** - a user cannot tell whether a folder is a team/project thing or just
   personal chat organisation. It is the latter (a `Folder` groups a user's own conversations), but the
   control does not say so.
2. **Standalone (unfiled) chats are buried below folders.** The order was pinned -> folders -> unfiled, so
   a brand-new chat with no folder landed under all the folders and looked like it had gone "into" one.
3. **Sizing / hierarchy is off.** The folder's delete (bin) was always shown and felt heavy, the
   "+ New folder" input was full-size, and the folder title was a faded 10px uppercase micro-label - so
   the folder name (the thing that matters) was the least prominent element.

## Requirements

- **Standalone chats first.** Under the Personal home (and in the simple Personal-only list), unfiled
  chats render directly under the heading, above the user's folders, so a new standalone chat is easy to
  find and never looks filed.
- **Folder framing is clearly personal.** The new-folder control reads as personal chat organisation:
  placeholder "New folder for your chats" and a tooltip "Group your own chats into a folder. Personal to
  you - not a team or project."
- **Visual hierarchy: the folder NAME is prominent.** The folder title is a real title (13px, normal case,
  not a faded uppercase micro-label). Its delete control is subtle and reveals on hover only. The
  "+ New folder" input is a quiet, secondary dashed field. Per-conversation delete/pin icons are slightly
  smaller.

## Acceptance criteria

- In the rail, unfiled personal chats appear before the folder list (both the Personal home and the
  simple list).
- The new-folder input placeholder/tooltip make clear it is personal, not a team/project.
- The folder title renders at 13px in normal case; the folder delete control is hidden until the folder
  row is hovered.

## Scope

`_sidebar.html` (ordering + new-folder copy) and `style.css` (folder title, folder delete, new-folder
input, conversation icon sizing). No route or model change. Not in scope: a per-conversation "move to
folder" control in the sidebar itself (today filing is via the chat page's "Move to folder" dropdown) - a
worthwhile follow-up for #414's discoverability point, but a larger interaction.
