"""The reference-machine scoreboard (#236): scenarios, the result file, the report-only comparison and the rules
the harness keeps (never pulls a model, refuses battery, unique cold prompts). Model-free: a fake engine
stands in for Ollama and nothing touches a clock or a real machine."""

import contextlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "bench"))

from machinebench import machine as machine_mod
from machinebench import results as res
from machinebench import scenarios as sc
from machinebench.engine import EngineError, ModelMissing, OllamaEngine
from machinebench.protocol import PowerError, Protocol
from machinebench.run import run

from anthill.inference.stats import TurnStats

FAST = Protocol(cooldown_s=0.0)
NO_SLEEP = lambda _s: None  # noqa: E731


class FakeEngine:
    """Tokens are a quarter of the characters plus a fixed overhead. A prompt seen before is mostly cached, as a
    real engine's prefix cache would. Every call is recorded; there is no pull method at all."""

    def __init__(self, *, installed=("m:1b",), decode_tokens=100, report_cached=True, depth_skew=1.0,
                 force_cached=0, prefill_tok_s=1000.0, decode_tok_s=50.0):  # fmt: skip
        self.installed_tags = {t: f"sha256:{t}" for t in installed}
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.seen: set[str] = set()
        self.decode_tokens, self.report_cached = decode_tokens, report_cached
        self.depth_skew, self.force_cached = depth_skew, force_cached
        self.prefill_rate, self.decode_rate = prefill_tok_s, decode_tok_s
        self.calibrated = False

    def require(self, tags):
        self.calls.append("require")
        missing = [t for t in tags if t not in self.installed_tags]
        if missing:
            raise ModelMissing(f"Not installed: {', '.join(missing)}.")
        return {t: self.installed_tags[t] for t in tags}

    def version(self):
        return "0.40.7"

    def unload(self, model):
        self.calls.append("unload")

    def running(self):
        return [
            {
                "name": "m:1b",
                "size": 2_163_558_317,
                "size_vram": 2_163_558_317,
                "context_length": 8192,
            }
        ]

    def chat(self, model, messages, *, num_ctx, num_predict, keep_alive="10m"):
        self.calls.append("chat")
        text = "".join(m["content"] for m in messages)
        self.prompts.append(text)
        # Prose is a quarter of the characters; the junk "x x x" a bad calibration would use is twice as dense.
        tokens = len(text) // (2 if text.startswith("x x") else 4) + 10
        if self.calibrated:
            tokens = int(tokens * self.depth_skew)
        elif num_predict == 1 and len(text) > 1000:
            self.calibrated = True  # the second calibration prompt is the longer one
        # What every call shares: the chat template's header, and the model's own default system text when the
        # caller sends none. A real engine caches both from the second call on.
        shared = 3 if messages[0]["role"] == "system" else 27
        cached = max(tokens - 1, 0) if text in self.seen else self.force_cached
        if self.seen:
            cached = max(cached, shared)
        self.seen.add(text)
        processed = tokens - cached
        return TurnStats(
            model=model,
            num_ctx=num_ctx,
            prompt_tokens=tokens,
            cached_tokens=cached if self.report_cached else None,
            prefill_ms=round(processed / self.prefill_rate * 1000) or 1,
            decode_tokens=min(num_predict, self.decode_tokens),
            decode_ms=round(min(num_predict, self.decode_tokens) / self.decode_rate * 1000),
            load_ms=1500,
            first_token_ms=300,
            total_ms=1000,
        )


def _machine(power="ac"):
    return machine_mod.Machine(
        id="apple-m4-16gb", chip="Apple M4", gpu_cores=10, memory_gb=16.0,
        system="Darwin 25.5.0 arm64", bandwidth_gbps=120.0, power=power,
    )  # fmt: skip


# -- calibration and depth ---------------------------------------------------------------------------


def test_a_prompt_is_built_near_the_target_depth_and_the_real_depth_is_reported():
    engine = FakeEngine()
    cpt = sc.calibrate(engine, "m:1b")
    assert cpt == pytest.approx(4.0, rel=0.05)
    out = sc.run_depth(engine, "m:1b", 4000, cpt)
    assert out["prompt_tokens"] == pytest.approx(4000, rel=0.10)
    assert out["decode_tok_s"] == pytest.approx(50.0, rel=0.05)
    assert out["prefill_tok_s"] == pytest.approx(1000.0, rel=0.10)


def test_a_run_more_than_ten_percent_off_its_depth_is_rejected():
    engine = FakeEngine(depth_skew=1.5)
    cpt = sc.calibrate(
        engine, "m:1b"
    )  # calibrated honestly, then the engine counts 50% more tokens
    with pytest.raises(sc.RunInvalid, match="not about 4000"):
        sc.run_depth(engine, "m:1b", 4000, cpt)


def test_a_scenario_whose_every_run_is_off_depth_keeps_no_figures_and_says_why():
    engine = FakeEngine(depth_skew=1.5)
    out = sc.run_all(engine, "m:1b", FAST, lambda: 0, only={"depth_4k"}, sleep=NO_SLEEP)
    m = out["depth_4k"]["measurement"]
    assert m.metrics == {} and len(m.notes) == 4 and all("not about 4000" in n for n in m.notes)


def test_a_short_answer_is_rejected():
    engine = FakeEngine(decode_tokens=20)
    with pytest.raises(sc.RunInvalid, match="stopped after 20 tokens"):
        sc.run_depth(engine, "m:1b", 0, 4.0)


def test_depth_zero_has_no_filler_and_a_unique_tag_in_a_system_message():
    messages = sc.depth_messages(0, 4.0, "abc123")
    assert messages[0]["role"] == "system" and messages[0]["content"].startswith("[abc123]")
    assert "ledger" not in messages[1]["content"]


def test_the_unique_tag_goes_in_a_system_message_because_a_default_system_text_is_cached():
    # The first real run rejected every depth_0 run: with the tag in the user message, the model's own default
    # system text came first, identical on every call, and the engine reused 24 of 51 tokens.
    engine = FakeEngine()
    user_only = [{"role": "user", "content": "[tag1] hi"}]
    engine.chat("m:1b", user_only, num_ctx=8192, num_predict=8)
    cached = engine.chat(
        "m:1b", [{"role": "user", "content": "[tag2] hi"}], num_ctx=8192, num_predict=8
    )
    with pytest.raises(sc.RunInvalid):
        sc.check_cold(cached)  # 27 shared tokens of a 40-token prompt: not a cold run
    assert (
        sc.run_depth(engine, "m:1b", 0, 4.0)["prefill_tok_s"] > 0
    )  # tagged in a system message: valid


def test_the_chat_templates_fixed_header_is_allowed_but_a_real_cache_hit_is_not():
    ok = TurnStats(model="m", prompt_tokens=40, cached_tokens=3)
    sc.check_cold(ok)
    for cached in (9, 2000):
        with pytest.raises(sc.RunInvalid):
            sc.check_cold(TurnStats(model="m", prompt_tokens=2500, cached_tokens=cached + 120))
    sc.check_cold(TurnStats(model="m", prompt_tokens=None, cached_tokens=None))


def test_calibration_uses_prose_not_junk_text():
    # The first real run built 1,709-token prompts for a 4,000 target: it had calibrated on "x x x ..." (about two
    # characters a token) while the filler is prose (about four).
    engine = FakeEngine()
    assert sc.calibrate(engine, "m:1b") == pytest.approx(4.0, rel=0.05)
    assert not any(p.startswith("x x") for p in engine.prompts)


# -- cold runs ---------------------------------------------------------------------------------------


def test_every_cold_prompt_starts_with_a_different_token():
    engine = FakeEngine()
    for _ in range(4):
        sc.run_wiki_cold(engine, "m:1b")
        sc.run_depth(engine, "m:1b", 0, 4.0)
    firsts = [p.split("]", 1)[0] for p in engine.prompts]
    assert all(f.startswith("[") for f in firsts) and len(set(firsts)) == len(firsts)


def test_a_cold_run_that_the_engine_says_hit_the_cache_is_rejected():
    engine = FakeEngine(force_cached=2000)
    with pytest.raises(sc.RunInvalid, match="reused 2000"):
        sc.run_wiki_cold(engine, "m:1b")


def test_the_warm_wiki_prompt_is_mostly_cached_and_prefill_speed_counts_only_the_rest():
    engine = FakeEngine()
    cold = sc.run_wiki_cold(engine, "m:1b")
    warm = sc.run_wiki_warm(engine, "m:1b")
    assert (
        cold["cached_tokens"] <= sc.CACHE_ALLOWANCE_TOKENS
        and warm["cached_tokens"] > 0.9 * warm["prompt_tokens"]
    )
    assert warm["prefill_ms"] < cold["prefill_ms"]


def test_an_engine_that_reports_no_cached_count_gives_a_missing_figure_not_zero():
    # The bundled Ollama 0.30.10 has no prompt_eval_cached_count (see docs/specs/ollama-engine-upgrade-gate.md).
    engine = FakeEngine(report_cached=False)
    out = sc.run_all(engine, "m:1b", FAST, lambda: 0, only={"wiki_warm"}, sleep=NO_SLEEP)
    assert out["wiki_warm"]["measurement"].metrics["cached_tokens"].status == "missing"


def test_prefill_speed_excludes_cached_tokens_and_is_none_without_a_time():
    stats = TurnStats(model="m", prompt_tokens=1000, cached_tokens=900, prefill_ms=100)
    assert sc.prefill_tok_s(stats) == pytest.approx(1000.0)  # 100 processed tokens in 0.1 s
    assert sc.prefill_tok_s(TurnStats(model="m", prompt_tokens=10)) is None


def test_the_wiki_prompt_fixture_is_the_real_prompt_shape_and_is_not_mutated():
    messages = sc.wiki_messages()
    assert messages[0]["role"] == "system" and "REFERENCE MATERIAL" in messages[1]["content"]
    assert sum(len(m["content"]) for m in messages) > 10_000
    tagged = sc.wiki_messages("zzz")
    assert (
        tagged[0]["content"].startswith("[zzz] ")
        and sc.wiki_messages()[0]["content"] == messages[0]["content"]
    )


# -- memory and swap ---------------------------------------------------------------------------------


def test_memory_reports_the_engines_allocation_and_the_swap_difference():
    reads = iter([100, 100])
    out = sc.run_memory(FakeEngine(), "m:1b", lambda: next(reads), turns=3)
    assert (
        out["allocated_gb"] == pytest.approx(2.164, abs=0.001)
        and out["in_gpu_memory_gb"] == out["allocated_gb"]
    )
    assert out["swap_outs"] == 0.0 and out["turns"] == 3.0


def test_swap_outs_are_counted_and_an_unreadable_counter_is_missing_not_zero():
    reads = iter([100, 140])
    assert sc.run_memory(FakeEngine(), "m:1b", lambda: next(reads), turns=1)["swap_outs"] == 40.0
    assert sc.run_memory(FakeEngine(), "m:1b", lambda: None, turns=1)["swap_outs"] is None
    assert sc.single({"swap_outs": None}).metrics["swap_outs"].status == "missing"


def test_a_model_that_is_not_loaded_gives_missing_memory_figures():
    class Idle(FakeEngine):
        def running(self):
            return []

    out = sc.run_memory(Idle(), "m:1b", lambda: 0, turns=1)
    assert out["allocated_gb"] is None and out["in_gpu_memory_gb"] is None


# -- the machine -------------------------------------------------------------------------------------


def test_machine_id_is_a_file_name_safe_slug():
    assert machine_mod.machine_id("Apple M4 Max", 63.9) == "apple-m4-max-64gb"
    assert machine_mod.machine_id("", 8) == "unknown-8gb"


def _vm(text):
    return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=text, stderr="")


def test_swap_outs_read_from_vm_stat_and_proc_vmstat(tmp_path):
    assert machine_mod.swap_outs(_vm("Pageouts: 5.\nSwapouts:   1234.\n")) == 1234
    proc = tmp_path / "vmstat"
    proc.write_text("pgfault 1\npswpout 77\n")

    def no_vm_stat(*a, **k):
        raise FileNotFoundError("vm_stat")

    assert machine_mod.swap_outs(no_vm_stat, proc) == 77
    assert machine_mod.swap_outs(no_vm_stat, tmp_path / "missing") is None


# -- the engine client -------------------------------------------------------------------------------


def test_the_engine_client_reads_only_and_never_pulls(monkeypatch):
    seen = []

    def get(url, timeout=None, **kw):
        seen.append(("GET", url.rsplit("/api", 1)[1]))
        return httpx.Response(
            200,
            json={"models": [{"name": "m:1b", "digest": "sha256:aa"}]},
            request=httpx.Request("GET", url),
        )

    def post(url, **kw):
        seen.append(("POST", url))
        raise AssertionError("require must not call anything that changes the engine")

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    engine = OllamaEngine("http://x")
    assert engine.require(["m:1b"]) == {"m:1b": "sha256:aa"}
    with pytest.raises(ModelMissing, match="never downloads"):
        engine.require(["gone:1b"])
    assert {m for m, _ in seen} == {"GET"}


def test_a_chat_call_is_temperature_zero_with_an_explicit_window_and_times_the_first_word(
    monkeypatch,
):
    sent = {}

    class Resp:
        is_success, status_code = True, 200

        def iter_lines(self):
            yield json.dumps({"message": {"content": "Hi"}, "done": False})
            yield json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 12, "eval_count": 3,
                              "eval_duration": 60_000_000, "prompt_eval_duration": 24_000_000, "load_duration": 1_000_000})  # fmt: skip

    @contextlib.contextmanager
    def stream(method, url, json=None, timeout=None, **kw):
        sent.update(json)
        yield Resp()

    monkeypatch.setattr(httpx, "stream", stream)
    stats = OllamaEngine("http://x").chat(
        "m:1b", [{"role": "user", "content": "hi"}], num_ctx=8192, num_predict=16
    )
    assert (
        sent["options"]["temperature"] == 0
        and sent["options"]["num_ctx"] == 8192
        and sent["stream"] is True
    )
    assert (
        stats.prompt_tokens == 12
        and stats.first_token_ms is not None
        and stats.decode_tokens_per_s == pytest.approx(50.0)
    )


def test_a_stream_that_never_finishes_is_an_engine_error(monkeypatch):
    class Resp:
        is_success, status_code = True, 200

        def iter_lines(self):
            yield json.dumps({"message": {"content": "Hi"}, "done": False})

    @contextlib.contextmanager
    def stream(*a, **k):
        yield Resp()

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(EngineError, match="ended before it finished"):
        OllamaEngine("http://x").chat("m", [], num_ctx=1, num_predict=1)


# -- a full run and the result file ------------------------------------------------------------------


def _run(tmp_path, engine=None, **kw):
    engine = engine or FakeEngine()
    return run(
        ["m:1b"], "1.1.1", tmp_path, FAST, engine=engine, power=kw.pop("power", "ac"),
        describe_machine=lambda power: _machine(power), swap_reader=lambda: 0,
        sleep=NO_SLEEP, log=lambda _s: None, **kw,
    ), engine  # fmt: skip


def test_a_run_writes_one_file_per_machine_per_release_with_the_provenance(tmp_path):
    path, _ = _run(tmp_path)
    assert path == tmp_path / "1.1.1" / "apple-m4-16gb.json"
    data = res.load_result(path)
    assert data["anthill_release"] == "1.1.1" and data["engine"] == {
        "name": "ollama",
        "version": "0.40.7",
    }
    assert data["models"]["m:1b"]["digest"] == "sha256:m:1b"
    assert data["machine"]["power"] == "ac" and data["machine"]["chip"] == "Apple M4"
    assert data["protocol"] == {
        "cooldown_s": 0.0, "repeats": 3, "warmups": 1, "max_spread": 0.1, "ms_noise_floor": 5.0, "token_noise_floor": 8.0,
    }  # fmt: skip
    names = set(data["models"]["m:1b"]["scenarios"])
    assert names == {"depth_0", "depth_4k", "wiki_cold", "wiki_warm", "cold_load", "memory_swap"}
    metric = data["models"]["m:1b"]["scenarios"]["depth_4k"]["metrics"]["decode_tok_s"]
    assert set(metric) == {"median", "spread", "samples", "status"} and len(metric["samples"]) == 3


def test_a_missing_model_is_reported_before_anything_runs_and_nothing_is_pulled(tmp_path):
    engine = FakeEngine(installed=())
    with pytest.raises(ModelMissing):
        _run(tmp_path, engine)
    assert engine.calls == ["require"]  # no chat, no unload; and the fake has no pull to call
    assert not list(tmp_path.rglob("*.json"))


def test_a_machine_on_battery_is_refused_before_the_first_call_and_the_result_says_so_when_allowed(
    tmp_path,
):
    engine = FakeEngine()
    with pytest.raises(PowerError):
        _run(tmp_path, engine, power="battery")
    assert engine.calls == ["require"]
    path, _ = _run(tmp_path, power="battery", allow_battery=True)
    assert res.load_result(path)["machine"]["power"] == "battery"


def test_only_runs_the_named_scenarios(tmp_path):
    path, _ = _run(tmp_path, only={"cold_load"})
    assert set(res.load_result(path)["models"]["m:1b"]["scenarios"]) == {"cold_load"}


def test_an_unknown_schema_is_refused(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"schema": 99}))
    with pytest.raises(ValueError, match="schema"):
        res.load_result(bad)


# -- the comparison is report-only --------------------------------------------------------------------


def _result(decode=50.0, first_word=300.0, swap=0.0, status="ok"):
    def m(v):
        return {"median": v, "spread": 0.01, "samples": [v], "status": status}

    return {
        "schema": 1, "anthill_release": "1.1.1", "recorded_at": "x",
        "machine": {"id": "apple-m4-16gb"}, "engine": {"name": "ollama", "version": "0.40.7"},
        "models": {"m:1b": {"digest": "d", "scenarios": {
            "depth_4k": {"params": {}, "notes": [], "metrics": {"decode_tok_s": m(decode), "first_word_ms": m(first_word)}},
            "memory_swap": {"params": {}, "notes": [], "metrics": {"swap_outs": m(swap), "allocated_gb": m(2.0)}},
        }}},
    }  # fmt: skip


def test_a_drop_above_fifteen_percent_is_flagged():
    flagged = res.compare(_result(), _result(decode=42.0))  # 42 against 50 is 16% worse
    assert [(c.metric, round(c.change, 2)) for c in flagged] == [("decode_tok_s", 0.16)]


def test_decode_speed_down_and_first_word_up_are_both_regressions():
    changes = res.compare(_result(), _result(decode=40.0, first_word=360.0))
    assert {c.metric for c in changes} == {"decode_tok_s", "first_word_ms"}
    assert all(c.change > 0.15 for c in changes)


def test_the_limit_is_fifteen_percent_exactly():
    assert res.compare(_result(), _result(decode=42.5)) == []  # 15% worse: not above the limit
    assert [c.metric for c in res.compare(_result(), _result(decode=42.4))] == ["decode_tok_s"]


def test_a_better_result_is_never_flagged():
    assert res.compare(_result(), _result(decode=80.0, first_word=100.0)) == []


def test_new_swapping_is_flagged_and_a_figure_that_is_not_compared_is_ignored():
    changes = res.compare(_result(swap=0.0), _result(swap=5.0))
    assert [c.metric for c in changes] == ["swap_outs"]  # allocated_gb is not a speed or a time


def test_an_unstable_figure_is_not_compared():
    assert res.compare(_result(), _result(decode=10.0, status="unstable")) == []


def test_the_compare_tool_prints_and_always_exits_zero(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_result()))
    b.write_text(json.dumps(_result(decode=10.0)))
    import compare_scoreboard

    assert compare_scoreboard.main([str(a), str(b)]) == 0
    assert "decode_tok_s" in capsys.readouterr().out
    assert compare_scoreboard.main([str(a), str(a)]) == 0
