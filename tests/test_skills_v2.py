"""Skills v2: skill_md, bundled assets, conversational refine, scoped text review."""

from anthill.agent import skills as sk
from anthill.wiki.review import review_text


class _B:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kw):
        if callable(self.reply):
            raise self.reply()
        return self.reply


def test_skill_md_roundtrips():
    md = sk.skill_md("My Skill", "desc", "when", "do it", tier="org")
    parsed = sk.parse_skill_md(md, slug="my-skill")
    assert parsed.name == "My Skill" and parsed.tier == "org" and "do it" in parsed.instructions


def test_write_skill_with_assets(tmp_path):
    s2 = sk.write_skill(
        "Reporter",
        "d",
        "w",
        "run the script",
        directory=str(tmp_path),
        assets={"scripts/run.py": "print('hi')\n"},
    )
    assert "scripts/run.py" in s2.assets
    assert (tmp_path / "reporter" / "scripts" / "run.py").read_text() == "print('hi')\n"


def test_load_dir_discovers_assets(tmp_path):
    folder = tmp_path / "rep"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: Rep\n---\nbody")
    (folder / "data.csv").write_text("a,b")
    rep = next(s for s in sk._load_dir(tmp_path) if s.slug == "rep")
    assert "data.csv" in rep.assets and "SKILL.md" not in rep.assets


def test_write_assets_rejects_traversal(tmp_path):
    sk.write_skill("Safe", "d", "w", "i", directory=str(tmp_path), assets={"../escape.txt": "nope"})
    assert not (tmp_path / "escape.txt").exists()


def test_refine_skill_asks_a_question():
    b = _B(
        '{"name":"X","description":"","when_to_use":"","instructions":"",'
        '"question":"What output format?","ready":false}'
    )
    out = sk.refine_skill([{"role": "user", "content": "build a thing"}], b)
    assert out["question"] == "What output format?" and out["ready"] is False and out["name"] == "X"


def test_refine_skill_finalizes():
    b = _B(
        '{"name":"X","description":"d","when_to_use":"w","instructions":"steps",'
        '"question":"","ready":true}'
    )
    out = sk.refine_skill([{"role": "user", "content": "x"}], b)
    assert out["ready"] is True and out["instructions"] == "steps"


def test_refine_skill_handles_garbage():
    out = sk.refine_skill([{"role": "user", "content": "x"}], _B("not json"))
    assert out["ready"] is False


def test_review_text_clean_approves():
    out = review_text(
        _B('{"summary":"fine","flags":[]}'), "Be concise.", kind="principles", scope="org"
    )
    assert out.flags == [] and out.recommendation == "approve"


def test_review_text_fails_safe_when_model_down():
    out = review_text(_B(RuntimeError), "x", kind="skill", scope="org")
    assert "needs_edit" in out.flags
