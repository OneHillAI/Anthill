"""Agent skills are conformant agentskills.io skills, extended with Anthill governance under x-anthill-*
(the OKF -> OKGF move for skills). These pin the base-standard conformance + the backward-compat reader."""

from anthill.agent import skills


def test_conform_name_normalises_to_the_spec():
    assert skills.conform_name("PDF Processing") == "pdf-processing"
    assert skills.conform_name("Effective  wiki  page!!") == "effective-wiki-page"  # collapse --
    assert skills.conform_name("--weird--") == "weird"  # strip leading/trailing
    assert skills.validate_skill_name(skills.conform_name("")) == ""  # empty -> some valid name
    assert len(skills.conform_name("x" * 100)) <= 64


def test_validate_skill_name_per_spec():
    assert skills.validate_skill_name("pdf-processing") == ""
    assert skills.validate_skill_name("data-analysis-2") == ""
    for bad in ("PDF-Processing", "-pdf", "pdf-", "pdf--processing", "", "x" * 65):
        assert skills.validate_skill_name(bad) != "", bad


def test_written_skill_is_conformant():
    md = skills.skill_md(
        "PDF Processing", "Extract text from PDFs.", "when handling PDFs", "1. do it"
    )
    # base fields are conformant (lowercase-hyphenated name, non-empty description)
    assert "name: pdf-processing" in md and skills.validate_skill_name("pdf-processing") == ""
    # the current agentskills.io spec treats "when to use" as part of `description`, not a separate
    # field - so when both are given, `when_to_use` is folded into the base description.
    assert "description: Extract text from PDFs. Use when: when handling PDFs" in md
    # governance/routing live under the x-anthill-* extension namespace
    assert "x-anthill-title: PDF Processing" in md
    # x-anthill-when-to-use is STILL also emitted (Anthill's own keyword matcher + UI read it
    # directly) - the fold-in is additive, not a replacement.
    assert "x-anthill-when-to-use: when handling PDFs" in md and "x-anthill-tier: builtin" in md
    # reparse preserves the display title + routing fields
    sk = skills.parse_skill_md(md, slug="pdf-processing")
    assert sk.name == "PDF Processing" and sk.when_to_use == "when handling PDFs"
    assert sk.instructions == "1. do it"
    # the base `description` a non-Anthill agentskills.io tool would read now carries the full
    # "what + when" per spec, not just the "what" half.
    assert sk.description == "Extract text from PDFs. Use when: when handling PDFs"


def test_write_skill_folder_matches_the_conformant_name(tmp_path):
    sk = skills.write_skill("PDF Processing", "d", "w", "i", directory=str(tmp_path))
    assert sk.slug == "pdf-processing"  # the agentskills.io name == the folder
    p = tmp_path / "pdf-processing" / "SKILL.md"
    assert p.exists() and "name: pdf-processing" in p.read_text()


def test_empty_description_falls_back_to_stay_conformant():
    md = skills.skill_md("x", "", "use it sometimes", "body")
    sk = skills.parse_skill_md(md, slug="x")
    assert sk.description  # never empty (agentskills.io requires a non-empty description)


def test_reads_legacy_pre_standard_frontmatter():
    legacy = (
        "---\nname: Effective Wiki Page\ndescription: d\n"
        "when_to_use: when writing\nscopes: wiki, docs\ntier: personal\n---\nbody"
    )
    sk = skills.parse_skill_md(legacy, slug="effective-wiki-page")
    assert sk.name == "Effective Wiki Page"  # a title-cased legacy `name` becomes the display title
    assert (
        sk.when_to_use == "when writing" and sk.scopes == ["wiki", "docs"] and sk.tier == "personal"
    )


def test_optional_standard_fields_round_trip():
    md = skills.skill_md(
        "x",
        "d",
        "w",
        "i",
        license="Apache-2.0",
        allowed_tools="Read Bash(git:*)",
        compatibility="Requires git",
    )
    sk = skills.parse_skill_md(md, slug="x")
    assert sk.license == "Apache-2.0" and sk.allowed_tools == "Read Bash(git:*)"
    assert sk.compatibility == "Requires git"
    assert "license: Apache-2.0" in md and "allowed-tools: Read Bash(git:*)" in md


def test_nested_metadata_block_does_not_break_the_parser():
    md = "---\nname: pdf\ndescription: d\nmetadata:\n  author: acme\n  version: '1.0'\n---\nbody"
    sk = skills.parse_skill_md(md, slug="pdf")
    assert sk.description == "d" and sk.instructions == "body"  # nested keys skipped, no crash
