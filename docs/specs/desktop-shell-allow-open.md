# Spec: External links open in the default browser in the packaged desktop (#170)

Status: implemented
Lane: `pillar:platform`
Relates to: `src-tauri/capabilities/default.json`, `src-tauri/src/lib.rs`, `src-tauri/README.md`, `tests/test_desktop_capabilities.py`.

## 1. Problem

Clicking any external link in the packaged Tauri v2 desktop app (`Anthill.app`) - including assistant markdown links and web-source pills in `chat.html`, task output links in `task_result.html`, and provider or documentation links across Settings and Contribute - does nothing.

Two mechanisms interact here:

1. **Link rendering and WebView new-window behaviour:** `chat.html` and `task_result.html` attach a DOMPurify `afterSanitizeAttributes` hook that sets `target="_blank" rel="noopener noreferrer"` on every `<a>` tag, and other templates (`mcp.html`, `_inference_provider.html`, `_compute_chooser.html`, `contribute.html`, `settings_remote.html`) also use `target="_blank"`. In Tauri v2's `wry` backend (`WKWebView` on macOS, `WebView2` on Windows, `WebKitGTK` on Linux), native `target="_blank"` new-window requests are denied when no custom `new_window_handler` is configured.
2. **`tauri-plugin-shell` click interception and remote-origin ACL:** `src-tauri/src/lib.rs` registers `.plugin(tauri_plugin_shell::init())`. That plugin injects `init-iife.js` into every main-frame page, which intercepts clicks on `<a target="_blank">` links whose `href` starts with `http://`, `https://`, `mailto:`, or `tel:`, calls `e.preventDefault()`, and invokes `window.__TAURI_INTERNALS__.invoke("plugin:shell|open", { path: r.href })`. Because the desktop window navigates to `http://127.0.0.1:<port>/...`, Tauri v2 treats that origin as **remote** and rejects any IPC command not explicitly listed in `src-tauri/capabilities/default.json`. `default.json` granted `shell:allow-execute` (for spawning the sidecar) but omitted `shell:allow-open`, so `plugin:shell|open` was rejected by the ACL (`shell.open not allowed by ACL`).

## 2. Requirements

- R1. `plugin:shell|open` is permitted from the local backend's loopback origins (`http://127.0.0.1:*/**` and `http://localhost:*/**`) on the `main` window.
- R2. `plugin:shell|open` remains refused by the ACL from any other origin (`https://example.com/...`, LAN addresses, etc.); `remote.urls` must not be widened.
- R3. Only URLs matching `tauri-plugin-shell`'s default `OpenScope` regex (`^((mailto:\w+)|(tel:\w+)|(https?://\w+)).+`) may be opened; non-browser schemes (`file://`, `javascript:`) remain blocked by `OpenScope`.
- R4. Automated tests in both `src-tauri/src/lib.rs` (`mod acl_tests`) and `tests/test_desktop_capabilities.py` guard the capability and ACL behaviour so regressions are caught in CI as well as in `cargo test --lib`.

## 3. Fix

- Add `"shell:allow-open"` to `permissions` in `src-tauri/capabilities/default.json` and document it in `default.json` and `src-tauri/README.md`.
- Add `invoke_shell_open_from`, `shell_open_is_allowed_from_the_local_backend_origin`, and `shell_open_is_still_refused_to_any_other_origin` to `mod acl_tests` in `src-tauri/src/lib.rs`.
- Add `tests/test_desktop_capabilities.py` to verify that `src-tauri/capabilities/default.json` grants `shell:allow-open` and `allow-open-profile` strictly to the two loopback `remote.urls` and that the Rust ACL test suite covers `plugin:shell|open`.

## 4. Acceptance Criteria

- [x] From `http://127.0.0.1:<port>/`, `http://127.0.0.1:<port>/chat/1`, and `http://localhost:<port>/chat/1`, `plugin:shell|open` passes the Tauri ACL and reaches `OpenScope` validation.
- [x] Non-browser schemes (`file:///etc/passwd`, `javascript:alert(1)`) are rejected by `OpenScope`'s regex validation.
- [x] From unlisted origins (`https://example.com/chat/1`, `http://192.168.1.20:8000/chat/1`), `plugin:shell|open` is rejected by the ACL.
- [x] `cargo test --lib` in `src-tauri/` and `pytest` in `tests/test_desktop_capabilities.py` pass.
