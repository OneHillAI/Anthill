"""Tests ensuring comments and docstrings stay accurate and aligned with implementation."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_init_docstring_lists_all_generated_targets():
    import anthill.cli as cli_mod

    doc = cli_mod.init.__doc__ or ""
    assert "skills/" in doc
    assert "principles.md" in doc
    assert "SCHEMA.md" in doc
    assert "index.md" in doc
    assert "log.md" in doc


def test_tools_email_comments_accurate():
    tools_py = (REPO_ROOT / "anthill" / "agent" / "tools.py").read_text(encoding="utf-8")
    assert "email stubs" not in tools_py
    assert "configure ANTHILL_EMAIL_* to enable sending" not in tools_py
    assert "Review and send manually from your email client" in tools_py


def test_sources_md_points_to_agents_md():
    sources_md = (REPO_ROOT / "anthill" / "skills_gallery" / "SOURCES.md").read_text(
        encoding="utf-8"
    )
    assert "docs/CODE_AND_DOCS_STANDARDS.md" not in sources_md
    assert "`AGENTS.md`" in sources_md


def test_sidebar_comments_accurate():
    sidebar_html = (REPO_ROOT / "anthill" / "web" / "templates" / "_sidebar.html").read_text(
        encoding="utf-8"
    )
    assert "pin/rename/delete controls" in sidebar_html
    assert "Integrations" in sidebar_html


def test_personalize_tabs_comment_includes_integrations():
    personalize_html = (REPO_ROOT / "anthill" / "web" / "templates" / "personalize.html").read_text(
        encoding="utf-8"
    )
    assert "Integrations (MCP connectors, admin-only)" in personalize_html


def test_knowledge_tabs_comment_reflects_five_surfaces():
    know_tabs = (REPO_ROOT / "anthill" / "web" / "templates" / "_knowledge_tabs.html").read_text(
        encoding="utf-8"
    )
    assert "five surfaces" in know_tabs
    assert "Suggestions" in know_tabs


def test_chat_options_comment_omits_retired_agent_override():
    chat_html = (REPO_ROOT / "anthill" / "web" / "templates" / "chat.html").read_text(
        encoding="utf-8"
    )
    assert "the web/agent/scope overrides" not in chat_html
    assert "web search and scope options" in chat_html
