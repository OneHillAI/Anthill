"""The flat sidebar ends at a single Settings entry: Users, Integrations and Org settings all fold
into its "Organisation" card (the admin-only org hub), so the rail below the cut is not a stack of
admin items. Integrations is admin-managed there; members reach it through an admin.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add_all([admin, member])
    s.commit()
    return TestClient(app_mod.app), {"org": org.id, "admin": admin.id, "member": member.id}


def _auth(client, uid, org_id, role):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_integrations_is_not_a_rail_item_for_either_role(tmp_path, monkeypatch):
    # Integrations + Users fold into the single Settings entry (its "Organisation" card -> the org hub,
    # admin-only), so the rail below the cut ends at Settings. Neither the member tab (/integrations) nor
    # the admin tab (/connectors/mcp) is a rail item anymore.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], "member")
    member_nav = client.get("/integrations").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'href="/integrations"' not in member_nav
    assert 'href="/connectors/mcp"' not in member_nav

    _auth(client, ids["admin"], ids["org"], "admin")
    admin_nav = client.get("/settings").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'href="/connectors/mcp"' not in admin_nav  # not a rail item...
    assert 'href="/integrations"' not in admin_nav
    assert 'href="/users"' not in admin_nav
    # ...they live in the org hub, reached from the Settings page's "Organisation" card.
    hub = client.get("/settings/org").text
    assert 'href="/connectors/mcp"' in hub and 'href="/users"' in hub


def test_member_cannot_reach_the_org_hub(tmp_path, monkeypatch):
    # Integrations + Users are admin-managed now: members have no rail link and can't open the org hub.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], "member")
    r = client.get("/settings/org", follow_redirects=False)
    assert r.status_code in (302, 303, 403)


def test_member_cannot_open_connectors(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], "member")
    r = client.get("/connectors/mcp", follow_redirects=False)
    assert r.status_code in (302, 303, 403)  # admin-only route, members are turned away


# ── the task-based sidebar: admin items are gated per item (no "ADMIN" wall) ───────────
# Groups are by task (Workspace / General / Insights / Organization), and admin-only items
# are gated individually so related things sit together (Wiki + Wiki review, Metrics + Audit).

# Users + Integrations folded off the rail into the Settings -> Organisation hub, so these are no longer
# rail links for EITHER role - they must not appear in the sidebar nav. (Local-model + advanced /settings
# are folded into Solo settings; /backend + /wiki/review are Org-hub sub-pages / a Wiki sub-tab, also not
# rail links.)
_FOLDED_ADMIN_LINKS = [
    'href="/users"',
    'href="/connectors/mcp"',
]
# links everyone should see
_SHARED_LINKS = ['href="/chat"', 'href="/wiki"', 'href="/teams"']
# the rail is flat now (no task-group labels); a "cut" divider separates the work surfaces above from
# setup/admin below.
_NAV_CUT = 'class="navcut"'
_OLD_SECTION_LABELS = (">Workspace<", ">General<", ">Insights<", ">Organization<")


def test_member_nav_hides_admin_items(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], "member")
    r = client.get("/integrations")  # a member-accessible page that renders the sidebar
    assert r.status_code == 200
    for link in _FOLDED_ADMIN_LINKS:
        assert link not in r.text, link  # Users + Integrations folded off the rail (into Settings)
    for link in _SHARED_LINKS:
        assert link in r.text, link  # shared items still show
    assert _NAV_CUT in r.text  # a "cut" divider separates the work surfaces from setup/admin
    for label in _OLD_SECTION_LABELS:
        assert label not in r.text, label  # the old task-group labels are gone (flat rail)


def test_admin_rail_folds_users_and_integrations_into_settings(tmp_path, monkeypatch):
    # The below-cut rail ends at Settings for admins too: Users + Integrations are not rail links, but the
    # shared work surfaces still are, and the folded surfaces are reachable from the org hub.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    nav = client.get("/settings").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    for link in _FOLDED_ADMIN_LINKS:
        assert link not in nav, link  # folded off the rail into Settings -> Organisation
    for link in _SHARED_LINKS:
        assert link in nav, link  # the shared work surfaces still show
    hub = client.get("/settings/org").text
    for link in _FOLDED_ADMIN_LINKS:
        assert link in hub, link  # ...and are gathered in the org hub


def test_personal_settings_are_one_solo_home(tmp_path, monkeypatch):
    # The three overlapping personal entries (Solo settings + Local model + Settings) are folded into
    # ONE Solo settings rail item (one model per account); /models and advanced /settings are reached
    # from within it, not as their own rail links.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    nav = client.get("/").text.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'href="/personalize"' in nav  # the single Solo settings entry
    assert ">Local model<" not in nav  # folded into Solo settings
    assert (
        'href="/settings"' not in nav
    )  # advanced folded into Solo settings (still reachable there)


def test_dashboard_is_first_nav_item(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    r = client.get("/settings")
    nav = r.text.split("<nav>", 1)[1]
    # Flat rail: Dashboard (home) first, then the work surfaces, then the cut, then setup/admin.
    assert nav.index('href="/"') < nav.index('href="/chat"')  # Dashboard before Chat
    assert (
        nav.index('href="/chat"') < nav.index('class="navcut"') < nav.index('href="/personalize"')
    )  # work surfaces, then the cut, then Solo settings (setup/admin)


# The org-level config surfaces are consolidated into one "Org settings" hub now (P1); the individual
# pages still exist (with their shared sub-tab bar) but are reached from the hub, not the rail.
_ORG_SUBPAGES = ["/settings/general", "/backend", "/backup"]


def test_organization_page_has_sub_tabs(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    # the Organization page and its sibling sub-pages render a shared sub-tab bar
    for path in _ORG_SUBPAGES:
        r = client.get(path)
        assert r.status_code == 200 and 'class="settings-tabs"' in r.text, path
    # the org's config surfaces are consolidated into ONE "Org settings" hub now (P1); the individual
    # config pages moved off the rail and into the hub (Integrations stays its own rail item).
    nav = client.get("/connectors/mcp").text.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'href="/personalize"' in nav  # the single Settings entry (Org settings folds under it)
    assert 'href="/settings/org"' not in nav  # ...so Org settings is no longer its own rail item
    assert (
        'href="/connectors/mcp"' not in nav
    )  # Integrations folded into Settings -> Organisation too
    for folded in (
        'href="/settings/general"',
        'href="/settings/organization"',
        'href="/agent-access"',
    ):
        assert folded not in nav, folded  # folded into the Org settings hub
    # Org settings is reached from the Settings page (its "Organisation" card)...
    assert 'href="/settings/org"' in client.get("/personalize").text
    hub = client.get("/settings/org").text
    for item in ("/settings/general", "/settings/organization", "/agent-access", "/training"):
        assert item in hub, item  # ...and the hub gathers them all


def test_org_sub_tabs_use_plain_language(tmp_path, monkeypatch):
    # the Organization sub-tabs use plain, user-facing names
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    tabs = (
        client.get("/settings/general")
        .text.split('class="settings-tabs"', 1)[1]
        .split("</div>", 1)[0]
    )
    assert ">General<" in tabs and ">Backend<" in tabs and ">Backup<" in tabs


def test_nav_shows_org_name(tmp_path, monkeypatch):
    # a REAL org (a shared backend is configured) names itself under the brand so it is clearly *their*
    # Anthill; a personal workspace shows no such chrome (see test_org_name_optional, #481 reframed B).
    import anthill.web.app as app_mod
    from anthill.web.db import OrgSettings

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(OrgSettings(org_id=ids["org"], org_backend_status="validated"))  # -> is_org_mode true
    s.commit()
    _auth(client, ids["member"], ids["org"], "member")
    r = client.get("/integrations")
    assert r.status_code == 200
    assert 'class="brand-org"' in r.text and "Acme" in r.text
    # the wordmark is one flex child (a plain "Anthill" beside the mound mark), so the flex gap
    # can't open a space inside the word
    assert '<span class="wordmark">Anthill</span>' in r.text


def test_nav_links_user_team_workspace(tmp_path, monkeypatch):
    # a team the user belongs to is linked under Workspace for quick reach; a team they are
    # not on (or only invited to) is not.
    import anthill.web.app as app_mod
    from anthill.web.db import Team, TeamMembership

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    mine = Team(org_id=ids["org"], name="Platform", slug="platform", owner_id=ids["member"])
    other = Team(org_id=ids["org"], name="Secret Ops", slug="secret-ops", owner_id=ids["admin"])
    s.add_all([mine, other])
    s.flush()
    s.add(TeamMembership(team_id=mine.id, user_id=ids["member"], role="owner", status="active"))
    s.commit()
    mine_id = mine.id
    s.close()

    _auth(client, ids["member"], ids["org"], "member")
    r = client.get("/integrations")
    assert r.status_code == 200
    assert f'href="/wiki/team/{mine_id}"' in r.text and "Platform" in r.text
    assert "Secret Ops" not in r.text  # not a member -> not linked


def test_team_review_does_not_inflate_org_badge(tmp_path, monkeypatch):
    # Regression: a pending team-scope draft used to bump the org "Wiki review" badge, but
    # /wiki/review lists org-scope drafts only - so the badge said "1" while the page said
    # "nothing to do." The badge count and the page must agree.
    import anthill.web.app as app_mod
    from anthill.web.db import Team, WikiReview

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["org"], name="Platform", slug="platform", owner_id=ids["admin"])
    s.add(team)
    s.flush()
    s.add(
        WikiReview(
            org_id=ids["org"], slug="team-doc", content="c", target_scope="team", team_id=team.id
        )
    )
    s.commit()
    s.close()

    _auth(client, ids["admin"], ids["org"], "admin")
    page = client.get("/wiki/review").text
    assert "No pending wiki updates" in page  # the page has nothing to do...
    assert 'class="nav-badge"' not in page  # ...so the badge must not claim otherwise


def test_org_review_badge_counts_only_org_scope(tmp_path, monkeypatch):
    # With one org draft and one team draft pending, the org badge counts the org draft only,
    # and the review page lists exactly that one.
    import anthill.web.app as app_mod
    from anthill.web.db import Team, WikiReview

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["org"], name="Platform", slug="platform", owner_id=ids["admin"])
    s.add(team)
    s.flush()
    s.add(WikiReview(org_id=ids["org"], slug="org-doc", content="c"))  # org scope (default)
    s.add(
        WikiReview(
            org_id=ids["org"], slug="team-doc", content="c", target_scope="team", team_id=team.id
        )
    )
    s.commit()
    s.close()

    _auth(client, ids["admin"], ids["org"], "admin")
    page = client.get("/wiki/review").text
    assert '<span class="nav-badge">1</span>' in page  # counts the org draft only, not 2
    assert '<span class="nav-badge">2</span>' not in page
    assert "org-doc" in page and "team-doc" not in page  # page lists only the org draft


def test_workspace_sidebar_groups_collapsed_by_default(tmp_path, monkeypatch):
    # On a workspace page (chat/tasks/agents) the lower groups (General/Insights/Organization/Help)
    # are foldable and start COLLAPSED so the work is the focus; a single click reopens them. The
    # items stay in the DOM (CSS hides them until expanded), so nothing is lost - just tucked away.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    r = client.get("/chat", follow_redirects=True)
    assert r.status_code == 200
    assert (
        "workspace-mode" in r.text and 'class="nav-group nav-more"' in r.text
    )  # secondary nav folds into "More"
    assert 'nav-more" open' not in r.text  # ...and collapsed by default
    # the below-cut items are still present (hidden by CSS until the group is expanded), not removed
    assert 'href="/personalize"' in r.text and 'class="navcut"' in r.text


def test_non_workspace_sidebar_is_not_foldable(tmp_path, monkeypatch):
    # Outside a workspace (e.g. Settings) the groups are always open - no folding, no workspace-mode.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    r = client.get("/settings")
    assert r.status_code == 200
    assert "workspace-mode" not in r.text and "nav-more" not in r.text


def test_org_cloud_model_page_is_reachable_from_the_rail(tmp_path, monkeypatch):
    # the org cloud + served-model page stays reachable from the rail via the Org settings hub (P1),
    # not buried
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    assert 'href="/personalize"' in client.get("/").text  # the Settings entry is on the rail...
    assert (
        'href="/settings/org"' in client.get("/personalize").text
    )  # ...which reaches Org settings
    hub = client.get("/settings/org").text
    assert (
        'href="/settings/organization"' in hub and "Cloud &amp; model" in hub
    )  # ...links Cloud & model


def test_knowledge_surfaces_are_one_rail_entry(tmp_path, monkeypatch):
    # Wiki, Snippets, Memory and Skills fold into ONE "Knowledge" rail entry; they are no longer four
    # separate rail items but sub-tabs of a shared Knowledge bar (_knowledge_tabs.html) rendered on each
    # of the four surfaces.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    nav = client.get("/").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert ">Knowledge<" in nav  # the single Knowledge rail entry...
    assert 'href="/wiki"' in nav  # ...points at the wiki (the hub's first tab)
    for gone in (">Snippets<", ">Memory<", ">Skills<"):
        assert gone not in nav, gone  # the old four separate rail labels are gone from the rail
    # each of the four surfaces renders the shared Knowledge tab bar (all four tabs present)
    for path in ("/wiki", "/snippets", "/memory", "/skills"):
        page = client.get(path).text
        for tab in ('href="/wiki"', 'href="/snippets"', 'href="/memory"', 'href="/skills"'):
            assert tab in page, (path, tab)


def test_help_links_moved_from_rail_to_footer(tmp_path, monkeypatch):
    # Help (Contribute / How it works / Setup / Take a tour) is no longer a rail group; it lives in the
    # sidebar footer, so the main nav stays task-focused.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    r = client.get("/settings")
    nav = r.text.split("<nav", 1)[1].split("</nav>", 1)[0]
    footer = r.text.split('class="sidebar-footer"', 1)[1]
    assert ">Help<" not in nav  # the Help nav-group label is gone from the rail...
    assert 'href="/contribute"' not in nav  # ...and so are its links
    for link in ('href="/contribute"', 'href="/docs/how-it-works"', 'href="/docs/setup"'):
        assert link in footer, link  # they now live in the footer Help menu


def test_projects_is_in_the_lower_group_with_settings(tmp_path, monkeypatch):
    # Projects (/teams) sits in the lower group (below the cut) with Knowledge and Settings, apart from the
    # daily work surfaces (Chat/Tasks/Agents) above - still available to non-admin members.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], "member")
    nav = client.get("/integrations").text.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'href="/teams"' in nav  # a non-admin member still sees Projects...
    assert nav.index('class="navcut"') < nav.index('href="/teams"')  # ...now below the cut...
    assert nav.index('href="/teams"') < nav.index(
        'href="/personalize"'
    )  # ...grouped before Settings


def test_settings_shows_sub_items_in_the_rail(tmp_path, monkeypatch):
    # When on the Settings page, its sub-sections show as rail sub-items (Model / Knowledge /
    # Personality / This device / Privacy / Organisation), linking to the corresponding tab; they don't
    # show off the Settings page.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    on = client.get("/personalize").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'class="settings-subrail"' in on
    for href in (
        "/personalize#model",
        "/personalize#knowledge",
        "/personalize#personality",
        "/personalize#device",
        "/personalize#privacy",
        "/personalize#org",
    ):
        assert f'href="{href}"' in on, href
    off = client.get("/integrations").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'class="settings-subrail"' not in off  # only on the Settings page itself


def test_footer_is_a_chip_plus_help_overflow(tmp_path, monkeypatch):
    # The footer is a compact account chip + Sign out, with Help/How-it-works/Setup/Take-a-tour tucked into
    # a "..." overflow menu - not a flat pile of links.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    footer = client.get("/settings").text.split('class="sidebar-footer"', 1)[1]
    assert 'class="footer-account"' in footer  # the account chip
    assert 'href="/logout"' in footer  # sign out
    assert 'class="footer-help-menu"' in footer  # the "..." overflow holds the help links
    assert 'onclick="toggleHelpMenu' in footer  # ...opened by the "..." button


def test_metrics_and_audit_are_not_rail_items(tmp_path, monkeypatch):
    # Metrics and Audit are org-level insights, not rail items - they live on the admin Dashboard and
    # under Org settings -> Insights, not in the sidebar.
    client, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"], "admin")
    nav = client.get("/settings").text.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'href="/metrics"' not in nav and 'href="/audit"' not in nav  # gone from the rail
    hub = client.get("/settings/org").text
    assert 'href="/metrics"' in hub and 'href="/audit"' in hub  # ...under Org settings (Insights)
    dash = client.get("/").text
    assert 'href="/metrics"' in dash and 'href="/audit"' in dash  # ...and on the admin dashboard
