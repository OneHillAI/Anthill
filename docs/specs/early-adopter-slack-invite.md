# Spec: every new user is invited to the early-adopter Slack group

Status: implemented. Lane: `pillar:feature`. Issue: #369.

## Problem

Early adopters get support, give feedback and steer the development of Anthill in a Slack group, but nothing in the
app tells a new user it exists.

## Fix

1. Every user who signed up on or after 25 November 2026 (`NEW_USERS_FROM` in `anthill/web/community.py`) gets one
   notification the first time a page is drawn for them: title "Join the early-adopter Slack group", message "Join the
   early-adopter Slack group to receive support, provide feedback, and steer the development of Anthill.", and the
   invite link. It is made through `notify()`, with no push, so nothing leaves the app. People who signed up earlier get
   nothing.
2. It is given once. A second page, two pages loading at the same moment, and a notification the person has already
   read do not add another (if two are made at once the first is kept).
3. It shows in two places that share one state, the notification itself:
   - the bell and the Notifications page, with a "Join Slack" button that opens the link in a new tab
     (`rel="noopener noreferrer"`); the page makes sure a new user has it before it lists their notifications;
   - the dashboard, under "Needs your attention": the title, the message in full, a "Join Slack" button that opens the
     link in a new tab and a "Not now" button.
4. Clicking "Join Slack", "Not now", or opening the Notifications page marks it read: the dashboard row goes and the bell
   count clears. `POST /community/slack/done` does it for the signed-in person only (a click sends no `next` and gets
   204; the form sends `next=/` and goes back to the dashboard).
5. The Notifications page opens any link that starts with `http` in a new tab with the safe `rel`; links inside the app
   open as before and keep their "Open" button.
6. Solo and organisation accounts, and invited members once they are active, are treated the same. A failure to write it
   never costs the person their page.

## Acceptance

- A new user sees the row on the first dashboard, with the exact words and the link, and one unread bell notification.
- A user from before the cutoff sees nothing.
- It is not given twice, including two calls at once; nothing is pushed.
- "Not now", a click on "Join Slack" and opening the Notifications page each hide the row and clear the bell.
- External links in notifications open in a new tab safely; in-app links are unchanged.
- Looked at on a running copy at desktop widths in light and dark.

## Known limit

The link is a Slack shared invite and can expire; when it does, the constant `INVITE_URL` changes in one place.
