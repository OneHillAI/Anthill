# Tasks

- [x] Make `opt-web`'s default `checked` attribute conditional on `active_conv.plane != 'solo'`.
- [x] Give the trust-banner text its own `id="chat-trust-msg"` span and sync it via JS to the live
  checkbox state (on load and on `change`).
- [x] Update the tooltip/comment text around the toggle to be accurate for both planes.
- [x] Tests: Solo renders unchecked, Org renders checked, Solo banner server-default text, Org has no
  `chat-trust-msg` element.
- [x] Full local validation (`ruff`, `mypy`, `pytest`) + live browser verification (toggle updates banner
  live; reload restores the per-browser saved preference).
