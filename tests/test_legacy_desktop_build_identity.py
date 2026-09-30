"""The standalone PyInstaller build (Anthill.spec, driven by scripts/build-app.sh /
scripts/build-dmg.sh / install.command) predates the Tauri shell (src-tauri/) and has no
native window of its own - anthill/desktop.py opens the system browser instead whenever
ANTHILL_NO_BROWSER isn't set. It used to share the real, native-windowed Tauri release's
bundle name (Anthill.app) AND identifier (org.onehill.anthill) byte-for-byte, so a build from
this pipeline could silently overwrite - or be mistaken for - an install of the real release,
with the only symptom being a browser tab where a native window should be. These tests pin the
two bundle identities apart so that regressed."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = (REPO_ROOT / "Anthill.spec").read_text()
TAURI_CONF = (REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text()


def test_standalone_build_does_not_claim_the_real_bundle_identifier():
    assert 'bundle_identifier="org.onehill.anthill"' not in SPEC
    # the real, canonical identifier - confirm it's still what the Tauri shell uses, so this
    # test would actually catch the identifiers drifting back together
    assert '"identifier": "org.onehill.anthill"' in TAURI_CONF


def test_standalone_build_does_not_claim_the_real_app_bundle_name():
    assert 'name="Anthill.app"' not in SPEC
    assert '"CFBundleName": "Anthill"' not in SPEC
    assert '"CFBundleDisplayName": "Anthill"' not in SPEC


def test_build_scripts_do_not_produce_the_real_release_filenames():
    # dist/Anthill.app and dist/Anthill.dmg are (or collide with) the real Tauri release's own
    # artifact names - the dev/fallback scripts must never target them.
    for rel in ("scripts/build-app.sh", "scripts/build-dmg.sh", "install.command", "Makefile"):
        text = (REPO_ROOT / rel).read_text()
        assert "dist/Anthill.app" not in text, rel
        assert "dist/Anthill.dmg" not in text, rel
