"""Task access control (#597): a task is OWNED (created_by + plane), not org-wide. A peer member cannot
view or mutate another member's Solo task; an org-plane task is shared read (org member) but written only
by its creator or an org admin. Mirrors the agent access model. Model-free."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, ScheduledTask, User


def _setup(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    b = User(org_id=org.id, email="b@a.co", role="member", active=True)
    d = User(org_id=org.id, email="d@a.co", role="member", active=True)
    admin = User(org_id=org.id, email="admin@a.co", role="admin", active=True)
    s.add_all([b, d, admin])
    s.commit()
    return s, org, b, d, admin


def _u(user):  # the token-dict shape the helpers expect
    return {"sub": str(user.id), "org": user.org_id, "role": user.role}


def test_solo_task_is_private_to_its_creator(tmp_path):
    from anthill.web.app import _task_for_write, _task_visible

    s, org, b, d, admin = _setup(tmp_path)
    t = ScheduledTask(org_id=org.id, created_by=b.id, title="B solo", goal="g", plane="solo")
    s.add(t)
    s.commit()
    # the creator may view + write
    assert _task_visible(s, t.id, org, _u(b)) is not None
    assert _task_for_write(s, t.id, org, _u(b)) is not None
    # a peer member may do NEITHER (the IDOR that #597 fixed)
    assert _task_visible(s, t.id, org, _u(d)) is None
    assert _task_for_write(s, t.id, org, _u(d)) is None
    # not even the org admin may read/write a member's private Solo task
    assert _task_visible(s, t.id, org, _u(admin)) is None
    assert _task_for_write(s, t.id, org, _u(admin)) is None


def test_org_plane_task_is_shared_read_but_admin_write(tmp_path):
    from anthill.web.app import _task_for_write, _task_visible

    s, org, b, d, admin = _setup(tmp_path)
    t = ScheduledTask(org_id=org.id, created_by=b.id, title="org", goal="g", plane="org")
    s.add(t)
    s.commit()
    assert (
        _task_visible(s, t.id, org, _u(d)) is not None
    )  # any org member may VIEW an org-plane task
    assert _task_for_write(s, t.id, org, _u(d)) is None  # but a plain member may not WRITE it
    assert _task_for_write(s, t.id, org, _u(admin)) is not None  # an admin may
    assert _task_for_write(s, t.id, org, _u(b)) is not None  # and the creator may


def test_team_plane_task_is_shared_with_its_project_members(tmp_path):
    # #597 + review: a team-plane task is shared team infra - an active member of the project may VIEW and
    # OPERATE it (run/queue), but only the creator/admin may WRITE (edit/cancel); a non-member sees nothing.
    from anthill.web.app import _task_for_operate, _task_for_write, _task_visible
    from anthill.web.db import Team, TeamMembership

    s, org, b, d, admin = _setup(tmp_path)
    team = Team(org_id=org.id, name="Proj", slug="proj", owner_id=b.id)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=d.id, status="active", role="member"))
    s.commit()
    t = ScheduledTask(
        org_id=org.id, created_by=b.id, title="team task", goal="g", plane="team", team_id=team.id
    )
    s.add(t)
    s.commit()
    assert _task_visible(s, t.id, org, _u(d)) is not None  # a project member may view
    assert _task_for_operate(s, t.id, org, _u(d)) is not None  # and run/queue it (shared infra)
    assert _task_for_write(s, t.id, org, _u(d)) is None  # but not edit/cancel it
    assert _task_visible(s, t.id, org, _u(admin)) is None  # a non-member (even admin) sees nothing
