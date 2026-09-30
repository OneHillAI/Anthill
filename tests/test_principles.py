"""Guiding principles + scoped skills + the injection chokepoint.

Principles are always-on (every answer + every agent identity); skills are tiered by
scope but still capability-gated. These cover assembly/caps, scoped loading, the
executor injection, the ask() path, and a coverage guard so no AgentExecutor can be
constructed without principles.
"""

import re

from anthill.wiki.workspace import Workspace


def _ws(tmp_path, name, text=None):
    ws = Workspace(tmp_path / name)
    ws.init()
    if text is not None:
        ws.principles_md.write_text(text)
    return ws


def test_read_principles_strips_template(tmp_path):
    from anthill.wiki.principles import read_principles

    blank = _ws(tmp_path, "blank")  # seeded with comment-only template
    assert read_principles(blank) == ""
    real = _ws(tmp_path, "real", "Be precise and cite sources.")
    assert read_principles(real) == "Be precise and cite sources."


def test_assemble_principles_precedence_order(tmp_path):
    from anthill.wiki.principles import assemble_principles

    org = _ws(tmp_path, "org", "Org rule: be precise.")
    team = _ws(tmp_path, "team", "Team rule: use bullets.")
    me = _ws(tmp_path, "me", "My rule: be terse.")
    out = assemble_principles([("Organization", org), ("Team 1", team), ("Personal", me)])
    assert out.index("Org rule") < out.index("Team rule") < out.index("My rule")


def test_assemble_truncates_lowest_precedence_first(tmp_path):
    from anthill.wiki.principles import assemble_principles

    org = _ws(tmp_path, "o", "O" * 1600)  # capped to 1500 each by read_principles
    team = _ws(tmp_path, "t", "T" * 1600)
    me = _ws(tmp_path, "m", "PERSONALRULE")
    out = assemble_principles([("Organization", org), ("Team", team), ("Personal", me)])
    assert "PERSONALRULE" not in out  # personal dropped when org+team fill the budget
    assert "truncated" in out.lower()


def test_load_skills_scoped_tags_origin(tmp_path, monkeypatch):
    from anthill.agent.skills import load_skills, write_skill

    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(tmp_path / "builtin"))
    write_skill("Builtin One", "d", "when", "do the thing")  # builtin dir
    team = _ws(tmp_path, "team")
    (team.skills / "team-skill").mkdir(parents=True)
    (team.skills / "team-skill" / "SKILL.md").write_text("---\nname: Team Skill\n---\nbody")
    by_name = {s.name: s.tier for s in load_skills(scoped=[("team", team)])}
    assert by_name.get("Builtin One") == "builtin"
    assert by_name.get("Team Skill") == "team"


def test_executor_injects_principles():
    from anthill.agent.executor import AgentExecutor

    ex = AgentExecutor(None, [], principles="ALWAYS be kind.")
    system = ex._build_initial_messages("do a thing", "")[0].content
    assert "Standing principles" in system and "ALWAYS be kind." in system


def test_ask_injects_principles(tmp_path, monkeypatch):
    import numpy as np

    import anthill.wiki.ask as ask_mod

    monkeypatch.setattr(ask_mod.emb, "embed", lambda *a, **k: np.zeros(4, dtype="float32"))

    class _Cache:
        def __init__(self, **k):
            pass

        def lookup(self, q):
            return None

        def store(self, q, a, slugs=None):
            pass

    monkeypatch.setattr(ask_mod, "SemanticCache", _Cache)

    grabbed = {}

    class _Backend:
        def chat(self, messages, **k):
            grabbed["t"] = "\n".join(getattr(m, "content", "") for m in messages)
            return "ok"

    ws = _ws(tmp_path, "w")
    ask_mod.ask(ws, "what is our policy?", _Backend(), principles="Never expose secrets.")
    assert "Never expose secrets." in grabbed["t"]


def test_every_executor_construction_passes_principles():
    """Coverage guard: principles injection is keyed by scope, so no agent may be
    built without it. Fails if any AgentExecutor(...) omits principles=."""
    import inspect

    import anthill.web.app as app_mod
    import anthill.web.scheduler as sched_mod

    for mod in (app_mod, sched_mod):
        src = inspect.getsource(mod)
        assert "agent_context_for" in src, f"{mod.__name__} must use the chokepoint"
        for m in re.finditer(r"AgentExecutor\(", src):
            window = src[m.start() : m.start() + 400]
            assert "principles=" in window, f"AgentExecutor without principles in {mod.__name__}"
