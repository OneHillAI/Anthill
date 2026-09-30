# In-app notifications: the chokepoint + bell + centre (P0)

## Problem

Notifications reached the user through three disjoint paths: web push wired to a handful of events, ad-hoc
flash banners, and raw `alert()`. There was no persisted record, no unread state, and no one place to
route a notification-worthy event from (#284). So most "you need to act" moments (an agent waiting on your
approval, a run that needs a look) were only visible if you happened to be on the right page.

## Design (P0)

One chokepoint, one persisted model, one place to see them:

- **`Notification` model** - `user_id`, `org_id`, `kind` (approval | review | run | info), `title`,
  `body`, `link` (the in-app URL to act on it), `read`, `created_at`. Auto-created by `create_tables` on
  startup (a new table, no column migration).
- **`notify()` chokepoint** (`web/notify.py`) - the single call every event uses. It persists one
  `Notification` and best-effort sends a web push (the existing out-of-app channel) with the same
  title/body/link. It never raises into the caller - a notification is a side effect, not the main work.
- **Bell + unread badge** - a Notifications item in the sidebar with the unread count (reusing the
  existing `nav_badges` pattern), computed once per render.
- **Notification centre** (`GET /notifications`) - the user's notifications, newest first, each with a
  title/body/time and an Open link. Opening the centre marks the unread ones read (an inbox: you have now
  seen them), which clears the bell badge; the ones that were new this visit are flagged so you can still
  spot them.

**First event routed:** an agent that proposes a consequential action (an `AgentApproval` is created)
notifies its owner - the clearest "act now" case.

## Requirements

- Every notification is created through `notify()`; nothing writes `Notification` rows directly.
- The bell shows the count of the current user's unread notifications and clears once the centre is opened.
- The centre lists the user's notifications newest-first with a link to act, and marks unread ones read on
  open.
- `notify()` failures (DB or push) never break the event that triggered them.

## Acceptance criteria

- `notify(db, user_id=..., kind=..., title=...)` persists one unread `Notification`.
- With an unread notification, any page's sidebar shows the Notifications bell with an unread badge;
  after `GET /notifications` the badge is gone and the notification is marked read.
- When an agent queues an approval, its owner gets an `approval` notification linking to the agent.

## Consolidation (done, after P0)

- **More events routed through `notify()`:** a scheduled **task** or **agent** run that fails or is flagged
  for review notifies its owner (replacing the old per-tick web-push wiring; only runs that need attention
  notify, so the bell stays signal), and a **project invite** notifies the invited member. With the P0
  agent-approval event, that covers the main "act now" cases.
- **One immediate-feedback path:** the scattered raw `alert()` calls across the app are unified onto a
  single global `showToast()` in the shell (with a `toastAfter()` variant that survives a page reload).
  This also fixes feedback that silently did nothing in the desktop webview, where native `alert()` is
  unreliable. The two duplicated per-page toast implementations are removed.

Wiki-review queuing is intentionally left on its existing nav-badge (routing it too would double-signal).

## Still open (smaller follow-ups)

A dropdown centre in the shell (vs the page), per-notification dismiss, and grouping.
