# Task creation modal accessibility

Issue: #785

## Problem

The task create/edit overlay has no dialog semantics or keyboard focus management. Screen readers cannot reliably identify the overlay or associate several visible labels with their controls, and keyboard users can move focus behind it or lose their place after closing it.

## Requirements

- Use the native HTML modal dialog where supported by the browser and Tauri webview.
- Fall back without new dependencies when the webview lacks the native dialog API, preserving the same semantics, focus containment, dismissal paths, and focus restoration without raising the macOS 11 support floor.
- Expose dialog semantics and modal state, and give the dialog an accessible name from its visible heading.
- Preserve the New task, draft, example prefill, and edit opening paths and form behavior. Treat each opening as a distinct modal session, and move focus to the task title only when a new session opens.
- Discard both success and error outcomes from a draft request after a newer modal session starts, without changing the newer form.
- Keep Tab and Shift+Tab traversal inside the open dialog, and prevent its key events from reaching isolated background UI.
- Close with Escape, the close button, Cancel, or a click outside the dialog card.
- Return focus to the control that opened the current session. A queued native close event from an earlier session must not clear a newly reopened dialog's opener.
- Programmatically associate visible form labels with their controls and give unlabeled controls accessible names.
- Give the close button a meaningful accessible name.
- In the non-native fallback, hide and disable every background branch from assistive technology, pointer input, and focus while preserving and restoring its previous state on close.
- Keep the fallback full-viewport without relying on CSS `inset`, and stack it above existing global overlays such as Help and the product tour.

## Acceptance checks

Playwright drives the real Tasks page and verifies:

- dialog discovery by accessible name, initial focus, forward and reverse focus wrapping, dismissal paths, and focus return;
- New task, example prefill, draft, and edit ownership, including late success and failure responses plus close-then-reopen events;
- the non-native path with the native dialog API removed, including background semantics and focus isolation;
- full-viewport fallback layout without `inset` and visual stacking above pre-opened Help and tour overlays; and
- Escape and Enter remain contained when background UI was already open.
