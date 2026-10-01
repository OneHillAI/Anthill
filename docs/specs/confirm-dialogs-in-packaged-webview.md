# Spec: working confirmations in the packaged desktop (no native window.confirm)

Status: proposed
Lane: `pillar:platform`
Relates to: `anthill/web/templates/base.html`, `src-tauri/`, and the destructive-action controls across the web UI.

## 1. Problem

The desktop shell is Tauri 2 / wry with no dialog plugin and no Rust-side JS-dialog handler. In that
webview `window.confirm()` returns false and `window.alert()` does nothing. Every destructive action in
the web UI was gated behind a native confirm (`if (!confirm(...)) return;`, `onsubmit="return
confirm(...)"`, or `onclick="return confirm(...)"`), so in the packaged app the confirm silently
returned "no" and the action never ran: the user clicked and nothing happened, the list stayed stale
(founder report, 2026-10-01: "I click uninstall and nothing happens"). About 28 actions across 19
templates were affected (model uninstall, delete conversation / folder / agent / snippet / skill,
deactivate / remove user, revoke token, cancel task, restore backup, tear down endpoint, queue training,
expose-org-brain, dismiss suggestion, send-to-admin). It never reproduced in a browser, where the native
confirm works, which is how it shipped.

Separately, the model picker ("where your AI runs") must carry a working per-model Uninstall, and the
separate raw on-disk model-storage list (the `/models` "Installed on this device" table and the Settings
"Model storage" card) is removed: it exposes storage management that does not belong in Anthill's UX.

## 2. Requirements

- R1. No destructive action may depend on `window.confirm()` / `alert()` / `prompt()` in the packaged app.
- R2. A single shared, in-DOM confirmation is provided and used uniformly: any element with
  `data-confirm="Question?"` (a form or a clickable) is gated by an in-DOM dialog; on Confirm the
  element's real action proceeds, on Cancel it aborts, and Escape or a backdrop click cancels.
  `window.anthillConfirm(msg)` returns a `Promise<bool>` for the few script-driven call sites.
- R3. Every previously confirm-gated destructive action is converted to R2.
- R4. The model picker shows a working per-model Uninstall (the action is reversible, so a mis-click is
  guarded by a lightweight in-place step) with in-DOM feedback, never a native dialog.
- R5. The standalone model-storage list is removed: the `/models` "Installed on this device" table and
  the Settings "Model storage" card plus its Manage link. Uninstall lives only on each model in the picker.

## 3. Acceptance criteria

- In a packaged build, clicking a destructive control shows the in-DOM dialog; Confirm performs the
  action and Cancel aborts; model Uninstall removes the model and the UI reflects it.
- No rendered page relies on a native confirm for a destructive action.
- `/models` no longer renders an "Installed on this device" table; Settings has no "Model storage" card.
- The existing bespoke two-click confirmations (profiles, team settings, personalize, the council
  picker) keep working.
- `make test` is green, with tests updated to the new behavior.

## 4. Non-goals

- Adding `@tauri-apps/plugin-dialog`: the in-DOM approach needs no new native capability.
- Rewriting the already-working bespoke two-click confirmations.
