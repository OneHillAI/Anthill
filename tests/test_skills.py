"""Agent Skills: parse, load, match, system-prompt, write, AI-draft (no network)."""

from anthill.agent import skills as sk


def test_parse_with_frontmatter():
    text = (
        "---\nname: Investor Update\ndescription: weekly update\n"
        "when_to_use: when drafting the update\nscopes: wiki, web\n---\n"
        "Do step one.\nDo step two."
    )
    s = sk.parse_skill_md(text, slug="investor-update")
    assert s.name == "Investor Update"
    assert s.description == "weekly update"
    assert s.when_to_use == "when drafting the update"
    assert s.scopes == ["wiki", "web"]
    assert "step one" in s.instructions and s.instructions.startswith("Do step one")


def test_parse_without_frontmatter_derives_name():
    s = sk.parse_skill_md("Just do the thing.", slug="do-the-thing")
    assert s.name == "Do The Thing"
    assert s.instructions == "Just do the thing."


def test_load_skills(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "SKILL.md").write_text(
        "---\nname: Alpha\ndescription: handle invoices\n---\nInvoice steps."
    )
    (tmp_path / "beta.md").write_text("---\nname: Beta\n---\nBeta steps.")
    loaded = sk.load_skills(str(tmp_path))
    names = {s.name for s in loaded}
    assert {"Alpha", "Beta"} <= names


def test_load_skills_missing_dir_is_empty():
    assert sk.load_skills("/nonexistent/skills/dir") == []


def test_match_skills_picks_relevant():
    skills = [
        sk.Skill(
            slug="invoice", name="Invoice handling", description="process invoices and billing"
        ),
        sk.Skill(slug="travel", name="Travel booking", description="book flights and hotels"),
    ]
    hits = sk.match_skills(skills, "help me process this billing invoice", k=1)
    assert len(hits) == 1 and hits[0].slug == "invoice"


def test_match_skills_none_when_irrelevant():
    skills = [sk.Skill(slug="travel", name="Travel booking", description="flights and hotels")]
    assert sk.match_skills(skills, "what is the capital of France") == []


def test_system_prompt_includes_instructions():
    skills = [sk.Skill(slug="x", name="X", instructions="Follow these exact steps.")]
    p = sk.skills_system_prompt(skills)
    assert "Follow these exact steps." in p and "Skill: X" in p
    assert sk.skills_system_prompt([]) == ""


def test_write_and_reload_roundtrip(tmp_path):
    sk.write_skill(
        "My Skill",
        "does things",
        "when needed",
        "Step A.\nStep B.",
        scopes=["wiki"],
        directory=str(tmp_path),
    )
    assert (tmp_path / "my-skill" / "SKILL.md").is_file()
    reloaded = sk.load_skills(str(tmp_path))
    assert len(reloaded) == 1
    assert reloaded[0].name == "My Skill" and "Step A." in reloaded[0].instructions
    assert reloaded[0].scopes == ["wiki"]


class _Backend:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply


def test_draft_skill_parses_json():
    b = _Backend(
        '{"name":"Refund","description":"process refunds",'
        '"when_to_use":"on refund requests","instructions":"Check policy then refund."}'
    )
    d = sk.draft_skill("how to handle refunds", b)
    assert d["name"] == "Refund" and "refund" in d["instructions"].lower()


def test_draft_skill_falls_back_on_bad_output():
    d = sk.draft_skill("handle returns", _Backend("not json"))
    assert d["name"] and d["instructions"] == "handle returns"


def test_executor_enforces_skill_scopes():
    """A matched skill is only injected if the agent identity holds its scopes."""
    from anthill.agent.executor import AgentExecutor
    from anthill.agent.tools import AgentPrincipal

    skills = [
        sk.Skill(
            slug="slack-digest",
            name="Slack digest",
            description="post a summary to slack",
            instructions="Use Slack.",
            scopes=["slack"],
        ),
        sk.Skill(
            slug="wiki-note",
            name="Wiki note",
            description="write a slack summary note",
            instructions="Write a wiki note.",
            scopes=["wiki"],
        ),
    ]
    ident = AgentPrincipal(name="t", scopes={"wiki", "web"})  # no slack
    ex = AgentExecutor(_Backend("x"), [], skills=skills, identity=ident)
    sys = ex._build_initial_messages("post a slack summary", "")[0].content
    assert "Write a wiki note." in sys  # wiki skill allowed
    assert "Use Slack." not in sys  # slack skill withheld (no scope)
