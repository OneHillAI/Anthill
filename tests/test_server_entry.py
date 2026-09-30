"""The cloud-backend container entrypoint (anthill/server.py): it roots all persistent state at the data
volume and generates/persists the JWT + at-rest keys so a fresh pod boots a working, durable backend."""

import os


def test_configure_container_env_roots_data_and_persists_secrets(tmp_path, monkeypatch):
    from anthill.server import configure_container_env

    home = tmp_path / "data"
    for k in (
        "ANTHILL_HOME",
        "ANTHILL_DB",
        "ANTHILL_WORKSPACE",
        "ANTHILL_FILES_DIR",
        "ANTHILL_SKILLS_DIR",
        "ANTHILL_WIKI_ROOT",
        "ANTHILL_ORG_WIKI",
        "ANTHILL_JWT_SECRET",
        "ANTHILL_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHILL_HOME", str(home))

    out = configure_container_env()
    assert out == home and home.exists()
    # every data path is rooted under the volume
    assert os.environ["ANTHILL_DB"] == str(home / "anthill.db")
    assert os.environ["ANTHILL_ORG_WIKI"] == str(home / "org-wiki")
    assert os.environ["ANTHILL_WIKI_ROOT"] == str(home / "wikis")
    # secrets are generated + persisted (so sessions/encrypted fields survive restarts)
    assert (home / "secrets.env").exists()
    jwt = os.environ.get("ANTHILL_JWT_SECRET")
    assert jwt and os.environ.get("ANTHILL_ENCRYPTION_KEY")

    # a second boot reuses the persisted secrets (a fresh key each time would log everyone out)
    monkeypatch.delenv("ANTHILL_JWT_SECRET", raising=False)
    configure_container_env()
    assert os.environ["ANTHILL_JWT_SECRET"] == jwt
