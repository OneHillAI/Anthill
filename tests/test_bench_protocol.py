"""The reference-machine measurement protocol (#236): cool-down, discarded warm-up, repeats, median, spread.
Model-free: nothing here touches an engine or the clock."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "bench"))

from machinebench import protocol as p

FAST = p.Protocol(cooldown_s=0.0)


def _runs(*values, key="x"):
    """A run function that returns each value in turn."""
    it = iter(values)
    return lambda: {key: next(it)}


def test_defaults_are_the_documented_protocol():
    d = p.Protocol()
    assert (d.cooldown_s, d.repeats, d.warmups, d.max_spread, d.ms_noise_floor) == (
        20.0,
        3,
        1,
        0.10,
        5.0,
    )


def test_one_warmup_is_discarded_and_three_repeats_are_kept():
    m = p.measure(_runs(999, 10, 11, 12), FAST)  # the 999 is the warm-up
    assert m.metrics["x"].samples == (10, 11, 12)
    assert m.metrics["x"].median == 11


def test_cooldown_is_taken_after_the_warmup_and_between_repeats():
    slept = []
    p.measure(_runs(1, 1, 1, 1), p.Protocol(cooldown_s=20.0), sleep=slept.append)
    assert slept == [20.0, 20.0, 20.0]  # after the warm-up, then between the three repeats


def test_median_and_spread():
    m = p.measure(_runs(0, 100, 104, 96), FAST).metrics["x"]
    assert m.median == 100
    assert m.spread == pytest.approx(0.08)
    assert m.status == "ok"


def test_spread_above_ten_percent_is_unstable_not_dropped():
    m = p.measure(_runs(0, 100, 120, 80), FAST).metrics["x"]
    assert m.status == "unstable" and m.median == 100 and m.samples == (100, 120, 80)


def test_spread_exactly_at_the_limit_is_ok():
    m = p.measure(_runs(0, 95, 100, 105), FAST).metrics["x"]
    assert m.spread == pytest.approx(0.10) and m.status == "ok"


def test_a_tiny_time_within_clock_jitter_is_not_unstable_but_a_big_one_is():
    # The first real run flagged a warm first word of 41, 45 and 46 ms (an 11% spread over a 5 ms range).
    jitter = p.measure(_runs(0, 45, 41, 46, key="first_word_ms"), FAST).metrics["first_word_ms"]
    assert jitter.spread > 0.10 and jitter.status == "ok"
    big = p.measure(_runs(0, 450, 410, 460, key="first_word_ms"), FAST).metrics["first_word_ms"]
    assert big.status == "unstable"  # a 50 ms range is more than jitter


def test_a_few_cached_tokens_of_a_template_header_are_not_unstable_but_a_big_count_is():
    # The second real run flagged a cold prompt's 3, 4 and 4 cached tokens (a 25% spread over a 1 token range).
    small = p.measure(_runs(0, 4, 3, 4, key="cached_tokens"), FAST).metrics["cached_tokens"]
    assert small.spread > 0.10 and small.status == "ok"
    large = p.measure(_runs(0, 2000, 1800, 2100, key="cached_tokens"), FAST).metrics[
        "cached_tokens"
    ]
    assert large.status == "unstable"


def test_the_jitter_allowance_applies_to_times_and_token_counts_only():
    speed = p.measure(_runs(0, 4.5, 4.1, 4.6, key="decode_tok_s"), FAST).metrics["decode_tok_s"]
    assert (
        speed.status == "unstable"
    )  # a range of 0.5 tok/s on 4.5 is a real spread, not clock jitter


def test_a_median_of_zero_cannot_have_a_spread_so_it_is_unstable():
    assert p.measure(_runs(0, 0, 0, 0), FAST).metrics["x"].status == "unstable"


def test_a_figure_the_engine_did_not_report_is_missing_never_zero():
    m = p.measure(_runs(0, 5, None, 7), FAST).metrics["x"]
    assert m.status == "missing" and 0 not in m.samples and m.samples == (5, 7)
    assert p.summarise([None, None, None], FAST).median is None


def test_several_numbers_per_run_are_summarised_separately():
    runs = iter([{"a": 1, "b": 9}, {"a": 1, "b": 9}, {"a": 1, "b": 9}, {"a": 1, "b": 9}])
    m = p.measure(lambda: next(runs), FAST)
    assert set(m.metrics) == {"a", "b"}


def test_an_invalid_run_is_noted_and_makes_the_figures_unstable():
    runs = iter(
        [{"x": 1}, {"x": 10}, p.RunInvalid("prompt was 5 tokens, not about 4000"), {"x": 10}]
    )

    def run():
        value = next(runs)
        if isinstance(value, Exception):
            raise value
        return value

    m = p.measure(run, FAST)
    assert m.notes == ("prompt was 5 tokens, not about 4000",)
    assert (
        m.metrics["x"].status == "unstable"
    )  # two good runs of the three do not count as a clean result


def test_a_bad_warmup_is_noted_but_does_not_spoil_the_figures():
    runs = iter([p.RunInvalid("cold run hit the cache"), {"x": 10}, {"x": 10}, {"x": 10}])

    def run():
        value = next(runs)
        if isinstance(value, Exception):
            raise value
        return value

    m = p.measure(run, FAST)
    assert m.notes == ("cold run hit the cache",) and m.metrics["x"].status == "ok"


# -- power -------------------------------------------------------------------------------------------


def _pmset(text):
    return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=text, stderr="")


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1) 100%", "ac"),
        ("Now drawing from 'Battery Power'\n -InternalBattery-0 80%", "battery"),
        ("something else", "unknown"),
        ("", "unknown"),
    ],
)
def test_power_state_reads_pmset(text, state):
    assert p.power_state(_pmset(text)) == state


def test_power_state_is_unknown_when_pmset_is_missing():
    def boom(*a, **k):
        raise FileNotFoundError("pmset")

    assert p.power_state(boom) == "unknown"


def test_battery_is_refused_unless_allowed():
    with pytest.raises(p.PowerError, match="battery"):
        p.require_ac("battery")
    p.require_ac("battery", allow_battery=True)
    p.require_ac("ac")
    p.require_ac("unknown")  # not a laptop, or not macOS: nothing to refuse
