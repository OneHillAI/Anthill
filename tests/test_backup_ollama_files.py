"""The only Ollama files `anthill/backup.py` touches (#227).

A new backup holds no model files, and its manifest is Anthill's own. The one place Ollama's store is written is
restoring an OLDER plain archive that carries an `ollama/` folder: those files are copied under the Ollama models
folder by their relative path, an existing file is set aside first, and a failure puts everything back.
Anthill never reads an Ollama manifest, so a different on-disk layout (the 0.40.2 background rewrite) does not
change what this code does; what it would do to a store a newer engine has already rewritten is written down as
an open question in docs/specs/ollama-engine-upgrade-gate.md, not tested here.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from test_backup_protected import PHRASE, make_install, plain_archive, tree

from anthill import backup as bk
from anthill import backup_crypto

OLD_BLOB = b"old-layout-blob"
NEW_BLOB = b"blob-from-the-archive"
MANIFEST_BODY = json.dumps({"schemaVersion": 2, "layers": []}).encode()


def _with_ollama_files(plain: Path, extra: dict[str, bytes]) -> Path:
    """The plain archive plus `ollama/...` members (a tar.gz cannot be appended to, so copy it across)."""
    out = plain.with_name("with-models.tar.gz")
    with tarfile.open(plain) as src, tarfile.open(out, "w:gz") as dst:
        for member in src:
            dst.addfile(member, src.extractfile(member))
        for name, data in extra.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    return out


def _restoring_install(tmp_path, monkeypatch):
    store = tmp_path / "ollama-store"
    monkeypatch.setenv("OLLAMA_MODELS", str(store))
    a = make_install(tmp_path, monkeypatch, "a", keychain=False)
    old = plain_archive(tmp_path / "old.tar.gz", a.home, key=a.key)
    make_install(tmp_path, monkeypatch, "b", keychain=False)
    return store, old


def test_an_older_archives_model_files_land_under_the_models_folder_by_relative_path(
    tmp_path, monkeypatch
):
    store, old = _restoring_install(tmp_path, monkeypatch)
    archive = _with_ollama_files(
        old,
        {
            "ollama/blobs/sha256-aaa": NEW_BLOB,
            "ollama/manifests/registry.ollama.ai/library/qwen2.5/3b": MANIFEST_BODY,
        },
    )
    bk.restore_backup(archive, passphrase=PHRASE)
    assert (store / "blobs" / "sha256-aaa").read_bytes() == NEW_BLOB
    assert (store / "manifests/registry.ollama.ai/library/qwen2.5/3b").read_bytes() == MANIFEST_BODY


def test_an_existing_model_file_is_replaced_and_nothing_is_left_aside(tmp_path, monkeypatch):
    store, old = _restoring_install(tmp_path, monkeypatch)
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "sha256-aaa").write_bytes(OLD_BLOB)
    bk.restore_backup(
        _with_ollama_files(old, {"ollama/blobs/sha256-aaa": NEW_BLOB}), passphrase=PHRASE
    )
    assert (store / "blobs" / "sha256-aaa").read_bytes() == NEW_BLOB
    assert not list(
        store.rglob("*before-restore*")
    )  # the set-aside copy is removed once all went well


def test_a_failure_part_way_puts_the_models_folder_back_as_it_was(tmp_path, monkeypatch):
    store, old = _restoring_install(tmp_path, monkeypatch)
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "sha256-aaa").write_bytes(OLD_BLOB)
    archive = _with_ollama_files(
        old, {"ollama/blobs/sha256-aaa": NEW_BLOB, "ollama/blobs/sha256-bbb": NEW_BLOB}
    )
    real_place = bk._Undo.place
    placed = []

    def failing_place(self, staged, dest):
        if store in Path(dest).parents:
            placed.append(Path(dest).name)
            if len(placed) == 2:  # the second model file cannot be moved in
                raise OSError("disk full")
        return real_place(self, staged, dest)

    monkeypatch.setattr(bk._Undo, "place", failing_place)
    with pytest.raises((bk.BackupError, OSError)):
        bk.restore_backup(archive, passphrase=PHRASE)
    assert len(placed) == 2  # the failure really happened on the second file
    assert (
        store / "blobs" / "sha256-aaa"
    ).read_bytes() == OLD_BLOB  # put back from the set-aside copy
    assert not (store / "blobs" / "sha256-bbb").exists()  # the partly restored file is gone


def test_a_new_backup_holds_no_ollama_model_files(tmp_path, monkeypatch):
    store = tmp_path / "ollama-store"
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "sha256-aaa").write_bytes(OLD_BLOB)
    (store / "manifests").mkdir()
    (store / "manifests" / "m").write_bytes(MANIFEST_BODY)
    monkeypatch.setenv("OLLAMA_MODELS", str(store))
    make_install(tmp_path, monkeypatch, "a", keychain=True)
    res = bk.create_backup(passphrase=PHRASE)
    with open(res.path, "rb") as fh:
        reader = backup_crypto.DecryptingReader(fh, PHRASE)
        with tarfile.open(fileobj=reader, mode="r|gz") as t:
            names = [m.name for m in t if m.isfile()]
    assert names and not [n for n in names if n.startswith("ollama/") or "sha256-" in n]
    assert bk.read_manifest(res.path, PHRASE)["ollama_models"] == []


def test_the_backup_manifest_is_anthills_own_not_an_ollama_one(tmp_path, monkeypatch):
    make_install(tmp_path, monkeypatch, "a", keychain=True)
    manifest = bk.read_manifest(bk.create_backup(passphrase=PHRASE).path, PHRASE)
    assert "anthill_backup_format" in manifest
    assert not {"schemaVersion", "layers", "config", "mediaType"} & set(
        manifest
    )  # an OCI/Ollama manifest


def test_restoring_never_reads_the_models_folder_to_decide_anything(tmp_path, monkeypatch):
    # A models folder that is not even a directory does not stop a restore of an archive with no model files.
    store, old = _restoring_install(tmp_path, monkeypatch)
    store.write_text("not a folder")
    before = tree(Path(str(tmp_path / "b")))
    bk.restore_backup(old, passphrase=PHRASE)
    assert tree(Path(str(tmp_path / "b"))) != before  # the data was restored
    assert store.read_text() == "not a folder"  # and the store was left alone
