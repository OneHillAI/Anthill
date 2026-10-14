"""The summary the Windows full suite writes to its job page (scripts/windows_suite_summary.py).

The job is advisory: failures must be readable at a glance, and a missing result file must be reported without raising.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "windows_suite_summary", ROOT / "scripts" / "windows_suite_summary.py"
)
summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summary)


def _junit(tmp_path, cases, tests=None, failures=None, errors=0, skipped=0):
    body = "".join(cases)
    n = tests if tests is not None else body.count("<testcase")
    f = failures if failures is not None else body.count("<failure")
    path = tmp_path / "suite.xml"
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="{n}" failures="{f}" '
        f'errors="{errors}" skipped="{skipped}">{body}</testsuite></testsuites>'
    )
    return path


def _ok(module, name):
    return f'<testcase classname="tests.{module}" name="{name}" time="0.01"/>'


def _bad(module, name, message="boom"):
    return (
        f'<testcase classname="tests.{module}" name="{name}" time="0.01">'
        f'<failure message="{message}">Traceback\n{message}</failure></testcase>'
    )


def test_a_clean_run_says_nothing_failed_and_how_to_make_it_a_gate(tmp_path):
    out = summary.summarise(_junit(tmp_path, [_ok("test_a", "t1"), _ok("test_a", "t2")]))
    assert "2 passed, 0 failed, 0 skipped, of 2" in out
    assert "Nothing failed on Windows" in out and "continue-on-error" in out


def test_failures_are_grouped_by_test_file_and_the_worst_file_comes_first(tmp_path):
    cases = [
        _ok("test_good", "t"),
        _bad("test_paths", "test_one", "PermissionError: [WinError 32] in use"),
        _bad("test_paths", "test_two", "assert 'a\\\\b' == 'a/b'"),
        _bad("test_signals", "test_term", "AttributeError: SIGKILL"),
    ]
    out = summary.summarise(_junit(tmp_path, cases))
    assert "1 passed, 3 failed, 0 skipped, of 4" in out
    assert "2 test file(s) with failures" in out
    assert out.index("tests/test_paths.py") < out.index(
        "tests/test_signals.py"
    )  # 2 failures before 1
    assert "tests/test_paths.py: 2 failed" in out
    assert "PermissionError: [WinError 32] in use" in out and "AttributeError: SIGKILL" in out
    assert "test_good" not in out


def test_long_lists_are_cut_so_a_broken_environment_stays_readable(tmp_path):
    many = [_bad("test_x", f"test_{i}") for i in range(summary._MAX_PER_FILE + 4)]
    many += [_bad(f"test_file_{i}", "t") for i in range(summary._MAX_FILES + 3)]
    out = summary.summarise(_junit(tmp_path, many))
    assert "... and 4 more" in out
    assert "... and" in out and "more files" in out


def test_errors_count_as_failures_and_skips_are_reported(tmp_path):
    err = '<testcase classname="tests.test_e" name="t"><error message="ImportError: no module">tb</error></testcase>'
    out = summary.summarise(
        _junit(tmp_path, [err, _ok("test_s", "t")], tests=2, failures=0, errors=1, skipped=0)
    )
    assert "1 passed, 1 failed" in out and "ImportError: no module" in out
    skipped = summary.summarise(_junit(tmp_path, [_ok("test_s", "t")], tests=3, skipped=2))
    assert "1 passed, 0 failed, 2 skipped, of 3" in skipped


def test_a_missing_or_broken_file_is_reported_not_raised(tmp_path, capsys):
    out = summary.summarise(tmp_path / "nope.xml")
    assert "No result file to read" in out and "FileNotFoundError" in out
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuites><oops")
    assert "ParseError" in summary.summarise(bad)
    assert summary.main(["x", str(tmp_path / "nope.xml")]) == 0  # the summary never fails the job
    assert "No result file" in capsys.readouterr().out
    assert summary.main(["x"]) == 2


def test_the_test_file_is_found_for_functions_classes_and_sub_folders():
    assert summary._test_file("tests.test_a") == "tests/test_a.py"
    assert summary._test_file("tests.test_a.TestX") == "tests/test_a.py"
    assert summary._test_file("tests.browser.test_b") == "tests/browser/test_b.py"
    assert summary._test_file("tests.conftest") == "tests/conftest.py"


def test_hostile_names_and_messages_stay_inside_a_block_they_cannot_close(tmp_path):
    # What a message could do to the job page if it were read as markdown: HTML, a link, bold, and a backtick run
    # long enough to close an ordinary three-backtick fence.
    hostile = "&lt;img src=x&gt; [x](http://y) **b** `tick` ```` end"
    name = "test_p[a`b&lt;i&gt;]"
    out = summary.summarise(_junit(tmp_path, [_bad("test_h", name, hostile)]))
    lines = out.splitlines()
    fence = "`" * 5  # one longer than the four-backtick run in the message
    assert lines.count(fence + "text") == 1 and lines.count(fence) == 1
    start, end = lines.index(fence + "text"), lines.index(fence)
    inside = "\n".join(lines[start + 1 : end])
    assert "<img src=x> [x](http://y) **b** `tick` ```` end" in inside
    assert "test_p[a`b<i>]" in inside and "tests/test_h.py: 1 failed" in inside
    outside = "\n".join(lines[:start] + lines[end + 1 :])
    assert "<" not in outside and "](" not in outside and "**" not in outside and "`" not in outside


def test_the_fence_is_three_backticks_when_nothing_needs_more(tmp_path):
    out = summary.summarise(_junit(tmp_path, [_bad("test_q", "t", "plain")]))
    assert "\n```text\n" in out and "\n```\n" in out and "````" not in out


def test_malformed_counts_read_as_zero_and_never_raise(tmp_path, capsys):
    assert summary._int(None) == 0 and summary._int("") == 0 and summary._int("7") == 7
    assert summary._int("3.0") == 3 and summary._int("lots") == 0 and summary._int("1e999") == 0
    assert summary._int("nan") == 0 and summary._int("-4") == 0
    path = tmp_path / "odd.xml"
    path.write_text(
        '<testsuites><testsuite tests="many" failures="" errors="x" skipped="1e999">'
        + _bad("test_m", "t", "boom")
        + "</testsuite></testsuites>"
    )
    out = summary.summarise(path)
    assert "0 passed, 0 failed, 0 skipped, of 0." in out and "tests/test_m.py: 1 failed" in out
    assert summary.main(["x", str(path)]) == 0
    assert "boom" in capsys.readouterr().out


def test_a_huge_test_name_is_cut_and_the_summary_stays_small(tmp_path):
    junit = tmp_path / "r.xml"
    cases = "".join(
        f'<testcase classname="tests.test_a" name="test_x[{"p" * 10000}{i}]"><failure message="m"/></testcase>'
        for i in range(5)
    )
    junit.write_text(f'<testsuite tests="5" failures="5">{cases}</testsuite>')
    text = summary.summarise(junit)
    assert len(text) < 5000 and "p" * 300 not in text


def test_an_unexpected_error_while_reading_gives_the_plain_notice(tmp_path, monkeypatch):
    junit = tmp_path / "r.xml"
    junit.write_text("<testsuite tests='1'/>")
    monkeypatch.setattr(
        summary.ET, "parse", lambda path: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert "No result file to read" in summary.summarise(junit)
