#!/usr/bin/env python3
"""Generate the hosted model catalog published at https://anthill.run/model-catalog.json.

The vetted frontier list lives in ``anthill/hosting/model_catalog.json`` (the app's bundled seed and the
single source of truth for *which* models appear). This script reads that seed, refreshes the fast-moving
bit from live sources, and writes ``www/model-catalog.json`` for Cloudflare Pages to serve. Anthill's
"Refresh models" button then pulls it, so the picker keeps up with the open frontier without an app release.

What is refreshed vs. only flagged (deliberately conservative, mirroring scripts/sync_catalog.py: keep the
curated data authoritative, report drift for a human, never silently corrupt it):

  * intelligence  - OVERWRITTEN from the Artificial Analysis Intelligence Index (the ranking genuinely moves
                    monthly; this is the whole point of a refresh).
  * params/license/gated - taken from HuggingFace only to FILL values the seed leaves empty; a disagreement
                    with a vetted value is reported, not overwritten.
  * ollama_tag    - verified against the Ollama registry; an uninstallable tag is reported (and dropped only
                    under --strict-tags), so a registry hiccup can never empty the catalog.
  * new models    - trending open-weight models from frontier labs that are not in the seed are reported as
                    candidates for a human to add; never auto-added.

Usage:
    python scripts/gen_model_catalog.py                 # live refresh (needs AA_API_KEY for intelligence)
    python scripts/gen_model_catalog.py --offline       # re-emit the seed sorted + date-stamped (no network)
    python scripts/gen_model_catalog.py --strict-tags   # also drop rows whose ollama tag is not installable

The pure transforms (apply_intelligence, sort_catalog, validate_rows, ...) are unit-tested without network
in tests/test_model_catalog_gen.py; the fetch_* helpers are the only impure parts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # so `import model_catalog_sources` works as a script
import model_catalog_sources as src

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED = REPO_ROOT / "anthill" / "hosting" / "model_catalog.json"
DEFAULT_OUT = REPO_ROOT / "www" / "model-catalog.json"


# --- pure transforms (no network) ---------------------------------------------------------------------


def _norm(s: str) -> str:
    """A loose key for matching model names across sources: lowercase alphanumerics only."""
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def load_seed(seed_path: Path) -> tuple[str, list[dict]]:
    """Return (note, rows) from the vetted seed. Accepts {note, models:[...]} or a bare list."""
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return str(data.get("note", "")), list(data.get("models") or [])
    return "", list(data or [])


_AA_SCORE_KEYS = (
    "artificial_analysis_intelligence_index",
    "intelligence_index",
    "intelligence",
)


def _aa_score(rec: dict) -> float | None:
    """Pull the intelligence index out of an Artificial Analysis record, tolerant of shape drift."""
    for k in _AA_SCORE_KEYS:
        v = rec.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    ev = rec.get("evaluations")
    if isinstance(ev, dict):
        for k in _AA_SCORE_KEYS:
            v = ev.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


def index_intelligence(aa_models: list[dict]) -> dict[str, float]:
    """Map every identifier an AA record exposes (name/slug/id/hf id) -> its intelligence score."""
    idx: dict[str, float] = {}
    for rec in aa_models:
        if not isinstance(rec, dict):
            continue
        score = _aa_score(rec)
        if score is None:
            continue
        for key in (
            rec.get("name"),
            rec.get("slug"),
            rec.get("id"),
            rec.get("hugging_face_id"),
            rec.get("hf_id"),
        ):
            if isinstance(key, str) and key:
                idx[_norm(key)] = score
    return idx


def apply_intelligence(
    rows: list[dict], score_by_key: dict[str, float], name_overrides: dict[str, str]
) -> tuple[list[dict], list[str]]:
    """Overwrite each row's `intelligence` with the fresh AA score when it can be matched.

    Returns (rows, unmatched_names). Matching tries, in order: an explicit name override, the row's hf_id,
    then its display name. An unmatched row keeps its seed score (reported so a human can add an override).
    """
    out: list[dict] = []
    unmatched: list[str] = []
    for row in rows:
        new = dict(row)
        keys: list[str] = []
        override = name_overrides.get(str(row.get("name", "")))
        if override:
            keys.append(_norm(override))
        keys.append(_norm(str(row.get("hf_id", ""))))
        keys.append(_norm(str(row.get("name", ""))))
        score = next((score_by_key[k] for k in keys if k and k in score_by_key), None)
        if score is not None:
            new["intelligence"] = score
        else:
            unmatched.append(str(row.get("name", "?")))
        out.append(new)
    return out, unmatched


def fill_missing_meta(rows: list[dict], hf_by_id: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Fill only EMPTY seed fields (params_b, license, gated) from HuggingFace; report disagreements.

    Vetted, non-empty values are never overwritten - a mismatch is returned for a human to reconcile.
    """
    out: list[dict] = []
    discrepancies: list[str] = []
    for row in rows:
        new = dict(row)
        meta = hf_by_id.get(str(row.get("hf_id", "")))
        if meta:
            name = row.get("name", "?")
            hp = meta.get("params_b")
            if hp is not None:
                cur = float(row.get("params_b") or 0)
                if cur <= 0:
                    new["params_b"] = hp
                elif cur > 0 and abs(hp - cur) / cur > 0.15:
                    discrepancies.append(f"{name}: params_b seed={cur} hf={hp}")
            hl = meta.get("license")
            if hl and not row.get("license"):
                new["license"] = hl
            hg = meta.get("gated")
            if hg is not None and bool(row.get("gated", False)) != hg:
                discrepancies.append(f"{name}: gated seed={bool(row.get('gated', False))} hf={hg}")
        out.append(new)
    return out, discrepancies


def sort_catalog(rows: list[dict]) -> list[dict]:
    """Strongest first: by intelligence, then total params as a tie-break (matches the seed ordering)."""
    return sorted(
        rows,
        key=lambda r: (float(r.get("intelligence") or 0), float(r.get("params_b") or 0)),
        reverse=True,
    )


def validate_rows(rows: list[dict]) -> list[str]:
    """The loader (anthill/hosting/sizing.py) keeps only rows with a name AND an ollama_tag. Enforce that
    plus name-uniqueness so a broken generator run fails loudly instead of publishing junk."""
    errors: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        name = r.get("name")
        if not name:
            errors.append(f"row {i}: missing name")
        if not r.get("ollama_tag"):
            errors.append(f"row {i} ({name or '?'}): missing ollama_tag")
        if name in seen:
            errors.append(f"duplicate name: {name}")
        if name:
            seen.add(str(name))
    return errors


def discover_candidates(
    trending: list[dict], seed_rows: list[dict], frontier_orgs: frozenset[str]
) -> list[str]:
    """Trending HuggingFace repos from known frontier labs that are not already in the seed."""
    have = {str(r.get("hf_id", "")).lower() for r in seed_rows}
    out: list[str] = []
    for m in trending:
        mid = m.get("id") or m.get("modelId") or ""
        if not isinstance(mid, str) or not mid or mid.lower() in have:
            continue
        org = mid.split("/", 1)[0] if "/" in mid else ""
        if org in frontier_orgs and mid not in out:
            out.append(mid)
    return out


def build_document(rows: list[dict], note: str, generated: str) -> dict:
    return {"schema": 1, "note": note, "generated": generated, "models": rows}


def read_existing(path: Path) -> dict | None:
    """The currently published document, or None if it is absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def generated_for(rows: list[dict], existing: dict | None, today: str) -> str:
    """The date to stamp: today when the catalog actually changed, else the previous date.

    Stamping today unconditionally rewrites the file on every run, so the daily job would commit a
    date-only no-op to main forever (churning history, masking real catalog changes, and redeploying the
    site for nothing). Keeping the previous date when the models are identical leaves the file byte-for-byte
    unchanged, so the job's `git diff --quiet` check correctly skips the commit. It also makes ``generated``
    mean "when the catalog last actually changed", which is the more useful fact than "when the job last ran".
    """
    if existing and existing.get("models") == rows:
        previous = existing.get("generated")
        if isinstance(previous, str) and previous:
            return previous
    return today


# --- network (impure) ---------------------------------------------------------------------------------


def fetch_aa(api_key: str) -> list[dict]:
    """Artificial Analysis Intelligence Index. Returns [] on any failure (caller keeps seed scores)."""
    import httpx

    r = httpx.get(
        src.AA_MODELS_URL,
        headers={"x-api-key": api_key, "User-Agent": src.USER_AGENT},
        timeout=src.HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    models = data.get("data") if isinstance(data, dict) else data
    return models if isinstance(models, list) else []


def _hf_params_b(d: dict) -> float | None:
    st = d.get("safetensors")
    total = None
    if isinstance(st, dict):
        total = st.get("total")
        if total is None and isinstance(st.get("parameters"), dict):
            total = sum(v for v in st["parameters"].values() if isinstance(v, (int, float)))
    if isinstance(total, (int, float)) and total > 0:
        return round(total / 1e9, 1)
    return None


def _hf_license(d: dict) -> str:
    for t in d.get("tags") or []:
        if isinstance(t, str) and t.startswith("license:"):
            return t.split(":", 1)[1]
    return ""


def _hf_gated(d: dict) -> bool | None:
    g = d.get("gated")
    if g is None:
        return None
    return g in (True, "auto", "manual")


def fetch_hf(hf_id: str) -> dict | None:
    """HuggingFace model metadata (params/license/gated). None if the repo is missing or errors."""
    import httpx

    try:
        r = httpx.get(
            src.HF_MODEL_URL.format(hf_id=hf_id),
            headers={"User-Agent": src.USER_AGENT},
            timeout=src.HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None
    return {"params_b": _hf_params_b(d), "license": _hf_license(d), "gated": _hf_gated(d)}


def fetch_hf_trending() -> list[dict]:
    import httpx

    try:
        r = httpx.get(
            src.HF_TRENDING_URL, headers={"User-Agent": src.USER_AGENT}, timeout=src.HTTP_TIMEOUT
        )
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else []
    except Exception:
        return []


def ollama_installable(ollama_tag: str) -> bool:
    """True if `ollama pull <ollama_tag>` would find a manifest (registry returns 200)."""
    import httpx

    repo, _, tag = ollama_tag.partition(":")
    tag = tag or "latest"
    try:
        r = httpx.get(
            src.OLLAMA_MANIFEST_URL.format(repo=repo, tag=tag),
            headers={"Accept": src.OLLAMA_MANIFEST_ACCEPT, "User-Agent": src.USER_AGENT},
            timeout=src.HTTP_TIMEOUT,
            follow_redirects=True,
        )
        return r.status_code == 200
    except Exception:
        return False


# --- orchestration ------------------------------------------------------------------------------------


def _report(lines: list[str]) -> None:
    """Print a report and, in CI, append it to the GitHub step summary."""
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=Path, default=DEFAULT_SEED, help="vetted source catalog")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="published catalog to write")
    ap.add_argument(
        "--offline", action="store_true", help="no network: re-emit the seed sorted + stamped"
    )
    ap.add_argument(
        "--strict-tags", action="store_true", help="drop rows whose ollama tag is not installable"
    )
    args = ap.parse_args()

    note, rows = load_seed(args.seed)
    report: list[str] = [f"## Model catalog generation ({len(rows)} seed models)"]

    if args.offline:
        report.append("- mode: offline (seed re-emitted; no live refresh)")
    else:
        # Intelligence (the ranking) from Artificial Analysis - OPTIONAL and OFF unless AA_API_KEY is set.
        # It is left off by default on purpose: AA's free Data API is licensed "internal use, no
        # redistribution", and this catalog is published publicly (that is redistribution). Only enable it
        # with a redistribution-permitted commercial AA licence. Off, the curated seed scores are kept.
        api_key = os.environ.get("AA_API_KEY", "").strip()
        if api_key:
            try:
                aa = fetch_aa(api_key)
                rows, unmatched = apply_intelligence(
                    rows, index_intelligence(aa), src.AA_NAME_OVERRIDES
                )
                report.append(
                    f"- intelligence: refreshed from Artificial Analysis ({len(aa)} records)"
                )
                if unmatched:
                    report.append(
                        f"  - kept seed score (no AA match, add an override): {', '.join(unmatched)}"
                    )
            except Exception as exc:
                report.append(f"- intelligence: AA fetch failed, kept seed scores ({exc})")
        else:
            report.append("- intelligence: no AA_API_KEY, kept seed scores")

        # params/license/gated: fill blanks from HuggingFace, flag disagreements.
        hf_by_id: dict[str, dict] = {}
        for r in rows:
            hf_id = str(r.get("hf_id", ""))
            if hf_id:
                meta = fetch_hf(hf_id)
                if meta:
                    hf_by_id[hf_id] = meta
        rows, discrepancies = fill_missing_meta(rows, hf_by_id)
        if discrepancies:
            report.append("- metadata drift vs HuggingFace (review; not auto-applied):")
            report.extend(f"  - {d}" for d in discrepancies)

        # ollama tags: verify installable.
        uninstallable = [
            str(r.get("name"))
            for r in rows
            if r.get("ollama_tag") and not ollama_installable(str(r["ollama_tag"]))
        ]
        if uninstallable:
            verb = "dropped" if args.strict_tags else "flagged (still published)"
            report.append(f"- ollama tags not installable, {verb}: {', '.join(uninstallable)}")
            if args.strict_tags:
                rows = [r for r in rows if str(r.get("name")) not in set(uninstallable)]

        # new-model discovery (report only).
        candidates = discover_candidates(fetch_hf_trending(), rows, src.FRONTIER_HF_ORGS)
        if candidates:
            report.append("- trending frontier models not in the seed (consider adding):")
            report.extend(f"  - {c}" for c in candidates)

    rows = sort_catalog(rows)
    errors = validate_rows(rows)
    if errors:
        report.append("- FAILED validation, nothing written:")
        report.extend(f"  - {e}" for e in errors)
        _report(report)
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = read_existing(args.out)
    generated = generated_for(rows, existing, today)
    doc = build_document(rows, note, generated)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if generated == today:
        report.append(f"- wrote {args.out} ({len(rows)} models, generated {generated})")
    else:
        report.append(
            f"- no change ({len(rows)} models); kept generated {generated}, nothing to commit"
        )
    _report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
