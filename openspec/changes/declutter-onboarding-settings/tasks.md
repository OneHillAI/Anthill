# Tasks: declutter the onboarding/settings surface

## Build steps

1. `anthill/web/templates/setup_account_type.html`, `model_picker.html`: convert from standalone
   `<!DOCTYPE html>` documents to `{% extends "base.html" %}` / `{% block content %}` (mirror
   `dashboard.html`'s exact structure: `{% block title %}`, `{% block page_title %}`). Keep the existing
   centered-card content inside the content block, wrapped in a max-width container (matching
   style.css's `.card` convention) instead of `.auth-wrap`/`.auth-box`. Do NOT touch `setup.html` (step
   1, pre-auth, correctly stays standalone).
2. `anthill/web/app.py`: `setup_account_type_get` and `model_picker_get` add `"nav_locked": True` to
   their returned template context dicts.
3. `anthill/web/templates/_sidebar.html`: extend the existing conditional class logic on `<nav>` to add
   a `nav-locked` class when `nav_locked` is set in context.
4. `anthill/web/static/style.css`: add `.sidebar nav.nav-locked a { pointer-events:none; opacity:.45;
   cursor:not-allowed; }`. Do not touch `.sidebar-footer` (sign-out/profile must keep working).
5. `anthill/web/templates/model_picker.html`: rename the `<details><summary>` text from
   "Advanced: pick a model manually" to "Advanced settings" (local branch only - no structural change).
6. `anthill/web/app.py`, `model_picker_post`: in the `if compute in ("cloud", "mac_mini"):` branch,
   before the redirect, set `cfg.local_model_chosen = True` and a new `cfg.solo_compute = compute`
   (mirroring the field name convention `/personalize`/`/settings` already use), then `db.commit()`.
   Requires a new `OrgSettings.solo_compute` column (String, default `""`) in `anthill/web/db.py` +
   `_ensure_columns` migration entry, matching the existing convention for small string-flag columns.
7. `anthill/web/templates/settings_organization.html`: restructure into default vs. collapsed
   `<details class="card"><summary>Advanced settings</summary>`, per proposal.md's exact list (provider
   green/amber paragraph, GPU-size math paragraph, "Models are sized to..." paragraph, warm workers,
   quantization toggle + its paragraph, Lambda instance-type/region, HF token field, the entire Council
   reviewers card, "Review scheduled tasks & agent runs," AWS's checklist/extra-fields/IAM-policy
   blocks, the non-default provider `<option>`s (see task 8), and the "connect a server you already run"
   `<details>` relocated under/near Advanced). Shorten the intro paragraph. Verify NO `id`/`name`/JS-hook
   changes are needed - confirm `syncProvider()`/`applyModelStates()`/`filterGpusByModel()` still target
   the same element ids after the restructuring (they should, since this is pure `<details>` wrapping).
8. Provider `<select>` in `settings_organization.html`: group non-default providers (DataCrunch/Verda,
   OVHcloud, Scaleway, AWS, GCP, Azure, IBM) into an `<optgroup label="Advanced - not fully wired up
   yet">` nested inside (or immediately following) the Advanced settings `<details>`, OR keep the
   `<select>` itself always fully populated (for the existing-test reason in task 12) but visually
   split via two `<optgroup>`s ("Ready to use": On-prem/Lambda/RunPod; "Advanced (plan preview only)":
   the rest) - confirm which approach keeps `settings_org_get`'s `groups`/context variable usage intact
   before choosing.
9. New `anthill/web/templates/_compute_banner.html`: a macro taking `compute_ready: bool`,
   `setup_href: str | None` - renders nothing when ready, a `<div class="alert alert-info">` with a
   real `<a href="{{ setup_href }}">` when set, plain "Ask an admin to finish setup" text when `None`.
10. `anthill/web/templates/chat.html`: fix `setOrgChatEnabled`'s "not connected" branch to build
    `banner.innerHTML` (not `.textContent`) including a real `<a href="/settings/organization">` when
    the current user's role allows it (role already available in the page's rendered context/JS - no
    new endpoint needed), matching how `setSoloCloudStatus` already builds clickable `innerHTML` nearby.
11. `anthill/web/app.py`, `_wiki_ctx`: add `cfg = _cfg(db, org)` and
    `solo = normalize_topology(getattr(cfg, "deployment_topology", "") if cfg else "") == "solo"` to its
    returned context (both helpers already used elsewhere in app.py).
    `anthill/web/templates/wiki.html`: import and render `_compute_banner.html`'s macro near the top of
    the content block, passing the appropriate `compute_ready`/`setup_href` for solo vs org.
12. `anthill/web/app.py`, dashboard route: move the compute-readiness check out from under
    `if user.get("role") == "admin":`, compute it for every user, branch on `solo` (solo: `cfg and
    cfg.local_model_chosen and (cfg.solo_compute != "cloud" or planes.org_available(cfg))`; org:
    `planes.org_available(cfg)`), append to `activation` with `href=None`/`cta="Ask an admin"` when
    non-admin and not ready. Widen `activation_done`'s computation the same way.
    `anthill/web/templates/dashboard.html`: move the "Get your org running" checklist render out from
    under `{% if user.role == 'admin' %}` (rest of that admin section - Needs your attention, Quick
    actions - stays admin-gated). Guard `<a href="{{ s.href }}">` for a falsy `href` (render `{{ s.cta
    }}` as plain text instead).
13. Tests: extend/add coverage for the real bugs fixed (cloud/mac_mini setting `local_model_chosen`;
    non-admin dashboard CTA; chat.html banner link) and the restructured `settings_organization.html`
    (advanced-settings collapse present, minimal default fields, existing save/provision behavior
    unchanged). Confirm `tests/test_org_provisioning.py::test_get_renders_provider_picker_for_admin`
    still passes as-is (every provider still has a rendered `<option>`, just grouped/collapsed).
14. `docs/SYSTEM_IMPACT_LOG.md`: add a new entry for this PR (do not edit historical entries).
15. `changelog.d/+declutter-onboarding.changed.md`.
16. `ruff check`, `ruff format --check`, `mypy`, full test suite - must be green.
17. Live verification in the browser (per proposal.md's acceptance criteria 1-5), using the
    `anthill-audit` preview server already configured, mirroring exactly how the original mess was
    found.

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section - the three-way settings-page consolidation,
Wiki/Training redesign, DataCrunch/Verda's `startup_script_id` fix, Koyeb evaluation, provisioning/
backend logic changes, server-side wizard-order enforcement.
