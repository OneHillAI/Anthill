# Spec: branded error pages

Status: accepted. Lane: `pillar:feature`.

## Why

A person who hits a bad URL or a crash used to get a bare JSON blob (`{"detail":"Not Found"}`), because
the app raised `HTTPException` with no page behind it. An error is exactly where trust is easiest to
lose, so it should look like Anthill and stay light. This adds one warm, on-brand page for every error
a browser can land on.

## Behaviour

- A single template, `anthill/web/templates/error.html`, renders the error. It is standalone (it does
  not extend `base.html`), because an error can hit an anonymous or half-broken request where the nav
  context is not available. It pulls only the shared tokens from `static/style.css`.
- The centrepiece is the **fleeing-ant scene**: the mirror of the chat "antstreet" animation. Both use
  the same anthill mark (the `#ah-logo` symbol, shared from `_sidebar.html`; inlined on the standalone
  error page). Where the working state marches ants left -> right *into* the anthill as it does its job,
  an error sends them the other way, right -> left *away* from the (still pulsing) hill. Honours
  `prefers-reduced-motion` (animation off) and both colour schemes.
- Copy keeps one running joke: "You stepped on one of us" and the colony coming to the rescue. Per code:
  404 and 500 use the rescue line; 403 is "this tunnel is sealed"; 401 is a sign-in nudge. Defaults to
  the rescue line for any other code.

## Content negotiation and safety (the rules that must hold)

Two FastAPI handlers in `app.py` (`_http_exception_page` for `StarletteHTTPException`,
`_unhandled_exception_page` for `Exception`):

1. **Browser only.** The branded HTML renders only when the request `Accept` header contains
   `text/html`. API and JSON clients (and the test client, which sends `*/*`) keep the exact JSON error
   body they got before. No existing error contract changes.
2. **Redirects pass through.** A 3xx raised as `HTTPException` (the `303 -> /login` guard on protected
   pages) is returned as a redirect with its `Location` intact, never turned into an error page.
3. **Crashes are logged.** The catch-all `Exception` handler logs the traceback before rendering, so a
   500 is never silently swallowed.

## Tests

`tests/test_error_pages.py`: branded 404 for a browser, JSON 404 preserved for an API client, the
`/login` redirect still redirects, and a 500 renders branded HTML for a browser but JSON otherwise.
