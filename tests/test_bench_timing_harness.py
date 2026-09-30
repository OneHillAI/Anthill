"""tools/bench/{fetch_prompts,run_timing,compare_runs}.py - the real-usage sampling + timing harness
used to benchmark Anthill's speed against real ChatGPT/LLM usage patterns (WildChat + Arena-Hard-Auto).
Loaded by path (not a package import) since tools/bench/ is standalone dev tooling, matching cli/*.py's
existing convention of not being part of the installed anthill package.
"""

import importlib.util
import json
import sys
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent.parent / "tools" / "bench"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _BENCH_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses' introspection needs the module registered before exec
    spec.loader.exec_module(mod)
    return mod


fetch_prompts = _load("fetch_prompts")
run_timing = _load("run_timing")
compare_runs = _load("compare_runs")


# ── fetch_prompts.py: row filtering ─────────────────────────────────────────────────────────────


def test_first_user_turn_extracts_the_opening_message():
    row = {
        "row": {
            "conversation": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }
    }
    turn = fetch_prompts._first_user_turn(row, conv_key="conversation")
    assert turn == {"role": "user", "content": "hi"}


def test_first_user_turn_none_when_conversation_empty():
    assert (
        fetch_prompts._first_user_turn({"row": {"conversation": []}}, conv_key="conversation")
        is None
    )


def test_first_user_turn_none_when_first_turn_is_not_user():
    row = {"row": {"conversation": [{"role": "assistant", "content": "hi, how can I help?"}]}}
    assert fetch_prompts._first_user_turn(row, conv_key="conversation") is None


# ── run_timing.py: prompt loading (both schemas) ────────────────────────────────────────────────


def test_load_prompts_reads_own_schema(tmp_path):
    p = tmp_path / "own.jsonl"
    p.write_text(
        json.dumps({"id": "wildchat-1", "source": "wildchat", "prompt": "hi there"}) + "\n"
    )
    prompts = run_timing.load_prompts([str(p)])
    assert prompts == [{"id": "wildchat-1", "source": "wildchat", "prompt": "hi there"}]


def test_load_prompts_reads_arena_hard_schema(tmp_path):
    p = tmp_path / "arena.jsonl"
    p.write_text(
        json.dumps({"uid": "abc123", "category": "hard_prompt", "prompt": "write a zig program"})
        + "\n"
    )
    prompts = run_timing.load_prompts([str(p)])
    assert prompts == [{"id": "abc123", "source": "hard_prompt", "prompt": "write a zig program"}]


def test_load_prompts_combines_files_and_respects_limit(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text(
        "\n".join(json.dumps({"id": f"a{i}", "source": "a", "prompt": "x"}) for i in range(3))
    )
    b.write_text(
        "\n".join(json.dumps({"id": f"b{i}", "source": "b", "prompt": "y"}) for i in range(3))
    )
    prompts = run_timing.load_prompts([str(a), str(b)], limit=4)
    assert len(prompts) == 4
    assert [p["id"] for p in prompts] == ["a0", "a1", "a2", "b0"]


# ── run_timing.py: timing a turn (fake client, no network) ─────────────────────────────────────


class _FakeClient:
    """Mirrors OrgClient's interface with a scripted, delayed token stream so TTFT != total is
    actually observable in the test."""

    def __init__(self, events, *, new_conv_id=1, new_conv_error=None):
        self._events = events
        self._new_conv_id = new_conv_id
        self._new_conv_error = new_conv_error
        self.stream_calls = []

    def new_conversation(self, *, plane="solo"):
        if self._new_conv_error:
            raise self._new_conv_error
        return self._new_conv_id

    def stream(self, conv_id, message, *, web=False, escalate_org=False):
        self.stream_calls.append(
            {"conv_id": conv_id, "message": message, "web": web, "escalate_org": escalate_org}
        )
        yield from self._events


def test_time_one_turn_records_ttft_and_total(monkeypatch):
    ticks = iter([100.0, 100.2, 100.5])  # t_start, t_first (on the 1st token event), t_done
    monkeypatch.setattr(run_timing.time, "monotonic", lambda: next(ticks))
    client = _FakeClient([("token", "Hel"), ("token", "lo")])

    r = run_timing.time_one_turn(
        client,
        {"id": "p1", "source": "wildchat", "prompt": "hi"},
        label="local",
        plane="solo",
        escalate=False,
        web=False,
    )
    assert r.error == ""
    assert r.response_chars == 5  # "Hel" + "lo"
    assert r.ttft_s == 0.2
    assert r.total_s == 0.5


def test_time_one_turn_records_error_from_stream_event():
    client = _FakeClient([("token", "partial"), ("error", "backend unreachable")])
    r = run_timing.time_one_turn(
        client,
        {"id": "p1", "source": "s", "prompt": "hi"},
        label="l",
        plane="solo",
        escalate=False,
        web=False,
    )
    assert r.error == "backend unreachable"
    assert r.ttft_s is not None  # the partial token before the error was still timed


def test_time_one_turn_records_new_conversation_failure():
    from anthill.web_client import OrgClientError

    client = _FakeClient([], new_conv_error=OrgClientError("not logged in"))
    r = run_timing.time_one_turn(
        client,
        {"id": "p1", "source": "s", "prompt": "hi"},
        label="l",
        plane="solo",
        escalate=False,
        web=False,
    )
    assert "not logged in" in r.error
    assert r.ttft_s is None and r.total_s is None


def test_time_one_turn_passes_escalate_and_plane_through():
    client = _FakeClient([("token", "ok")])
    run_timing.time_one_turn(
        client,
        {"id": "p1", "source": "s", "prompt": "use the cloud"},
        label="l",
        plane="org",
        escalate=True,
        web=True,
    )
    assert client.stream_calls == [
        {"conv_id": 1, "message": "use the cloud", "web": True, "escalate_org": True}
    ]


# ── run_timing.py / compare_runs.py: stats ──────────────────────────────────────────────────────


def test_stats_computes_mean_median_p95():
    s = run_timing._stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["mean"] == 3.0
    assert s["median"] == 3.0
    assert s["min"] == 1.0 and s["max"] == 5.0


def test_stats_empty_list_is_all_none():
    assert run_timing._stats([]) == {
        "mean": None,
        "median": None,
        "p95": None,
        "min": None,
        "max": None,
    }


def test_summarize_groups_by_field():
    results = [
        run_timing.PromptResult(
            id="1", source="wildchat", prompt_chars=5, label="l", ttft_s=0.1, total_s=0.5
        ),
        run_timing.PromptResult(
            id="2", source="arena", prompt_chars=5, label="l", ttft_s=0.2, total_s=0.6
        ),
        run_timing.PromptResult(id="3", source="wildchat", prompt_chars=5, label="l", error="boom"),
    ]
    summary = run_timing._summarize(results, group_by="source")
    assert summary["wildchat"]["n"] == 2
    assert summary["wildchat"]["errors"] == 1
    assert summary["arena"]["n"] == 1


def test_compare_runs_reads_result_files_and_summarizes(tmp_path):
    p = tmp_path / "local.jsonl"
    p.write_text(
        "\n".join(
            json.dumps(
                {"id": str(i), "label": "local", "ttft_s": 0.1 * i, "total_s": 0.5 * i, "error": ""}
            )
            for i in range(1, 4)
        )
    )
    data = compare_runs._load(str(p))
    assert len(data) == 3
    assert data[0]["label"] == "local"
