"""Identity, device keys, at-rest key encryption, recovery kit, and open export (Rules 1, 7).

Each user holds one Ed25519 root key, one Ed25519 device signing key per desktop install, and
one X25519 key-agreement key for encrypted bundles/channels. The root key signs a binding
attestation over the device and X25519 public keys, and can sign device revocations offline.
Private keys are always encrypted at rest with AES-256-GCM using ``anthill.keystore.data_key()``
in the exact ``base64(nonce + ciphertext + tag)`` format validated by ``anthill.stored_values``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import tarfile
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from .. import keystore
from ..wiki import okf
from ..wiki.workspace import Workspace


class IdentityError(ValueError):
    """Invalid key, binding, recovery phrase, or revocation attestation."""


@dataclass(frozen=True)
class KeyPair:
    public_b64: str
    private_b64: str


# -- base64url helpers ---------------------------------------------------------


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception as exc:
        raise IdentityError("Invalid base64url key or signature encoding.") from exc


def key_fingerprint(pub_b64: str, prefix: str = "ed25519") -> str:
    if len(pub_b64) <= 14:
        return f"{prefix}:{pub_b64}"
    return f"{prefix}:{pub_b64[:6]}...{pub_b64[-6:]}"


# -- at-rest encryption (matches anthill/web/crypto.py & stored_values.py) -----


def encrypt_secret(plaintext: str, *, key: bytes | None = None) -> str:
    """AES-256-GCM encrypt to ``base64(nonce + ciphertext + tag)``."""
    data_key = key if key is not None else keystore.data_key()
    nonce = secrets.token_bytes(12)
    ct = AESGCM(data_key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(token: str, *, key: bytes | None = None) -> str:
    """Decrypt a value produced by ``encrypt_secret``."""
    data_key = key if key is not None else keystore.data_key()
    raw = base64.b64decode(token)
    if len(raw) < 28:
        raise IdentityError("Encrypted key payload is truncated.")
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(data_key).decrypt(nonce, ct, None).decode("utf-8")


# -- Ed25519 & X25519 primitives -----------------------------------------------


def generate_ed25519_keypair() -> KeyPair:
    priv = Ed25519PrivateKey.generate()
    return _ed25519_pair(priv)


def ed25519_from_seed(seed: bytes) -> KeyPair:
    if len(seed) != 32:
        raise IdentityError("Ed25519 seed must be exactly 32 bytes.")
    return _ed25519_pair(Ed25519PrivateKey.from_private_bytes(seed))


def _ed25519_pair(priv: Ed25519PrivateKey) -> KeyPair:
    raw_priv = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    raw_pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return KeyPair(public_b64=b64u_encode(raw_pub), private_b64=b64u_encode(raw_priv))


def generate_x25519_keypair() -> KeyPair:
    priv = X25519PrivateKey.generate()
    raw_priv = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    raw_pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return KeyPair(public_b64=b64u_encode(raw_pub), private_b64=b64u_encode(raw_priv))


def load_x25519_private(private_b64: str) -> X25519PrivateKey:
    raw = b64u_decode(private_b64)
    if len(raw) != 32:
        raise IdentityError("X25519 private key must be 32 bytes.")
    return X25519PrivateKey.from_private_bytes(raw)


def load_x25519_public(public_b64: str) -> X25519PublicKey:
    raw = b64u_decode(public_b64)
    if len(raw) != 32:
        raise IdentityError("X25519 public key must be 32 bytes.")
    return X25519PublicKey.from_public_bytes(raw)


def ed25519_sign(private_b64: str, data: bytes) -> str:
    raw = b64u_decode(private_b64)
    if len(raw) != 32:
        raise IdentityError("Ed25519 private key must be 32 bytes.")
    sig = Ed25519PrivateKey.from_private_bytes(raw).sign(data)
    return b64u_encode(sig)


def ed25519_verify(public_b64: str, data: bytes, signature_b64: str) -> bool:
    if not public_b64 or not signature_b64:
        return False
    try:
        raw_pub = b64u_decode(public_b64)
        raw_sig = b64u_decode(signature_b64)
        if len(raw_pub) != 32 or len(raw_sig) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(raw_pub).verify(raw_sig, data)
        return True
    except (IdentityError, InvalidSignature, ValueError):
        return False


# -- Root <-> Device binding & offline revocation ------------------------------


def _binding_bytes(root_pub: str, device_pub: str, x25519_pub: str, device_name: str) -> bytes:
    payload = {
        "purpose": "anthill-node-device-binding-v1",
        "root_pub": root_pub,
        "device_pub": device_pub,
        "x25519_pub": x25519_pub,
        "device_name": device_name,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_device_binding(
    root_priv_b64: str, root_pub: str, device_pub: str, x25519_pub: str, device_name: str
) -> str:
    return ed25519_sign(
        root_priv_b64, _binding_bytes(root_pub, device_pub, x25519_pub, device_name)
    )


def verify_device_binding(
    root_pub: str, device_pub: str, x25519_pub: str, device_name: str, binding_sig: str
) -> bool:
    return ed25519_verify(
        root_pub, _binding_bytes(root_pub, device_pub, x25519_pub, device_name), binding_sig
    )


def _revocation_bytes(root_pub: str, device_pub: str, revoked_at: str) -> bytes:
    payload = {
        "purpose": "anthill-node-device-revocation-v1",
        "root_pub": root_pub,
        "device_pub": device_pub,
        "revoked_at": revoked_at,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_device_revocation(
    root_priv_b64: str, root_pub: str, device_pub: str, revoked_at: str
) -> str:
    return ed25519_sign(root_priv_b64, _revocation_bytes(root_pub, device_pub, revoked_at))


def verify_device_revocation(
    root_pub: str, device_pub: str, revoked_at: str, revocation_sig: str
) -> bool:
    return ed25519_verify(
        root_pub, _revocation_bytes(root_pub, device_pub, revoked_at), revocation_sig
    )


# -- Deterministic recovery kit (32 seed bytes + 1 SHA-256 checksum byte) ------

# 256 unambiguous lowercase words (1 byte per word, zero external dependencies).
_WORDS: tuple[str, ...] = (
    "acorn",
    "agate",
    "alder",
    "amber",
    "anchor",
    "anise",
    "anvil",
    "apple",
    "apron",
    "arbor",
    "arrow",
    "aspen",
    "atlas",
    "atoll",
    "audio",
    "avow",
    "badge",
    "baker",
    "balsa",
    "banjo",
    "baron",
    "basil",
    "basin",
    "batch",
    "bayou",
    "beach",
    "beech",
    "berry",
    "bison",
    "blade",
    "blaze",
    "bloom",
    "board",
    "bough",
    "boxer",
    "braid",
    "brake",
    "brass",
    "bread",
    "brick",
    "brine",
    "brook",
    "broom",
    "brush",
    "bugle",
    "cabin",
    "cable",
    "cacao",
    "cairn",
    "camel",
    "canal",
    "canoe",
    "caper",
    "cargo",
    "carob",
    "carol",
    "cedar",
    "chalk",
    "charm",
    "chase",
    "chess",
    "chime",
    "cider",
    "cigar",
    "civic",
    "clamp",
    "clasp",
    "clear",
    "cleft",
    "cliff",
    "cloak",
    "clock",
    "cloud",
    "clove",
    "coach",
    "coast",
    "cobra",
    "comet",
    "conch",
    "coral",
    "couch",
    "crane",
    "crate",
    "creek",
    "crest",
    "crisp",
    "crown",
    "crumb",
    "cubic",
    "cumin",
    "curve",
    "cycle",
    "daisy",
    "dance",
    "datum",
    "delta",
    "depot",
    "diode",
    "diver",
    "dodge",
    "donor",
    "draft",
    "drape",
    "drift",
    "drill",
    "druid",
    "dunes",
    "eagle",
    "earth",
    "easel",
    "ebony",
    "egret",
    "elbow",
    "elder",
    "ember",
    "entry",
    "epoch",
    "equal",
    "fable",
    "facet",
    "fauna",
    "feast",
    "ferry",
    "fiber",
    "field",
    "finch",
    "fjord",
    "flame",
    "flask",
    "flint",
    "flock",
    "flora",
    "flute",
    "focus",
    "forge",
    "forum",
    "foxes",
    "frame",
    "frost",
    "fruit",
    "fungi",
    "gable",
    "gavel",
    "gecko",
    "glade",
    "gleam",
    "glint",
    "globe",
    "gnome",
    "grain",
    "grape",
    "graph",
    "grass",
    "gravel",
    "green",
    "grill",
    "grove",
    "guide",
    "guild",
    "gully",
    "hazel",
    "heath",
    "hedge",
    "helix",
    "heron",
    "hinge",
    "hippo",
    "hitch",
    "holly",
    "honey",
    "horse",
    "hound",
    "hover",
    "humus",
    "icing",
    "igloo",
    "index",
    "ingot",
    "inlet",
    "ivory",
    "jaunt",
    "jewel",
    "joker",
    "joust",
    "jumbo",
    "juniper",
    "kayak",
    "kebab",
    "ketch",
    "knack",
    "knell",
    "koala",
    "krill",
    "label",
    "ladle",
    "larch",
    "laser",
    "latch",
    "laurel",
    "ledge",
    "lemon",
    "lemur",
    "lever",
    "lilac",
    "linen",
    "llama",
    "lobby",
    "lodge",
    "lofty",
    "lotus",
    "lunar",
    "lynx",
    "macaw",
    "magic",
    "mango",
    "manor",
    "maple",
    "marsh",
    "mason",
    "medal",
    "melon",
    "merit",
    "metal",
    "metro",
    "midge",
    "mink",
    "mirth",
    "mocha",
    "modal",
    "molar",
    "moose",
    "moral",
    "mossy",
    "motel",
    "motor",
    "mound",
    "mount",
    "mouse",
    "mural",
    "music",
    "myrrh",
    "nacre",
    "navel",
    "nexus",
    "noble",
    "north",
    "notch",
    "novel",
    "nudge",
    "nutmeg",
    "oaken",
    "oasis",
    "oboe",
    "ocean",
    "ochre",
    "olive",
)
_WORD_TO_BYTE: dict[str, int] = {w: i for i, w in enumerate(_WORDS)}


def export_recovery_kit(root_priv_b64: str) -> str:
    """Encode the 32-byte Ed25519 root seed as 33 checksummed recovery words."""
    seed = b64u_decode(root_priv_b64)
    if len(seed) != 32:
        raise IdentityError("Root private key seed must be 32 bytes.")
    checksum = hashlib.sha256(b"anthill-recovery-v1:" + seed).digest()[0]
    raw = seed + bytes([checksum])
    return " ".join(_WORDS[b] for b in raw)


def restore_root_from_recovery_kit(phrase: str) -> KeyPair:
    """Restore the exact Ed25519 root keypair from a 33-word recovery kit."""
    tokens = [w.strip().lower() for w in phrase.replace("\n", " ").split() if w.strip()]
    if len(tokens) != 33:
        raise IdentityError("Recovery kit must contain exactly 33 words.")
    raw = bytearray()
    for word in tokens:
        val = _WORD_TO_BYTE.get(word)
        if val is None:
            raise IdentityError(f"Unknown recovery word: {word!r}")
        raw.append(val)
    seed, got_checksum = bytes(raw[:32]), raw[32]
    expected_checksum = hashlib.sha256(b"anthill-recovery-v1:" + seed).digest()[0]
    if got_checksum != expected_checksum:
        raise IdentityError("Recovery kit checksum failed; check for a typo or swapped word.")
    return ed25519_from_seed(seed)


def verify_recovery_kit(phrase: str, expected_root_pub: str) -> bool:
    """True when ``phrase`` restores the exact ``expected_root_pub``."""
    try:
        pair = restore_root_from_recovery_kit(phrase)
    except IdentityError:
        return False
    return pair.public_b64 == expected_root_pub


# -- Open-format exit export (Rule 7: OKGF wiki + signed JSONL log) ------------


def build_open_export_tgz(
    workspaces: dict[str, Workspace],
    records: list[dict[str, Any]],
    identity_public: dict[str, Any],
) -> bytes:
    """Build an open-format .tar.gz containing OKGF wikis, signed JSONL log, and public identity.

    Strictly excludes private keys.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # 1. Public identity & Hills metadata (never private keys).
        safe_identity = {
            k: v
            for k, v in identity_public.items()
            if "priv" not in k.lower() and "secret" not in k.lower()
        }
        _add_tar_text(
            tar, "identity-public.json", json.dumps(safe_identity, indent=2, sort_keys=True) + "\n"
        )

        # 2. Signed record log as newline-delimited JSON.
        jsonl = "\n".join(json.dumps(r, sort_keys=True) for r in records)
        _add_tar_text(tar, "records.jsonl", (jsonl + "\n") if jsonl else "")

        # 3. OKGF wiki bundles per workspace.
        for label, ws in sorted(workspaces.items()):
            if not ws.wiki.exists():
                continue
            pages: list[tuple[str, str, float]] = []
            for p in ws.pages():
                try:
                    pages.append((p.stem, p.read_text(encoding="utf-8"), p.stat().st_mtime))
                except OSError:
                    continue
            log_md = ws.log_md.read_text(encoding="utf-8") if ws.log_md.exists() else ""
            principles_md = (
                ws.principles_md.read_text(encoding="utf-8") if ws.principles_md.exists() else ""
            )
            bundle = okf.export_bundle(
                pages, scope=ws.scope or label, log_md=log_md, principles_md=principles_md
            )
            for rel, text in sorted(bundle.items()):
                _add_tar_text(tar, f"wiki/{label}/{rel}", text)

    return buf.getvalue()


def _add_tar_text(tar: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))
