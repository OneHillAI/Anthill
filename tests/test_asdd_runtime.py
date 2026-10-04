import subprocess
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[1] / ".github/asdd/runtime"


def _run(script):
    return subprocess.run(["bash", str(_RUNTIME / script)], capture_output=True, text=True)


def test_openai_compat_adapter_normalises_the_endpoint_and_survives_bad_replies():
    """The review runtime adapter turns a base ASDD_MODEL_URL into the full chat-completions endpoint, recovers
    the review object from prose-wrapped, fenced or reasoning-content replies, retries without response_format,
    prints nothing when every attempt fails (so the gate fails closed), and logs a key-safe cause."""
    result = _run("openai-compat.test.sh")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_json_extractor_recovers_a_review_and_rejects_non_json():
    result = _run("extract-json.test.sh")
    assert result.returncode == 0, result.stdout + result.stderr
