"""Peer-to-peer Node pillar domain package (H2 First Slice + Pilot).

Pure domain and protocol layer: never imports ``anthill.web`` (enforced by
``tests/test_architecture_boundaries.py``). Uses ``anthill.keystore``,
``anthill.hybrid.scrub``, and ``anthill.wiki`` directly.
"""

from __future__ import annotations

NODE_PROTOCOL_VERSION = 1
RECORD_ALG = "ed25519+sha256"
SCOPES = ("personal", "private-group", "internal", "open")
PAYLOAD_TYPES = ("plain", "encrypted")
RECORD_TYPES = (
    "PageChange",
    "Acceptance",
    "Membership",
    "Deletion",
    "Question",
    "Answer",
    "SuggestedChange",
    "Vote",
    "ThreadSettings",
    "Lending",
    "Usage",
)
MAX_CONSULT_TOKENS = 600
PEER_RATE_LIMIT_PER_HOUR = 20
DEFAULT_OPEN_TRUST_THRESHOLD = 10
