# Spec: Word documents read again with the newest libraries

Status: implemented. Lane: `pillar:knowledge`.

## Problem

With lxml 6.2.0 installed, no `.docx` file can be read. MarkItDown rewrites `word/document.xml` through BeautifulSoup's XML builder before mammoth reads it. lxml 6.2.0 no longer hands the namespace map to that builder, so the `w:` prefixes disappear and mammoth fails with `Could not find the body element: are you sure this is a docx file?`. Verified with python-docx output and with a `.docx` built from the standard library; MarkItDown 0.1.8 and 0.1.9 both fail, and lxml 6.0.0 to 6.1.3 work with either. Any fresh install, and CI (which installs unpinned), picks up lxml 6.2.0. The shipped desktop app is unaffected until `requirements.lock` is regenerated, because it pins lxml 6.1.1.

## Requirements

1. `pyproject.toml` caps lxml below 6.2 (`lxml>=6.0,<6.2`) with a comment saying why, so a fresh install and a regenerated lock stay on a working release.
2. A model-free test reads a `.docx` written with the standard library alone, so a failure points at the reader stack and not at python-docx.
3. The cap is raised only after a lxml release passes `tests/test_office_ingest.py`.

## Acceptance

- With lxml 6.2.0 forced, `tests/test_office_ingest.py` fails (four docx tests); with the cap, `pip install -e ".[ci]"` resolves lxml 6.1.3 and all of `tests/test_office_ingest.py` passes.
- `pytest -n 2 --dist loadscope -q`, `ruff check`, `ruff format --check` and `mypy anthill` pass.
