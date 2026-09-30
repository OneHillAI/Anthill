"""Container entrypoint for the always-on org backend (the Anthill web app on a cloud host).

Points all persistent state at the data volume (``ANTHILL_HOME``, default ``/data``) and ensures the JWT +
at-rest encryption keys exist there - so sessions and encrypted fields survive restarts - then serves the
web app. Used by ``docker/Dockerfile.backend``; the desktop app uses ``anthill.desktop`` instead. The image
ships only code: an org's data is seeded onto its own pod separately, never baked into the image.
"""

from __future__ import annotations

import os
from pathlib import Path


def configure_container_env() -> Path:
    """Root all persistent state at ANTHILL_HOME (the mounted volume; default /data) and ensure secrets.

    Each var is only set if the operator did not already override it, so a deployment can point pieces
    elsewhere. Returns the data home.
    """
    from .desktop import _ensure_secrets, _ensure_tls_certs

    _ensure_tls_certs()
    home = Path(os.environ.get("ANTHILL_HOME", "/data"))
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ANTHILL_HOME", str(home))
    os.environ.setdefault("ANTHILL_DB", str(home / "anthill.db"))
    os.environ.setdefault("ANTHILL_WORKSPACE", str(home / "workspace"))
    os.environ.setdefault("ANTHILL_FILES_DIR", str(home / "files"))
    os.environ.setdefault("ANTHILL_SKILLS_DIR", str(home / "skills"))
    os.environ.setdefault("ANTHILL_WIKI_ROOT", str(home / "wikis"))
    os.environ.setdefault("ANTHILL_ORG_WIKI", str(home / "org-wiki"))
    _ensure_secrets(home)
    return home


def main() -> None:
    configure_container_env()
    import uvicorn

    uvicorn.run(
        "anthill.web.app:app",
        host=os.environ.get("ANTHILL_HOST", "0.0.0.0"),
        port=int(os.environ.get("ANTHILL_PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
