# PyInstaller spec for the self-contained macOS dev/fallback build.
#   build:  pyinstaller --noconfirm Anthill.spec     (driven by scripts/build-app.sh)
# Produces dist/Anthill-DevBuild.app with a bundled Python + the anthill package + its deps, so
# it runs on a clean Mac with no repo, no system Python, and no install.command. The heavy ML
# libs (torch / sentence-transformers) are excluded to keep the dmg small; the app then runs
# embeddings-free - the embedder, semantic cache, and memory layers degrade to keyword-only
# search (see cache/embedder.safe_embed). Chat still answers. To get semantic retrieval, run
# from a full Python env with the embedding deps installed (a future option: bundle them, or
# install into a writable dir on first run - a frozen bundle cannot pip-install into itself).
#
# Deliberately NOT named/identified as "Anthill" (see BUNDLE below): this predates the Tauri
# shell (src-tauri/) and has no native window of its own - anthill/desktop.py opens the system
# browser instead (ANTHILL_NO_BROWSER unset). It used to share the real app's bundle name AND
# identifier byte-for-byte, so a build from here could silently overwrite - or be mistaken for -
# an install of the real, native-windowed Tauri release, with no way to tell them apart short of
# noticing the missing window. Keep this identity distinct from org.onehill.anthill / "Anthill"
# for exactly that reason if this file is ever touched again.
import os
import re

from PyInstaller.utils.hooks import collect_all, collect_submodules

_ICON = "assets/anthill.icns" if os.path.exists("assets/anthill.icns") else None


def _project_version() -> str:
    """Single source of truth: the bundle version tracks pyproject.toml (so the .app reports
    the real release, not a hardcoded constant that silently drifts)."""
    try:
        with open("pyproject.toml", encoding="utf-8") as fh:
            m = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "0.0.0"


_VERSION = _project_version()

# uvicorn loads the app by string ("anthill.web.app:app"), so every anthill + uvicorn
# submodule must be force-included.
hidden = collect_submodules("anthill") + collect_submodules("uvicorn") + [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.loops.auto",
]

datas = [
    ("anthill/web/templates", "anthill/web/templates"),
    ("anthill/web/static", "anthill/web/static"),
    ("docs", "docs"),  # the /docs/* pages read docs/*.md at runtime; bundle them or they 500
    ("skills", "skills"),
    ("anthill/skills_gallery", "anthill/skills_gallery"),  # Skills page's template gallery
    ("assets", "assets"),
    ("anthill/connectors/catalog.json", "anthill/connectors"),  # connector gallery (offline-safe)
    ("anthill/hosting/model_catalog.json", "anthill/hosting"),  # frontier model catalog seed (else load_catalog falls back to the 4-model safety net)
]

# Bundle the Ollama runtime (the local-model engine) when scripts/build-app.sh staged it into
# build-ollama/. Shipped verbatim as a self-contained folder (the binary + its sibling GPU/runner
# libs), so the app serves the local model with nothing else installed. find_ollama_bin prefers it.
if os.path.isdir("build-ollama"):
    datas += [("build-ollama", "ollama-runtime")]
    datas += [("licenses", "licenses")]  # third-party notices (Ollama MIT) ship with the app

binaries = []

# Binary-/data-backed deps PyInstaller can silently under-collect - grab each one whole.
# The office libs matter for correctness, not just size: python-docx and python-pptx ship DATA
# templates (default.docx / default.pptx) that create() opens at runtime via Document()/Presentation().
# PyInstaller bundles the modules but drops that package data, so the import succeeds and the export
# then fails only in the packaged app. collect_all pulls their data (+ lxml's binary). Magika supplies
# MarkItDown's file classifier as an ONNX model, so its model data and runtime must ship too. The
# packaged-app smoke test (`Anthill --selfcheck-office`, run by scripts/build-app.sh) guards Office.
for _pkg in (
    "lancedb",
    "pyarrow",
    "docx",
    "pptx",
    "openpyxl",
    "lxml",
    "sigstore",
    "markitdown",
    "pdfminer",
    "pypdfium2",
    "magika",
    "onnxruntime",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hidden += _h
    except Exception:
        pass

a = Analysis(
    ["anthill/desktop.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    # Nothing in anthill/ imports these anymore (embeddings now run through the already-bundled Ollama
    # runtime, see anthill/cache/embedder.py) - kept as a defensive exclude against a PyInstaller
    # dependency-analysis quirk accidentally re-pulling ~2GB of torch/transformers via some other
    # package's optional import path, not because anything currently needs them omitted.
    excludes=["torch", "sentence_transformers", "transformers"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Anthill", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Anthill")
app = BUNDLE(
    coll,
    name="Anthill-DevBuild.app",
    icon=_ICON,
    bundle_identifier="org.onehill.anthill.devbuild",
    version=_VERSION,
    info_plist={
        "CFBundleName": "Anthill (Dev Build)",
        "CFBundleDisplayName": "Anthill (Dev Build)",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
)
