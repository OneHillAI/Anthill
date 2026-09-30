# Security: password must never be stored as the display name

## Problem

On the account-creation forms - the invite-accept form (`invite.html`) and first-run setup
(`setup.html`) - an optional **name** text field sits directly above the **password** field, and none of
the auth forms carried `autocomplete` attributes. Without those hints, a browser or password manager can
misfill the name field with the password (or a user, landing on a "set a password" page with the cursor
auto-focused on the first field, types the password there). The server then stored that value as
`display_name` in cleartext, so a user's password leaked as their visible display name.

Passwords themselves are, and remain, bcrypt-hashed (`crypto.hash_password`, cost 12) - only the
`display_name` field leaked the plaintext.

## Fixes (defence in depth)

1. **Server-side guard (the guarantee).** In `invite_post` and `setup_post`, a submitted name equal to the
   password is dropped and falls back to the email, so a password can never be stored as a display name
   regardless of how the field was filled.
2. **`autocomplete` hints (fix at the source).** Every auth form now types its fields for the browser and
   password manager: email `autocomplete="username"`, the name field `autocomplete="name"`, a new password
   `autocomplete="new-password"`, and the login password `autocomplete="current-password"`. The
   invite form's autofocus moves from the optional name field to the password field. This stops the
   misfill before it happens.
3. **Startup remediation (existing leaks).** `_scrub_password_display_names()` runs at startup: for each
   password user whose display name is at least the 12-char password minimum and is not their email, it
   bcrypt-verifies the display name against the stored password hash; a match means the display name IS the
   password, so it is scrubbed back to the email. Idempotent (self-heals, does nothing on later starts);
   shorter names skip the bcrypt check.

## Acceptance criteria

- Posting the invite/setup form with the name field equal to the password stores the email as the display
  name, never the password.
- A pre-existing user whose display name equals their password has it scrubbed to their email at startup; a
  normal display name (even a long one) is left untouched.
- The auth forms carry the correct `autocomplete` attributes; the invite form focuses the password field.
- Password storage is unchanged (bcrypt); this only concerns the `display_name` field.

## Coverage completed (follow-up to the initial fix)

The first fix covered the invite + setup forms. The same class of surface is now fully covered:

- **`autocomplete` on every password field:** the change-password forms (`account.html`, `profile.html`),
  the password reset form (`reset.html`), and login. Secret/API-key fields already carried
  `autocomplete="off"`.
- **The display-name edit form (`/account/name`, on `profile.html`) is guarded too.** That form has no
  password field to compare against, so its handler bcrypt-verifies the submitted name against the user's
  stored password hash and, on a match, refuses it and falls back to the email - the same protection as
  the startup scrub, applied at write time.
