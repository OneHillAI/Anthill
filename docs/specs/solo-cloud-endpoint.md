# Solo cloud inference: bring your own endpoint

## Problem

A Solo account can run its model on the user's own cloud GPU (`solo_compute == "cloud"`), and the chat
router already sends a Solo turn to a connected cloud endpoint
(`plane_routing.plane_inference`, the `solo_compute == "cloud"` branch: OpenAI backend, personal wiki
kept, non-ephemeral, local fallback when unreachable). But the only way to actually *connect* that
endpoint was the org-admin "Cloud & model" page (`/settings/organization`) - a surface framed as "the
shared model server the org runs on", admin-gated, and cluttered with org-only concepts (council
reviewers, shared backend). A solo user picking "Your cloud" in Settings, or mid-setup, was redirected
into it. The one solo-native shortcut ("point this device at your own endpoint") was disabled ("coming
soon"). So the routing and validation existed, but no solo-scoped door onto them.

## Design

A Solo user connects and validates their own cloud model server entirely from personal
Settings -> This device -> "Change where it runs" -> Your cloud, never the org page.

**Route.** `POST /personalize/cloud` (`personalize_cloud`), authenticated as any user (not admin):

- `action == "connect"`: saves `endpoint_url` -> `org_model_endpoint`, `cloud_model` -> `org_model`,
  `api_key` -> `org_model_key_enc` (blank keeps the saved key), and sets `solo_compute == "cloud"`.
  It then validates the endpoint end to end with the existing `hosting.endpoint.validate`
  (reachable -> model available -> a real round-trip) and records `org_backend_status`
  (`validated` | `error`) and `org_backend_detail`. Success makes `planes.org_available` true, which is
  exactly what the Solo-cloud routing branch requires.
- `action == "disconnect"`: clears the endpoint and returns `solo_compute` to `local` (the on-device
  model), so a user can back out cleanly.
- A genuine multi-user org (`deployment_topology == "org"`) is redirected to `/settings/organization`;
  the shared org backend stays an admin concern.

**Reuse, not new plumbing.** The route writes the SAME `org_*` endpoint fields the org backend uses,
because the router already reads them for a Solo-cloud turn. No new columns, no routing change.

**Stays Solo.** `deployment_topology` is never touched, so the account remains Solo: the dashboard,
Settings, and wiki scope are all topology-driven. `nav_org` is narrowed to genuine org topology (was
`is_org_mode`), so connecting an endpoint no longer makes a Solo account sprout an org label in the
nav - it keeps reading "Solo, private".

**UI.** The "Your cloud" compute card gains a "Your cloud endpoint" panel: server URL, an optional
model, and an optional API key, with a "Connect and validate" action and a connected/Disconnect state.
Because the compute controls live inside the outer `/personalize` form, the connect controls target a
sibling `#solo-cloud-form` via the HTML5 `form=` attribute rather than nesting a form (nested forms are
invalid HTML and silently detach). The Advanced "custom endpoint" row now links to this panel instead
of reading "coming soon".

## Out of scope

- Provider provisioning for Solo (RunPod/Lambda turnkey) - a later slice; this is the
  bring-your-own-endpoint path only.
- The setup-wizard cloud step - a later slice reusing this panel.
