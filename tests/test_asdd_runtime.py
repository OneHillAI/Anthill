import subprocess
from pathlib import Path


def test_openai_compat_normalizes_base_url():
    """The review runtime adapter must turn a base ASDD_MODEL_URL into the full chat-completions
    endpoint instead of POSTing to it verbatim (which failed closed with no cause)."""
    script = Path(__file__).resolve().parents[1] / ".github/asdd/runtime/openai-compat.test.sh"
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
