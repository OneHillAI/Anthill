# First launch: one look from the splash to the first sign-in

Lane: `pillar:platform`

## Problem

A person who has just downloaded Anthill meets, in order, the installer, the desktop splash, the first-run setup
page and then sign-in. The splash and the error pages carry the hill and ants illustration. Setup and sign-in were a
plain white card, and forgot, reset, invite and confirm-email pages carried no brand at all. Copy and placeholders
differed from page to page (sign-in said "Your organisation's private AI - by Onehill Foundation" to a solo user,
"Onehill" was spelled two ways, the email placeholder was `you@company.com` on one page and `you@example.com` on
another), cards were different widths, none of the form labels was tied to its field, and the sign-in page
offered "Sign up" even where sign-up is by invitation only. The splash used the system font, the app uses Plus
Jakarta Sans, and the installer printed a robot emoji and "Installing anthill (admin)".

## Fix

1. **One brand block.** `_auth_brand.html` draws the hill and the name "Anthill" at the top of the card and is
   included by setup, sign in, forgot password, choose a new password, accept an invitation and confirm your email.
   The hill is the one used by the desktop splash and the error pages.
2. **One card width** (480 px) for all of them, set in `style.css` instead of per page.
3. **One voice and one set of placeholders.** Sign in reads "Sign in" / "Your own private AI." The email placeholder
   is `you@example.com` and the new-password placeholder is "At least 12 characters" on every page. Sign-in's
   password placeholder is "Your password".
4. **Labels tied to fields.** Every visible field on those pages has an `id` and a `<label for>`.
5. **Sign-up link only when it works.** Sign in shows "New here? Create an account" only when `signup_is_open`
   is true, on the first view and after a failed attempt.
6. **The splash uses the app's font.** The same Plus Jakarta Sans file is embedded in the splash (it cannot fetch
   `/static` before the backend is up), so the hand-off to the first form does not change the letters. The
   playful line "We're on our way!" stays: nothing is being asked of the person yet.
7. **The progress bar fits the card.** The four-step bar (account, solo or organisation, choose your AI, connect a
   provider) did not fit the 480 px card on the sign-up and solo-or-organisation pages and stuck out past its edge.
   In a card narrower than 430 px it now shows the label of the current step only, with all four numbered markers.
   Wider cards (the model page) keep every label.
8. **The "Get started" card always offers a way out.** "Already have an account? Sign in - Forgot your password?" shows
   on the card in every state (first run, later accounts, and when sign-up is by invitation), so nobody who already has
   an account is stuck on "Get started". On a brand-new install with no account, "Sign in" leads back to this same
   page, because `/login` sends an install with no account to `/setup`.
9. **The installer** says "Installing Anthill" with no emoji and no "(admin)", and "Anthill is installed".

## Acceptance criteria

- Each of the six pages contains one `auth-brand` block with the hill and the wordmark.
- Every visible input on them has an `id` matched by a `<label for>`.
- No page contains `you@company.com`, `min 12 characters` or `Onehill`.
- `.auth-box` has one `max-width` (480 px) and no page overrides it.
- Sign in shows the sign-up link only while sign-up is open, including after a failed sign-in.
- The setup card always has "Sign in" and "Forgot your password?" links.
- The splash embeds the font and loads nothing from the network.
- `tests/test_auth_pages_one_look.py`.
