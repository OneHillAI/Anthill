# anthill.run services

Cloudflare Pages Functions. Each file maps to a URL path by its location:

- `functions/api/health.js` -> `https://anthill.run/api/health`
- `functions/api/<name>.js` -> `https://anthill.run/api/<name>`

Export a handler per HTTP method (`onRequestGet`, `onRequestPost`, ...) or a catch-all `onRequest`.
See `api/health.js` for the minimal shape, and the onehill.org site's `functions/api/signup.js` for a
fuller example (form validation, a D1 binding, an outbound email). Bindings and secrets are configured in
the Cloudflare Pages dashboard (Settings -> Functions), not in this repo.

Keep functions dependency-free where possible; the Pages runtime has no `node_modules` install step unless
the project is configured with a build.
