"""The changelog-fragment assembler: fragments -> a grouped, ordered release section, dir cleared (#513).

Model-free unit tests for scripts/build_changelog.py, pointed at a temp changelog.d/ + CHANGELOG.md.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_changelog.py"


def _load(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("build_changelog", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    frag = tmp_path / "changelog.d"
    frag.mkdir()
    (frag / ".gitkeep").write_text("")
    (frag / "README.md").write_text("how-to, ignored")
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n\n### Added\n\n- old\n"
    )
    monkeypatch.setattr(mod, "FRAG_DIR", frag)
    monkeypatch.setattr(mod, "CHANGELOG", cl)
    return mod, frag, cl


def _frag(frag, name, body):
    (frag / name).write_text(body)


def test_render_groups_and_orders(monkeypatch, tmp_path):
    mod, frag, _ = _load(monkeypatch, tmp_path)
    _frag(frag, "10.fixed.md", "fixed ten")
    _frag(frag, "2.added.md", "added two")
    _frag(frag, "10.added.md", "added ten")
    _frag(frag, "+slug.security.md", "sec slug")
    section, consumed = mod.render_section("1.2.0", "2026-02-02")
    assert section.startswith("## [1.2.0] - 2026-02-02")
    # Keep a Changelog order: Added before Fixed before Security
    assert section.index("### Added") < section.index("### Fixed") < section.index("### Security")
    # numeric ids sort numerically within a category (2 before 10), +slug ids come after numbers
    assert section.index("added two") < section.index("added ten")
    assert "- fixed ten" in section and "- sec slug" in section
    assert len(consumed) == 4  # .gitkeep + README ignored


def test_ignores_non_fragments(monkeypatch, tmp_path):
    mod, frag, _ = _load(monkeypatch, tmp_path)
    _frag(frag, "notes.txt", "nope")
    _frag(frag, "random.md", "no valid category")  # not <id>.<category>.md
    _section, consumed = mod.render_section("1.0.0", "2026-01-01")
    assert consumed == []


def test_cut_inserts_between_unreleased_and_prior_release_then_clears(monkeypatch, tmp_path):
    mod, frag, cl = _load(monkeypatch, tmp_path)
    _frag(frag, "5.changed.md", "changed five")
    mod.cut("1.3.0", "2026-03-03")
    text = cl.read_text()
    assert "## [Unreleased]" in text
    assert text.index("## [1.3.0] - 2026-03-03") < text.index("## [0.1.0]")
    assert "- changed five" in text
    assert not (frag / "5.changed.md").exists()  # consumed
    assert (frag / ".gitkeep").exists() and (frag / "README.md").exists()  # kept


def test_cut_with_no_fragments_errors(monkeypatch, tmp_path):
    mod, _frag_dir, _cl = _load(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        mod.cut("1.0.0", "2026-01-01")


def test_draft_changes_nothing(monkeypatch, tmp_path, capsys):
    mod, frag, cl = _load(monkeypatch, tmp_path)
    _frag(frag, "7.added.md", "added seven")
    before = cl.read_text()
    mod.main(["--draft"])
    assert cl.read_text() == before and (frag / "7.added.md").exists()
    assert "added seven" in capsys.readouterr().out
