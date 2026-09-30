"""Phase 6a: local model disk-capacity probing and fit checks.

Nothing in this codebase checked disk space before this - only RAM/VRAM and speed. A model that fits in
memory and runs at a usable speed but will not fit on disk is not actually usable, so this is a THIRD,
independent precondition alongside `onprem_council_fits` (memory) and `is_usable_speed` (speed).
"""

from anthill.hosting import sizing


def test_free_disk_gb_defaults_to_the_existing_ollama_models_dir_helper(monkeypatch):
    # anthill.backup.ollama_models_dir() already knows the OLLAMA_MODELS-aware path; free_disk_gb()
    # must reuse it rather than re-deriving the same env-var lookup a second time.
    from anthill import backup

    monkeypatch.setattr(backup, "ollama_models_dir", lambda: "/mock/ollama-models")
    seen_paths: list[str] = []

    def fake_disk_usage(path):
        seen_paths.append(path)
        return type("_Usage", (), {"free": 100 * (1024**3)})()

    monkeypatch.setattr(sizing.shutil, "disk_usage", fake_disk_usage)

    assert sizing.free_disk_gb() == 100.0
    assert seen_paths == ["/mock/ollama-models"]


def test_free_disk_gb_uses_an_explicit_path_when_given(monkeypatch):
    seen_paths: list[str] = []

    def fake_disk_usage(path):
        seen_paths.append(path)
        return type("_Usage", (), {"free": 50 * (1024**3)})()

    monkeypatch.setattr(sizing.shutil, "disk_usage", fake_disk_usage)

    assert sizing.free_disk_gb("/explicit/path") == 50.0
    assert seen_paths == ["/explicit/path"]


def test_free_disk_gb_returns_none_on_failure(monkeypatch):
    def failing_disk_usage(_path):
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(sizing.shutil, "disk_usage", failing_disk_usage)

    assert sizing.free_disk_gb("/missing/path") is None


def test_one_model_fits_but_summed_council_is_refused(monkeypatch):
    # Each 32B model needs family_download_gb(32.0) = round(32.0 * 0.55, 1) = 17.6 GB.
    # 50.0 GB free - 20.0 GB headroom = 30.0 GB usable: one model (17.6) fits, two (35.2) do not.
    monkeypatch.setattr(sizing, "free_disk_gb", lambda path=None: 50.0)

    assert sizing.onprem_council_fits_on_disk([32.0])
    assert not sizing.onprem_council_fits_on_disk([32.0, 32.0])


def test_headroom_boundary_is_inclusive(monkeypatch):
    # 10B needs family_download_gb(10.0) = 5.5 GB. 5.5 + 20.0 headroom = 25.5 GB required.
    monkeypatch.setattr(sizing, "free_disk_gb", lambda path=None: 25.5)
    assert sizing.onprem_council_fits_on_disk([10.0])  # exactly enough

    monkeypatch.setattr(sizing, "free_disk_gb", lambda path=None: 25.4)
    assert not sizing.onprem_council_fits_on_disk([10.0])  # one-tenth GB short


def test_empty_model_list_never_probes_or_blocks(monkeypatch):
    def unexpected_probe(path=None):
        raise AssertionError("an empty model list must never probe the disk")

    monkeypatch.setattr(sizing, "free_disk_gb", unexpected_probe)

    assert sizing.onprem_council_fits_on_disk([])


def test_unprobed_disk_fails_open_not_closed(monkeypatch):
    # A bad probe (None) must never spuriously block a save/recommendation.
    monkeypatch.setattr(sizing, "free_disk_gb", lambda path=None: None)

    assert sizing.onprem_council_fits_on_disk([32.0, 32.0])
