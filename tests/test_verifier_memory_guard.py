"""Issue #413 (direction 3): the runtime verifier loads a SECOND (different-family) model to cross-check
an answer, concurrently with the still-resident producer model. On a memory-constrained Mac that froze
the app. The cross-check is now skipped when free RAM is below a floor (deterministic checks still run;
the verdict fails safe to needs_review, never blocks)."""

import anthill.hosting.sizing as sizing
from anthill.verify.verify import default_crosscheck


def test_parse_vm_stat_free_gb():
    sample = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                          65536.\n"
        "Pages active:                        100000.\n"
        "Pages inactive:                      65536.\n"
        "Pages wired down:                    50000.\n"
    )
    # (free 65536 + inactive 65536) pages * 16384 bytes = 2.0 GiB
    gb = sizing._parse_vm_stat_free_gb(sample)
    assert gb is not None and abs(gb - 2.0) < 0.01


def test_parse_vm_stat_free_gb_bad_input():
    assert sizing._parse_vm_stat_free_gb("not vm_stat output") is None


def test_crosscheck_skips_under_memory_pressure(monkeypatch):
    monkeypatch.setattr(sizing, "free_mem_gb", lambda: 2.0)  # only 2 GB free
    run = default_crosscheck("http://127.0.0.1:1", "qwen3:8b", {"qwen3:8b", "llama3.1:8b"})
    cc = run("some output", kind="task_result", goal="do X")
    assert cc.ok is None
    assert "memory pressure" in cc.reason
    assert cc.model == "llama3.1:8b"  # a verifier was chosen, but deliberately not loaded


def test_crosscheck_not_skipped_when_free_memory_unknown(monkeypatch):
    # If free memory can't be read, don't skip on that basis - fall through to the normal path (which
    # here fails to reach Ollama and returns a DIFFERENT ok=None reason, not the memory-pressure one).
    monkeypatch.setattr(sizing, "free_mem_gb", lambda: None)
    run = default_crosscheck("http://127.0.0.1:1", "qwen3:8b", {"qwen3:8b", "llama3.1:8b"})
    cc = run("out", kind="task_result", goal="g")
    assert cc.ok is None
    assert "memory pressure" not in cc.reason


def test_crosscheck_runs_when_memory_is_ample(monkeypatch):
    # Plenty of free RAM -> the guard does not skip; it proceeds (and here, with no reachable Ollama,
    # returns the "unavailable" ok=None, proving it got past the guard).
    monkeypatch.setattr(sizing, "free_mem_gb", lambda: 64.0)
    run = default_crosscheck("http://127.0.0.1:1", "qwen3:8b", {"qwen3:8b", "llama3.1:8b"})
    cc = run("out", kind="task_result", goal="g")
    assert cc.ok is None and "memory pressure" not in cc.reason
