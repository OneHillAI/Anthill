# Rolling sessions

Status: implemented. Lane: `pillar:privacy`.

## Problem

Before rolling sessions, Anthill's fixed 12-hour session expired even while someone was actively using
the desktop app. A page left open after expiry remained visible, but the next navigation required login.
Asynchronous actions could also follow the authentication redirect and try to parse the login page as
JSON, producing a misleading syntax error.

## Requirements

- An active valid session SHALL become eligible for renewal when its token age reaches the lesser of
  one hour and half the configured session lifetime; legacy tokens SHALL be eligible immediately.
- By default, a device with no authenticated activity for 30 days SHALL require login again.
- Explicit logout SHALL revoke every presented device session server-side before deleting the primary,
  renewal, and observed ordering cookies.
- A password change or reset SHALL revoke every older session for that user; a reset link SHALL remain
  atomic single-use under concurrent submissions.
- Session renewal SHALL preserve HttpOnly and SameSite protections and set Secure when issued over
  HTTPS.
- User deactivation, deletion, role changes, and forced-password-reset flags SHALL take effect on the
  next request. Deactivation SHALL invalidate outstanding password-reset and invitation links.
- Invitation, verification, and OAuth activation SHALL be conditional atomic transitions so an
  in-flight activation cannot reverse deactivation; OAuth SHALL NOT reactivate a deactivated account
  without a valid pending invitation.
- The `ANTHILL_SESSION_HOURS` override SHALL continue to control JWT lifetime, cookie lifetime, and the
  renewal threshold.
- Streaming chat activity and navigation context SHALL authenticate through the same live-session path
  as normal pages; revoked tokens SHALL expose no account or project metadata on public pages.
- Every asynchronous request on the Models page SHALL navigate to login when redirected there instead
  of parsing HTML as JSON or retrying forever.

## Browser session ordering

A browser receives a random `session_device` HttpOnly cookie. A browser carrying a signed legacy session
but no device cookie derives a stable device lineage from that session, so concurrent upgrade requests
cannot split into independent lineages. At request ingress, a potential authentication transition reads the device's durable order and greatest
response-issued order without changing either. After credentials, OAuth state, or a signed session lineage
validate, the request advances the order only by comparing and swapping from that exact ingress value.
Password changes, resets, activations, setup, and OAuth commit that comparison-and-swap in the same
transaction as the successful authentication mutation. Rejected or stale requests therefore neither
advance the durable order nor mutate credentials. The signed device and order must match their cookie
metadata when present; a missing device cookie is recovered from a valid matching signed lineage, while a
conflicting device cookie is rejected at the shared authentication boundary. Logout with an invalid or
revoked session only clears that caller's cookies.
Allocation increments the database high-water mark, so it remains monotonic across workers, restarts, and
wall-clock changes. A stale request whose presented order is no longer current cannot mutate credentials
or issue a session. A successful response writes its
device ID and order into the JWT and writes the JWT to both `session_token` and a distinct
`session_order_<order>` HttpOnly cookie. Ordering is independent of user identity and `auth_version`, so
responses for different accounts in one browser are comparable.

When ordering cookies are present, only the cookie with the greatest order is authoritative. The primary
and renewal cookies remain compatibility and transport fallbacks only when no ordering cookie exists.
The server also retains each device's greatest completed order in `browser_session_orders`. Every ordered
JWT must match that durable high-water mark, so removing or expiring browser cookies cannot make a delayed
lower-order token valid. Invalid or stale authoritative tokens fail closed rather than falling back to an
older order.

Renewal preserves the current order and refreshes its ordering, renewal, and device cookies; it does not
create a newer authentication event. A fresh-session response records its order as response-issued only
while that order is still current. Logout may supersede the single allocated successor of the last
response-issued session, then advances and records its own order, revokes every presented JWT, deletes the
primary, renewal, and all ordering cookies observed by its request, and writes a `logged-out` ordering
tombstone. It retains and refreshes `session_device` so later authentication remains in the same ordering
lineage. A delayed authentication or renewal response cannot claim a lower order or reverse logout. A later
successful authentication claims a greater order, deletes every ordering cookie it observed, and becomes
authoritative. Device and ordering cookies use the same HttpOnly, SameSite, lifetime, and HTTPS Secure
policy as session cookies.

## Acceptance criteria

- Activity after the renewal threshold issues a fallback renewal cookie for the configured lifetime;
  the greatest ordering cookie remains authoritative, while legacy clients with no ordering cookie prefer
  the primary token over the fallback.
- A logged-out token, including one renewed by an overlapping request, cannot authenticate again;
  delayed login, password, reset, invitation, verification, setup, or OAuth responses cannot supersede
  the logout tombstone or a newer authentication response, including across different accounts.
- Changing or resetting a password rejects tokens held by every other device while keeping the current
  legitimate device signed in; the first concurrent password mutation wins while stale requests must
  reauthenticate, and two concurrent reset submissions produce only one new session.
- Every session cookie issued or renewed for an HTTPS request includes Secure.
- A browser that loses its session while the Models page is open reaches `/login` from Refresh Models,
  pull polling, or benchmark polling and does not show a JSON syntax error.
