# Spec: consolidate chat folders into projects (one grouping concept)

Status: accepted (founder decisions 2026-10-02, see section 8)
Lane: `pillar:platform`
Relates to: anthill/web/templates/_sidebar.html, anthill/web/templates/chat.html, anthill/web/templates/teams.html, anthill/web/app.py (teams + folders routes), anthill/web/db.py (Folder, Team, Conversation).

## 1. Problem
Two overlapping ways to group chats confuse users (founder: "I created a project, it doesn't show up in the folder in the top right of the chats. Also, it says 'folder'"). A Folder groups only the user's own chats (no wiki/memory/members; created via a bare placeholder input - "there is no button for it"). A Project (a team) ties chats + wiki + memory + members; on Solo it is "your own - single-user, always local"; created from the Projects nav; its chats appear in a "Projects" rail section, never in the top-right per-chat Folder dropdown. Both group chats, so they compete; a project subsumes a folder for Solo. Not a backend bug (project creation, team-wiki sharing, personal isolation all pass) - two redundant concepts.

## 2. Decision
Drop standalone folders; grouping is a capability of projects. Exactly one way to group chats = a project; knowledge + (org) membership are optional layers. On Solo a project is a lightweight, local, single-user workspace created with one click.

## 3. Requirements
- R1. Single grouping primitive in chat = project. Remove the standalone folder concept, its "+ New folder" input, the per-chat top-right Folder dropdown, and the Folders rail section.
- R2. Project creation stays under the Projects menu. A just-created project is immediately visible in the chat rail; attaching a chat to it is an obvious control in the chat view (not a bare input).
- R3. From the chat, a chat can be attached to an existing project BOTH (a) before its first prompt and (b) after it has prompts/responses (move an existing chat, history intact). Replaces "Move to folder". Rail: Pinned, one section per project, Unfiled - no Folders section.
- R4. A Solo project runs fully local (no org backend), so chats keep working with no "your cloud" configured.
- R5. No data migration: folders are unused pre-release; remove the feature + `folders` table + UI; set any `conversations.folder_id` to NULL (those chats become Unfiled).
- R6. Knowledge ladder (personal -> project -> org) and wiki scoping unchanged; this only unifies chat grouping.

## 4. Migration
None. Drop the `folders` table + folder UI; NULL out any `conversations.folder_id`. The app already snapshots the DB pre-migration.

## 5. Acceptance criteria
- No folder surface in chat (no "+ New folder", no top-right Folder dropdown, no Folders rail section); creating a project is a visible button and the new project shows in the rail at once.
- A chat can be moved into a project and shows under it; removing it leaves it Unfiled (not deleted).
- A Solo account with no "your cloud" can create + use a project entirely locally.
- A chat can be attached to a project before its first prompt and after it has history; messages stay intact; it appears under that project.
- make test green; the old folder tests become project-grouping tests.

## 6. Open questions
- Default project vs keep Unfiled (leaning: keep Unfiled, no forced default).
- Solo single-user project wording ("Project" everywhere vs softer on Solo) - founder's call.
- Dropping `folders` table is a schema change; since unused it can go in this PR (NULL folder_id first).

## 7. Non-goals
- No change to org/team wiki scoping, the knowledge ladder, or membership/permissions.

## 8. Decisions and amendments (2026-10-02)

Decisions by the founder, answering section 6:
- Keep Unfiled. There is no forced default project; a chat is Unfiled until it is attached.
- "Project" is the one word everywhere, on Solo and in organizations.

Amendments found while building:
- R7 (privacy). A project chat runs on the model the routing rule gives the team plane
  (`plane_routing.plane_inference`): the organization's cloud model whenever an org backend has ever been
  configured (`planes.is_org_mode`), the local model otherwise. So in an org install, moving a chat that
  already has messages into a project sends those messages to the org model as context from the next
  message on, and gives the chat the project's wiki. (A Solo-plane chat in a genuine multi-user org already
  runs on the org model, kept private; the question is still asked, because the move also changes the
  chat's wiki scope and must never happen silently.) THE SYSTEM SHALL ask first: the move redirects to the
  chat with a confirmation, and nothing changes until the user confirms. No question is asked on a Solo install (nothing leaves the device), for a chat with no
  messages, or for a chat already in a project (it is already on that model). The chat stays visible only to
  its owner, and memory extracted from it stays personal.
- Moving is audited: `chat.project_attach` and `chat.project_detach` record the chat and both project ids.
- An Organization chat keeps its plane and cannot be attached; only a project the caller belongs to is
  accepted (a stray id is ignored); another user's chat is untouched.
- Rail: a project group shows even while empty, with a "+" that starts a chat inside it. Chats in no project
  sit under "Unfiled" (the solo plane). A "+ New project" link at the bottom of the rail and in the chat
  header (when the user has no project yet) opens the Projects page with the create form open
  (`/teams?new=1`); creation stays under the Projects menu.
- R5 and section 4, amended. The `Folder` model and `conversations.folder_id` are removed from the data model,
  and a new database never creates the `folders` table or the column. On an EXISTING database the
  versioned migration 5 clears every `folder_id` (those chats become Unfiled), but the physical `folders`
  table and the now-NULL `folder_id` column stay. Reason: `folder_id` is a foreign key to `folders`, SQLite
  cannot drop a column that is part of a foreign key, and the app runs with `PRAGMA foreign_keys=ON`, so
  dropping the table would make every later write to `conversations` fail. Dropping both safely needs a
  rebuild of the `conversations` table; that is a follow-up, not part of this change. Any folder rows an
  early user created are left in place, unused.

Acceptance criteria added:
- In an org install, moving a chat with history needs the confirmation; without it the chat does not move.
- Attach and detach are audited; a foreign project, a foreign chat and an Organization chat are untouched.
- A database with the old schema migrates (folder ids cleared, chats kept) and stays writable.

Tests: `tests/test_chat_projects.py` (replaces `tests/test_chat_folders.py`), the project-grouping rail in
`tests/test_plane_routing.py`, and a headless-browser flow in `tests/browser/test_chat_projects_browser.py`.
