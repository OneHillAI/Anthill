# Spec: Treat an exposed password as compromised (force-reset)

Status: implemented. Lane: `pillar:privacy`. Issue: follow-up to #591 (and 7db8cf5, which finished the
form coverage). This adds the missing piece: the credential-exposure remediation.

## Problem

#591 (and the follow-up 7db8cf5) fixed the CAUSE and the copy: autocomplete on every auth/password form,
a server guard dropping a name equal to the password on setup/invite and `/account/name`, and a startup
pass that scrubs any display_name that IS the password. But it treated the leak as a display bug.

It is not. A password that was stored AND rendered in cleartext (as the user's display name, visible to
other org members, and frozen into any pre-migration DB snapshot on disk) is EXPOSED. Setting
`display_name = email` removes the live copy but does not un-leak the secret. The account must rotate the
password, or a still-valid credential that was shown in cleartext keeps working.

## Requirements

- Treat a detected exposed password as compromised: force the account to set a new one, and tell the user.
- Never lock anyone out (must work for solo/local accounts with no email).
- Cover the reset flow both ways: force it, and clear the flag once the password is actually rotated.

## Design

- `User.must_reset_password` (additive column, default False, so normal accounts are unaffected).
- Startup remediation (`_scrub_password_display_names`): on a detected leak, in addition to scrubbing the
  copy, set `must_reset_password = True` and `notify()` the user. Audit event
  `security.password_display_name_remediated`.
- `login_post`: after the user authenticates, if `must_reset_password`, issue a reset token and redirect
  to `/reset/<token>?exposed=1` - a session is NEVER issued on the exposed password. No lockout: the user
  just proved they know the old password, so they set a new one immediately; the reset page explains why.
- `reset_post` and the change-password handler clear the flag when the password is rotated.

## Why force-reset, not just scrub

Rotation is what actually neutralises the exposure. Once the user sets a new password, the leaked value is
a dead credential everywhere it landed - the live DB, on-screen history, exports, and any pre-migration
snapshot on disk. Scrubbing the display_name alone leaves a still-valid password that was shown in the clear.

## Residual

Pre-migration snapshots captured while a leaked display_name existed still contain the old cleartext
password. Force-reset makes it inert (it no longer authenticates); operators should still purge stale
snapshots as housekeeping.

## Verification

`tests/test_password_display_leak.py`: the remediation forces a reset + notifies; a compromised login is
redirected to reset with NO session; a normal login is unaffected (regression guard); reset clears the
flag. Plus the auth/security regression suite (login, JWT live-revocation, rate-limit, OAuth, teams,
signup, audit) green.
