"""Hybrid cloud routing.

Open-source-first: every query is answered locally. Only when the local answer
falls below a user-defined confidence threshold does the system optionally
escalate to a paid cloud model - and even then, it sends the minimum necessary
(the question alone by default, never the wiki or conversation) and scrubs PII
first. Nothing leaves the perimeter unless the admin explicitly enables it.

  quality.py    confidence assessment of a local answer (cheap, deterministic)
  scrub.py      PII redaction before any text leaves the machine
  providers.py  cloud provider registry (OpenAI-compatible) + cost estimation
  escalate.py   the orchestration: decision (propose) -> consent -> execution (run) -> annotate
"""

from .escalate import (
    EscalationOutcome,
    EscalationProposal,
    HybridPolicy,
    maybe_escalate,
    propose_escalation,
    run_escalation,
)
from .providers import PROVIDERS, CloudProvider, CloudResult, call_cloud
from .quality import QualityVerdict, assess
from .scrub import ScrubResult, presidio_available, restore, scrub

__all__ = [
    "PROVIDERS",
    "CloudProvider",
    "CloudResult",
    "EscalationOutcome",
    "EscalationProposal",
    "HybridPolicy",
    "QualityVerdict",
    "ScrubResult",
    "assess",
    "call_cloud",
    "maybe_escalate",
    "presidio_available",
    "propose_escalation",
    "restore",
    "run_escalation",
    "scrub",
]
