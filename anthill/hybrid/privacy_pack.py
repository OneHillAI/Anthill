"""One-click install of the optional 'privacy pack' - Microsoft Presidio + a small spaCy model - which
adds free-text NAME and LOCATION redaction to the cloud PII scrub (issue #540).

The base install scrubs only structured identifiers (email, phone, SSN, cards, keys); names and places
need this pack. It installs at runtime with pip, so it only works on a source/server install - a frozen
(packaged desktop) app can't pip-install into its bundle, so ``can_install()`` is False there and the
CLI/UI surfaces the limitation instead of a broken button.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

# The small spaCy English model - ~12 MB, vs en_core_web_lg's ~560 MB. scrub._get_analyzer() prefers it.
_SPACY_MODEL = "en_core_web_sm"


def is_frozen() -> bool:
    """True inside the packaged (PyInstaller) desktop app, where runtime pip installs don't work."""
    return bool(getattr(sys, "frozen", False))


def can_install() -> bool:
    """True when the pack can be pip-installed at runtime: a source/server install (not the frozen
    desktop app) with pip importable."""
    return not is_frozen() and importlib.util.find_spec("pip") is not None


def install(timeout: float = 1800.0) -> bool:
    """Install presidio-analyzer + the spaCy model, then re-probe. Returns whether name/location
    redaction is available afterwards. Never raises; blocking, so callers run it in a background thread.
    A no-op (returns the current state) when it can't install."""
    from .scrub import presidio_available, reset_analyzer_cache

    if not can_install():
        return presidio_available()
    steps = (
        [sys.executable, "-m", "pip", "install", "presidio-analyzer>=2.2"],
        [sys.executable, "-m", "spacy", "download", _SPACY_MODEL],
    )
    for cmd in steps:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        except Exception:
            break  # partial install: re-probe below, report honestly whether it now works
    reset_analyzer_cache()  # forget the earlier "unavailable" probe so the new install is picked up
    return presidio_available()
