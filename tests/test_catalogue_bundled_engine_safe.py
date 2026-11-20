"""The catalogue never offers a model the bundled Ollama cannot download (#318).

Ollama 0.30.10, the engine the app bundles, refuses four catalogue models at the pull handshake (HTTP 412, "requires a
newer version of Ollama"); 0.40.16 accepts them. Found by starting a pull of every non-cloud catalogue tag on a real
0.30.10 and cancelling once the registry answered (31 of 35 accepted, these 4 refused). They are removed until the bundled
engine is updated and each is tested on it (docs/specs/catalogue-bundled-engine-safe.md). No network in these tests.
"""

import pytest

from anthill.hosting import sizing
from anthill.inference import ollama

REFUSED_BY_0_30 = {
    "qwen3.8:27b",
    "nemotron-3.5-bolt:e4b",
    "nemotron-3.5-lightning:30b",
    "nemotron-3.5-super:120b",
}
TIERS_GB = (8, 16, 24, 32, 64)


def _bundled() -> tuple[int, ...]:
    return tuple(int(part) for part in ollama._OLLAMA_VERSION.split("."))


@pytest.fixture(autouse=True)
def _only_while_the_engine_is_the_old_one():
    if _bundled() >= (0, 40, 0):
        pytest.skip(
            "the bundled engine is newer than the one that refused these rows: retest them on it"
        )


@pytest.fixture
def bundled_catalogue(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ANTHILL_HOME", str(tmp_path)
    )  # no refreshed override: the catalogue the app ships
    return sizing.load_catalog()


def test_the_four_refused_models_are_not_in_the_shipped_catalogue(bundled_catalogue):
    tags = {m.ollama_tag for m in bundled_catalogue}
    assert not tags & REFUSED_BY_0_30, sorted(tags & REFUSED_BY_0_30)


@pytest.mark.parametrize("mem_gb", TIERS_GB)
def test_no_recommendation_is_a_model_the_bundled_engine_refuses(bundled_catalogue, mem_gb):
    recommended = sizing.recommend(
        float(mem_gb), kind="apple", catalog=bundled_catalogue
    ).recommended
    assert recommended is None or recommended.ollama_tag not in REFUSED_BY_0_30
    picks = sizing.recommend_by_family(float(mem_gb), catalog=bundled_catalogue)
    by_family = {
        p.recommended.ollama_tag for p in picks if p.recommended
    }  # a family with nothing that fits has no pick
    assert not by_family & REFUSED_BY_0_30


@pytest.mark.parametrize("mem_gb", TIERS_GB)
def test_the_first_run_suggestion_is_never_a_refused_model(bundled_catalogue, mem_gb):
    setup = sizing.suggest_local_setup(float(mem_gb), "apple", catalog=bundled_catalogue)
    suggested = {member.recommended.ollama_tag for member in setup.members}
    assert suggested and not suggested & REFUSED_BY_0_30


def test_the_rest_of_the_catalogue_is_untouched(bundled_catalogue):
    assert len(bundled_catalogue) == 39
