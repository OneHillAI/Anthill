from anthill.common.text import first_h1, normalize_wiki_page, outbound_links, slugify
from anthill.wiki.lint import lint
from anthill.wiki.workspace import Workspace


def test_init_creates_structure(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    assert ws.exists()
    assert ws.raw.is_dir() and ws.inbox.is_dir() and ws.wiki.is_dir()
    assert ws.schema_md.exists() and ws.index_md.exists() and ws.log_md.exists()


def test_rebuild_index_lists_pages(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("Alpha Topic", "# Alpha Topic\n\nFirst summary line.\n\nSee [[beta-topic]].")
    ws.write_page("Beta Topic", "# Beta Topic\n\nSecond summary.")
    ws.rebuild_index()
    idx = ws.index_md.read_text()
    assert "[Alpha Topic](alpha-topic.md)" in idx
    assert "First summary line." in idx


def test_append_log(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.append_log("ingest", "Some Title")
    assert "ingest | Some Title" in ws.log_md.read_text()


def test_lint_flags_broken_and_orphan(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("Alpha", "# Alpha\n\nlinks to [[ghost]]")
    ws.write_page("Beta", "# Beta\n\nstands alone")
    kinds = {(f.kind, f.page) for f in lint(ws)}
    assert ("broken-link", "alpha") in kinds
    assert ("orphan", "beta") in kinds


def test_lint_clean_when_linked(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("Alpha", "# Alpha\n\npoints at [[beta]]")
    ws.write_page("Beta", "# Beta\n\npoints at [[alpha]]")
    assert lint(ws) == []


def test_normalize_wiki_page():
    # Related before H1 → gets reordered
    messy = "## Related\n[[b]]\n\n# Title\n\nSummary line.\n\nBody text."
    out = normalize_wiki_page(messy)
    assert out.startswith("# Title"), repr(out)
    assert "## Related" in out
    assert out.index("# Title") < out.index("## Related")

    # Duplicate Related sections get merged into one
    dupes = "# T\n\nBody.\n\n## Related\n[[a]]\n\n## Related\n[[b]]"
    out2 = normalize_wiki_page(dupes)
    assert out2.count("## Related") == 1
    assert "[[a]]" in out2 and "[[b]]" in out2


def test_text_helpers():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("   ") == "untitled"
    assert first_h1("# Title\nbody") == "Title"
    assert first_h1("no heading") is None
    assert outbound_links("see [[foo]] and [bar](baz.md) and [x](http://y.md)") == {"foo", "baz"}
