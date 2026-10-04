"""Local backup + restore for an Anthill install.

Everything that matters lives *outside* the code, so a code update never touches it - but until
now nothing protected it from disk corruption, an accidental data-dir delete, a bad migration, or a
move to a new machine. This module is that safety net.

What a full backup captures (all of it, in one ``.tar.gz``):
  - the SQLite DB (settings, memory, gold training examples, cache, snippets, teams, MCP, audit)
  - the wikis: the legacy workspace, every personal and project wiki, and the org wiki (markdown pages,
    PRINCIPLES.md, SCHEMA.md, log, and the original uploads kept under ``raw/``)
  - generated files and skills
  - ``secrets.env`` - the JWT + AES-256 keys, WITHOUT which every encrypted field (cloud / AWS /
    Modal / MCP secrets) is permanently unreadable on restore
  - optionally, the fine-tuned org model(s) from Ollama (manifest + blobs), so a restore needs no retrain

Public API:
  create_backup()  -> write a single archive of the whole data dir (+ optional Ollama model)
  restore_backup() -> reload such an archive (takes a safety copy of the current state first)
  snapshot_db()    -> a fast pre-migration copy of the DB + secrets (keeps the last N)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BACKUP_FORMAT = 1
_OLLAMA_REGISTRY = ("registry.ollama.ai", "library")  # manifests/<registry>/<ns>/<name>/<tag>

# Directory trees archived under ``data/``: (key in data_paths(), archive name). Backup and restore both
# walk this one list, so a tree can never be saved without being restored (or the reverse).
_TREES = (
    ("workspace", "data/workspace"),
    ("wikis", "data/wikis"),
    ("org_wiki", "data/org-wiki"),
    ("files", "data/files"),
    ("skills", "data/skills"),
)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def data_paths() -> dict[str, Path]:
    """Resolve where this install keeps its state, honouring the same env vars the app sets.

    Works for both the installed app (``~/Library/Application Support/Anthill``) and a dev/source run
    (``<clone>/data`` + ``<clone>/workspace``)."""
    # Fallbacks mirror each component's own default exactly, so this resolves correctly whether the
    # env vars are set (installed app: all under ANTHILL_HOME) or not (dev: cwd-relative defaults).
    db = Path(os.environ.get("ANTHILL_DB", "data/anthill.db"))
    home = Path(os.environ.get("ANTHILL_HOME", str(db.parent)))
    return {
        "home": home,
        "db": db,
        "workspace": Path(os.environ.get("ANTHILL_WORKSPACE", "workspace")),
        "wikis": Path(os.environ.get("ANTHILL_WIKI_ROOT", "data/wikis")),
        "org_wiki": Path(os.environ.get("ANTHILL_ORG_WIKI", "data/org-wiki")),
        "files": Path(os.environ.get("ANTHILL_FILES_DIR", "data/files")),
        "skills": Path(os.environ.get("ANTHILL_SKILLS_DIR", "skills")),
        "secrets": home / "secrets.env",
    }


def ollama_models_dir() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models")))


# ── archive helpers ───────────────────────────────────────────────────────────


def _sqlite_consistent_copy(src: Path, dest: Path) -> None:
    """Copy a (possibly live) SQLite DB via the backup API - never a torn read."""
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dest)) as d:
        s.backup(d)


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info, io.BytesIO(data))


def _export_ollama_model(model: str, tar: tarfile.TarFile, base: Path) -> bool:
    """Add one Ollama model's manifest + referenced blobs to the archive. Best-effort: returns
    False (no raise) if the model or store isn't found, so a missing model never aborts a backup."""
    name, _, tag = model.partition(":")
    tag = tag or "latest"
    manifest = base.joinpath("manifests", *_OLLAMA_REGISTRY, name, tag)
    if not manifest.is_file():
        return False
    try:
        meta = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return False
    digests = [meta.get("config", {}).get("digest")] + [
        layer.get("digest") for layer in meta.get("layers", [])
    ]
    rel = "/".join((*_OLLAMA_REGISTRY, name, tag))
    tar.add(manifest, arcname=f"ollama/manifests/{rel}")
    for digest in filter(None, digests):
        blob = base / "blobs" / digest.replace(":", "-")
        if blob.is_file():
            tar.add(blob, arcname=f"ollama/blobs/{blob.name}")
    return True


def _safe_members(tar: tarfile.TarFile):
    """Yield only members that extract *inside* the destination (reject absolute / .. paths)."""
    for m in tar.getmembers():
        name = m.name
        if name.startswith("/") or ".." in Path(name).parts:
            continue
        yield m


def _extract_file(tar: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> bool:
    """Write one archive member to ``dest`` (creating parents). Returns False for non-file members."""
    src = tar.extractfile(member)
    if src is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    return True


# ── public API ────────────────────────────────────────────────────────────────


@dataclass
class BackupResult:
    path: Path
    bytes: int
    includes: list[str]
    models: list[str] = field(default_factory=list)


def create_backup(
    out_path: Path | str | None = None,
    *,
    include_model: bool = True,
    model_names: list[str] | None = None,
) -> BackupResult:
    """Write a single ``.tar.gz`` of the whole data dir (and, if asked, the fine-tuned org model).

    ``model_names`` lets the caller pass the exact Ollama models to embed (e.g. ``org1-model-v2``);
    when None and ``include_model`` is set, nothing is embedded (the caller resolves the names)."""
    p = data_paths()
    if out_path is None:
        backups = p["home"] / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        out_path = backups / f"anthill-backup-{_now_stamp()}.tar.gz"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    includes: list[str] = []
    embedded_models: list[str] = []

    with tarfile.open(out_path, "w:gz") as tar:
        # DB - a consistent copy via a temp file next to the source.
        if p["db"].is_file():
            tmp_db = p["db"].with_suffix(p["db"].suffix + f".bkptmp-{_now_stamp()}")
            try:
                _sqlite_consistent_copy(p["db"], tmp_db)
                tar.add(tmp_db, arcname="data/anthill.db")
            finally:
                tmp_db.unlink(missing_ok=True)
            includes.append("db")

        for key, arc in _TREES:
            if p[key].exists():
                tar.add(p[key], arcname=arc)
                includes.append(key)

        # Secrets: the on-disk file if present, else synthesise from the live env so the archive is
        # self-sufficient (without the AES key, encrypted fields can't be decrypted on restore).
        if p["secrets"].is_file():
            tar.add(p["secrets"], arcname="data/secrets.env")
            includes.append("secrets")
        else:
            env_lines = [
                f"{k}={os.environ[k]}"
                for k in ("ANTHILL_ENCRYPTION_KEY", "ANTHILL_JWT_SECRET")
                if os.environ.get(k)
            ]
            if env_lines:
                _add_bytes(tar, "data/secrets.env", ("\n".join(env_lines) + "\n").encode())
                includes.append("secrets")

        if include_model and model_names:
            base = ollama_models_dir()
            for m in model_names:
                if _export_ollama_model(m, tar, base):
                    embedded_models.append(m)
            if embedded_models:
                includes.append("ollama_models")

        manifest = {
            "anthill_backup_format": BACKUP_FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "includes": includes,
            "ollama_models": embedded_models,
            "note": "Contains secrets.env (JWT + AES keys). Store securely.",
        }
        _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())

    return BackupResult(out_path, out_path.stat().st_size, includes, embedded_models)


@dataclass
class RestoreResult:
    restored: list[str]
    models: list[str]
    safety_backup: Path | None


def read_manifest(archive_path: Path | str) -> dict:
    """Return the manifest of a backup archive (raises if it isn't an Anthill backup)."""
    with tarfile.open(archive_path, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        if member is None:
            raise ValueError("not an Anthill backup: manifest.json missing")
        return json.loads(member.read().decode())


def restore_backup(
    archive_path: Path | str,
    *,
    make_safety_backup: bool = True,
) -> RestoreResult:
    """Reload a backup archive into this install's data dir (and Ollama store for the model).

    Overwrites the DB / wikis / files / skills / secrets in place; takes a safety copy of the
    *current* state first (so a mistaken restore is itself reversible). A tree the archive does not
    contain (an older backup has no wikis) is left as it is. Restart the app afterwards - the running
    process holds the old DB open."""
    archive_path = Path(archive_path)
    manifest = read_manifest(archive_path)  # validates it's ours before touching anything
    p = data_paths()

    safety: Path | None = None
    if make_safety_backup and p["db"].is_file():
        safety = p["home"] / "backups" / f"pre-restore-{_now_stamp()}.tar.gz"
        create_backup(safety, include_model=False)

    restored: list[str] = []
    models: list[str] = []
    with tarfile.open(archive_path, "r:gz") as tar:
        members = {m.name: m for m in _safe_members(tar)}

        # DB
        if "data/anthill.db" in members and _extract_file(tar, members["data/anthill.db"], p["db"]):
            restored.append("db")

        # Trees: wipe-and-replace so a restore is a true point-in-time, not a merge.
        for key, arc in _TREES:
            prefix = arc + "/"
            tree_members = [m for n, m in members.items() if n.startswith(prefix)]
            if not tree_members:
                continue
            if p[key].exists():
                shutil.rmtree(p[key])
            p[key].mkdir(parents=True, exist_ok=True)
            for m in tree_members:
                rel = m.name[len(prefix) :]
                if not rel:
                    continue
                dest = p[key] / rel
                if m.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    _extract_file(tar, m, dest)
            restored.append(key)

        # Secrets
        if "data/secrets.env" in members and _extract_file(
            tar, members["data/secrets.env"], p["secrets"]
        ):
            os.chmod(p["secrets"], 0o600)
            restored.append("secrets")

        # Ollama model blobs + manifests back into the store.
        base = ollama_models_dir()
        for n, m in members.items():
            if n.startswith("ollama/") and m.isfile():
                _extract_file(tar, m, base / n[len("ollama/") :])
        if manifest.get("ollama_models"):
            models = list(manifest["ollama_models"])
            restored.append("ollama_models")

    return RestoreResult(restored, models, safety)


def snapshot_db(reason: str = "manual", *, keep: int = 7) -> Path | None:
    """Fast pre-migration copy of the DB (+ secrets) into ``<home>/snapshots``; prune to the last N.

    Cheap enough to call before every schema migration; the safety net for a bad/aborted migration."""
    p = data_paths()
    if not p["db"].is_file():
        return None
    snaps = p["home"] / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    dest = snaps / f"anthill-{_now_stamp()}-{reason}.db"
    _sqlite_consistent_copy(p["db"], dest)
    if p["secrets"].is_file():
        shutil.copy2(p["secrets"], dest.with_name(dest.name + ".secrets.env"))
    # prune oldest, keeping the most recent `keep`
    existing = sorted(snaps.glob("anthill-*.db"))
    for old in existing[:-keep]:
        old.unlink(missing_ok=True)
        old.with_name(old.name + ".secrets.env").unlink(missing_ok=True)
    return dest
