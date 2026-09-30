# Spec: Desktop remote-IPC capability needs a path wildcard

Status: implemented (config). Lane: `pillar:platform`. Issue: #388.

## Problem

The Tauri desktop shell loads its window from a **remote** origin - the Python backend on
`http://127.0.0.1:<free-port>/<path>`. `src-tauri/capabilities/default.json` scopes the custom IPC
command (`open_profile`) to that origin with:

```json
"remote": { "urls": ["http://127.0.0.1:*", "http://localhost:*"] }
```

In Tauri v2 a remote-URL glob with **no path segment matches nothing**, so the actual page URL
(`http://127.0.0.1:<port>/profiles`) never matches. The `open_profile` invoke is silently dropped before
it reaches the Rust handler - so clicking **Open** on a non-active profile does nothing (no
`spawn_backend`, no new sidecar port, no window switch). Everything else works because it is plain HTTP
to the backend, not Tauri IPC. (Documented upstream: tauri-apps/tauri#11622.)

## Fix

Add the `/**` path wildcard so the glob matches any path on the local origin:

```json
"remote": { "urls": ["http://127.0.0.1:*/**", "http://localhost:*/**"] }
```

This does not widen the trust boundary - the same two local origins are trusted; the wildcard only lets
the glob match their paths, which is required for the invoke to reach Rust.

## Acceptance criteria

- `src-tauri/capabilities/default.json` is valid and accepted by the Tauri build codegen (`cargo check`
  runs `build.rs` -> capability validation with no error).
- End-to-end (needs a desktop build): clicking **Open** on a non-active profile switches the window to
  that profile's fresh sidecar (a new port) and stops the previous one, exercising the PR #307
  profile-switch IPC.
