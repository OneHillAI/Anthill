"""The suite can never run the real ``ollama`` (see ``ollama_spawn_guard`` in conftest.py).

On a developer's Mac with Ollama installed, background pull/serve threads that tests start can outlive the test
and, once its patches are undone, really download a model (a ~2.4 GB vision model did, 2026-10-03). The guard
refuses to spawn the executable for the whole session and fails the run if anything tried. Pinned here, with no
real process started:

- every way of starting ollama is refused and recorded (pull, serve, a path with spaces, a command string);
- other programs still run;
- the app's own background pull thread, handed an "installed and serving" Ollama, is stopped at the spawn.
"""

import subprocess
import sys
import time

import pytest


def _forget(guard, before):
    """Drop the attempts this test caused on purpose, so the session-end check only sees real leaks."""
    del guard[before:]


@pytest.mark.parametrize(
    "launch",
    [
        lambda: subprocess.run(["/usr/local/bin/ollama", "pull", "granite3.2-vision:2b"]),
        lambda: subprocess.run(["/Users/a b/Library/Mobile Documents/ollama", "serve"]),
        lambda: subprocess.Popen(["ollama", "serve"]),
        lambda: subprocess.check_output(["/opt/homebrew/bin/ollama", "list"]),
        lambda: subprocess.run(
            "ollama pull qwen3:8b"
        ),  # one command string, as a shell would be given
        lambda: subprocess.run(args=["/x/ollama.exe", "pull", "m"]),
    ],
)
def test_every_way_of_starting_ollama_is_refused_and_recorded(ollama_spawn_guard, launch):
    before = len(ollama_spawn_guard)
    with pytest.raises(OSError, match="refusing to run the real ollama"):
        launch()
    assert len(ollama_spawn_guard) == before + 1
    assert "test_every_way_of_starting_ollama" in ollama_spawn_guard[-1]
    _forget(ollama_spawn_guard, before)


def test_other_programs_still_run(ollama_spawn_guard):
    before = len(ollama_spawn_guard)
    done = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert done.stdout.strip() == "hi"
    assert (
        subprocess.run(["echo", "ollama is only a word here"]).returncode == 0
    )  # not the program name
    assert len(ollama_spawn_guard) == before


def test_the_apps_background_pull_thread_is_stopped_at_the_spawn(ollama_spawn_guard, monkeypatch):
    """The real leak path: _start_model_pull's thread, with Ollama 'installed' and 'serving', runs
    `ollama pull`. The path below does not exist, so even without the guard nothing could start."""
    import anthill.inference.ollama as ollama_mod
    import anthill.web.app as app_mod

    monkeypatch.setattr(app_mod, "_set_model_pulling", lambda *a, **k: None)
    monkeypatch.setattr(ollama_mod, "find_ollama_bin", lambda: "/nonexistent/bin/ollama")
    monkeypatch.setattr(ollama_mod, "ensure_serving", lambda *a, **k: True)
    before = len(ollama_spawn_guard)
    app_mod._start_model_pull(1, "x:1b")
    deadline = time.monotonic() + 5
    while len(ollama_spawn_guard) == before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(ollama_spawn_guard) == before + 1
    assert "'pull', 'x:1b'" in ollama_spawn_guard[-1]
    _forget(ollama_spawn_guard, before)
