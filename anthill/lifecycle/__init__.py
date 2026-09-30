"""Model lifecycle: provenance, renovation, evaluation, cache warm-up.

The wiki is plain markdown and survives model upgrades untouched - that is the
spine. This module handles everything *around* a model change:

  version.py   - tag each page with the model that wrote it; find stale pages
  evaluate.py  - score a candidate model against the current one before switching
  renovate.py  - regenerate stale pages by re-ingesting their source through a new model
  warmup.py    - re-answer the most-asked questions so the cache reflects the new model
"""

from .evaluate import EvalResult, evaluate_models
from .renovate import RenovationReport, renovate
from .version import PROVENANCE_RE, read_provenance, stale_pages, tag_page
from .warmup import warm_cache

__all__ = [
    "PROVENANCE_RE",
    "EvalResult",
    "RenovationReport",
    "evaluate_models",
    "read_provenance",
    "renovate",
    "stale_pages",
    "tag_page",
    "warm_cache",
]
