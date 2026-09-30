# PyInstaller spec for the Tauri sidecar: a ONE-FILE `anthill-server` binary.
#   build:  pyinstaller --noconfirm Anthill-sidecar.spec   (driven by scripts/build-sidecar.sh)
# This is the same backend as the standalone Anthill.app (anthill/desktop.py), packaged as a single
# executable so the Tauri desktop shell can bundle it as an `externalBin` sidecar and spawn it in
# headless mode (ANTHILL_NO_BROWSER=1 -> serves on a free port, prints PORT=<n>, no browser). The
# native Tauri window then points at http://127.0.0.1:<port>. Unlike Anthill.spec this emits a plain
# console binary (not a .app/.icns bundle) and does NOT bundle Ollama - the Tauri app supplies the
# local engine separately, reachable the same way regardless of who launched it. Heavy ML libs are
# excluded exactly as in the app build (see Anthill.spec's excludes comment - nothing here needs them;
# embeddings run through that same Ollama runtime), so the two backends behave identically.
import re

from PyInstaller.utils.hooks import collect_all, collect_submodules

# uvicorn loads the app by string ("anthill.web.app:app"), so every anthill + uvicorn submodule must
# be force-included (mirrors Anthill.spec).
hidden = (
    collect_submodules("anthill")
    + collect_submodules("uvicorn")
    + [
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops.auto",
    ]
)

datas = [
    ("anthill/web/templates", "anthill/web/templates"),
    ("anthill/web/static", "anthill/web/static"),
    ("docs", "docs"),  # /docs/* pages read docs/*.md at runtime
    ("skills", "skills"),
    ("anthill/skills_gallery", "anthill/skills_gallery"),  # Skills page's template gallery
    ("assets", "assets"),
    ("anthill/connectors/catalog.json", "anthill/connectors"),
    ("anthill/hosting/model_catalog.json", "anthill/hosting"),  # frontier model catalog seed
]

binaries = []

# Binary- and data-backed deps PyInstaller can miss - collect them whole (mirrors Anthill.spec).
for _pkg in (
    "lancedb",
    "pyarrow",
    "sigstore",
    "docx",
    "pptx",
    "openpyxl",
    "lxml",
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


def _project_version() -> str:
    try:
        with open("pyproject.toml", encoding="utf-8") as fh:
            m = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "0.0.0"


a = Analysis(
    ["anthill/desktop.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["torch", "sentence_transformers", "transformers"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# One-file: pass binaries + datas straight to EXE (no COLLECT/BUNDLE). console=True so the
# `PORT=<n>` line reaches stdout, which the Tauri shell reads to learn the port.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="anthill-server",
    console=True,
)
