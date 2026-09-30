"""Phase 2: the SUMMED on-prem council footprint gate (product-council-architecture.md R5).

Every on-prem council member serves via Ollama on the SAME machine, sharing its RAM/VRAM - unlike a
VPC member, which gets its own dedicated cloud instance (unaffected by this; see
sizing.servable_on_gpu / test_hosting_provision.py). sizing.onprem_council_fits() is the summed
check; this file is its unit coverage. Drafted by the ASDD dev-council, then reviewed by hand: the
council's own proposed tests only ever exercised kind="gpu" with a real memory value, never kind="apple"
with one - which is exactly the branch that had a double-counted _LOCAL_RUNNER_GB bug (fixed before this
shipped). test_apple_reserves_runner_overhead_exactly_once below closes that specific blind spot.
"""

from anthill.hosting import sizing


def test_two_onprem_members_fit_alone_but_summed_is_refused(monkeypatch):
    # 32B each ~= 17.6GB weights + KV; two of them together exceed a 40GB gpu-class box's budget even
    # though either one alone fits comfortably.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (40.0, "gpu"))
    assert sizing.onprem_council_fits([32.0])  # one fits
    assert not sizing.onprem_council_fits([32.0, 32.0])  # summed does not


def test_single_onprem_member_still_accepted(monkeypatch):
    # Regression: the single-member case must not become MORE restrictive than it was pre-Phase-2.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (40.0, "gpu"))
    assert sizing.onprem_council_fits([7.0])
    assert sizing.onprem_council_fits([32.0])


def test_empty_member_list_never_blocks():
    assert sizing.onprem_council_fits([])


def test_unprobed_hardware_fails_open_not_closed(monkeypatch):
    # A bad probe (0.0 memory) must never spuriously block a save.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (0.0, "apple"))
    assert sizing.onprem_council_fits([32.0, 32.0])


def test_apple_reserves_runner_overhead_exactly_once(monkeypatch):
    # Found by hand-review of the council's draft: usable_gb's "apple" branch ALREADY subtracts
    # _LOCAL_RUNNER_GB once internally; the draft ALSO subtracted it again in onprem_council_fits,
    # double-counting it on Apple Silicon specifically (the council's own tests never used kind="apple"
    # with a real memory value, so this shipped undetected in the draft). Ground the exact arithmetic: a
    # 32GB Mac's real budget is 0.66*32 - 2.5 = 18.62GB, reserved ONCE - a double-count would wrongly
    # leave only 16.12GB. These two members sum to 16.995GB: it fits the correct 18.62GB budget but
    # would NOT fit the buggy double-counted 16.12GB one, so a regression here fails loudly.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (32.0, "apple"))
    assert sizing.onprem_council_fits([30.0, 0.9], context_k=0.0)


def test_gpu_kind_reserves_runner_overhead_exactly_once(monkeypatch):
    # The "gpu" branch of usable_gb does NOT reserve _LOCAL_RUNNER_GB internally (unlike "apple"), so
    # onprem_council_fits must add it - exactly once, matching resident_gb's own local-serving
    # convention (weights + KV + runner working set).
    monkeypatch.setattr(sizing, "local_hardware", lambda: (40.0, "gpu"))
    # budget = (40 - 5.0 overhead) - 2.5 runner = 32.5GB; 59B * 0.55 = 32.45GB fits, 60B does not.
    assert sizing.onprem_council_fits([59.0], context_k=0.0)
    assert not sizing.onprem_council_fits([60.0], context_k=0.0)
