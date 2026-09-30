"""Issue #488: both tarball extractors (the Ollama runtime download and the Modal adapter fetch) must
use the PEP 706 ``filter="data"`` so a member like ``../escape`` cannot path-traverse out of the extract
dir. This was a bandit HIGH+HIGH (B202) that reddened the Security audit workflow on main; the Modal
adapter tarball is not checksum-gated, so the traversal path was real."""

import io
import tarfile

import pytest


def _traversal_tgz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"pwned"
        info = tarfile.TarInfo("../escape.txt")  # writes to the PARENT of the extract dir
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_data_filter_blocks_path_traversal(tmp_path):
    into = tmp_path / "into"
    into.mkdir()
    with tarfile.open(fileobj=io.BytesIO(_traversal_tgz()), mode="r:gz") as tf:
        with pytest.raises(tarfile.FilterError):
            tf.extractall(into, filter="data")
    assert not (tmp_path / "escape.txt").exists(), "traversal member escaped the extract dir"


def test_both_extractors_pass_the_data_filter():
    """Regression guard: if either extractall drops filter='data', bandit B202 returns and traversal
    reopens - so pin it here too, next to the behavioral test above."""
    import inspect

    from anthill.inference import ollama
    from anthill.training.backends import endpoint

    for mod in (ollama, endpoint):
        calls = [ln.strip() for ln in inspect.getsource(mod).splitlines() if ".extractall(" in ln]
        assert calls, f"{mod.__name__}: expected an extractall call to guard"
        for call in calls:
            assert 'filter="data"' in call or "filter='data'" in call, (
                f"{mod.__name__} extractall is missing the traversal-safe filter: {call}"
            )
