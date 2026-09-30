"""Runtime cross-check verifier for consequential agent outputs.

Independently cross-checks the outputs an agent produces on the user's behalf - wiki writes, scheduled
task results, and agentic actions - with DETERMINISTIC checks first and then, when a model of a
DIFFERENT FAMILY than the producer is available, a second-model sanity check. Returns a ``Verdict``
(ok + confidence + reason) that the caller surfaces to the user when it is low-confidence or on a
protected/consequential path. Advisory by contract: the verifier recommends, a human approves the
consequential paths. Spec: engineering-plans/RUNTIME_CROSSCHECK_VERIFIER.md.
"""

from .verify import (
    KINDS,
    CrossCheck,
    Verdict,
    crosscheck_for,
    default_crosscheck,
    pick_verifier_model,
    verify,
)

__all__ = [
    "KINDS",
    "CrossCheck",
    "Verdict",
    "crosscheck_for",
    "default_crosscheck",
    "pick_verifier_model",
    "verify",
]
