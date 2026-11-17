"""The protected backup container (``*.anthillbak``): a byte stream encrypted in authenticated chunks.

Layout::

    header   magic "ANTHBAK" | version (1) | scrypt n (u32) | r (u8) | p (u8) | salt length (u8) | salt
             | chunk size (u32) | nonce prefix (8 random bytes)
    chunks   AES-256-GCM, each holding up to ``chunk size`` bytes of the inner stream (the last may be
             shorter), written back to back as ciphertext + tag

The key is scrypt(passphrase, salt). Chunk ``i`` is sealed with nonce ``prefix + i`` and associated data
``header + i + final flag``. The header is therefore bound to every chunk, a chunk cannot move or repeat
(its counter is part of what is authenticated), and the final chunk carries a flag, so cutting the file
short, even exactly between two chunks, fails. Both directions stream: nothing larger than two chunks is
held in memory.

The inner stream is the gzip-compressed tar the backup always was. Spec: docs/specs/encryption-at-rest-phase1.md (Protected backups).
"""

from __future__ import annotations

import io
import os
import struct
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"ANTHBAK"
VERSION = 1
EXTENSION = ".anthillbak"
DEFAULT_CHUNK_SIZE = 1 << 20
_CHUNK_MIN, _CHUNK_MAX = 4096, 16 << 20
_TAG = 16
_PREFIX_LEN = 8
_FIXED = struct.Struct(">7sBIBBB")
_TAIL = struct.Struct(">I8s")
_SALT_LEN = 16
SCRYPT_N, SCRYPT_R, SCRYPT_P = 131072, 8, 1  # 128 MiB, about half a second
# A header is untrusted input: its cost settings are capped field by field AND together, so a crafted file
# cannot make a restore allocate gigabytes or run for minutes.
_N_MIN, _N_MAX, _R_MAX, _P_MAX = 2**14, 2**18, 16, 4
_MEMORY_MAX = 256 << 20  # 128 * n * r bytes
_WORK_MAX = 2**21  # n * r * p


class BackupCryptoError(Exception):
    """A protected backup could not be written or read. Messages never carry a secret."""


class NotProtectedBackup(BackupCryptoError):
    """The file does not start like a protected backup of a version this program reads."""


class WrongPassphrase(BackupCryptoError):
    """The first chunk did not open: the passphrase is wrong, or the file is damaged at its start (a cut at the
    first chunk boundary looks the same, so the message says both)."""


class DamagedBackup(BackupCryptoError):
    """A later chunk did not open, or the file ends too early or has extra bytes."""


def is_protected(head: bytes) -> bool:
    return head.startswith(MAGIC)


def _key(passphrase: str, n: int, r: int, p: int, salt: bytes) -> bytes:
    ok = (
        _N_MIN <= n <= _N_MAX
        and n & (n - 1) == 0
        and 1 <= r <= _R_MAX
        and 1 <= p <= _P_MAX
        and 128 * n * r <= _MEMORY_MAX
        and n * r * p <= _WORK_MAX
    )
    if not ok:
        raise BackupCryptoError(
            "this backup asks for key-derivation settings that are not accepted"
        )
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode())


def _read_upto(fp: BinaryIO, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        got = fp.read(size - len(out))
        if not got:
            break
        out += got
    return bytes(out)


def _nonce(prefix: bytes, counter: int) -> bytes:
    return prefix + counter.to_bytes(4, "big")


def _aad(header: bytes, counter: int, final: bool) -> bytes:
    return header + counter.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


class EncryptingWriter(io.RawIOBase):
    """A file-like object for ``tarfile.open(fileobj=..., mode="w|gz")``. Closing it writes the final chunk."""

    def __init__(
        self,
        fp: BinaryIO,
        passphrase: str,
        *,
        chunk_size: int | None = None,
        n: int | None = None,
    ) -> None:
        if not passphrase:
            raise BackupCryptoError("a passphrase is needed")
        chunk_size = chunk_size or DEFAULT_CHUNK_SIZE
        if not _CHUNK_MIN <= chunk_size <= _CHUNK_MAX:
            raise BackupCryptoError("the chunk size is out of range")
        n = n or SCRYPT_N
        r, p = SCRYPT_R, SCRYPT_P
        salt, prefix = os.urandom(_SALT_LEN), os.urandom(_PREFIX_LEN)
        self._header = (
            _FIXED.pack(MAGIC, VERSION, n, r, p, len(salt)) + salt + _TAIL.pack(chunk_size, prefix)
        )
        self._seal_chunk = AESGCM(_key(passphrase, n, r, p, salt)).encrypt
        self._fp, self._chunk, self._prefix = fp, chunk_size, prefix
        self._buf = bytearray()
        self._counter = 0
        fp.write(self._header)

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self._buf += data
        while len(self._buf) > self._chunk:
            self._emit(bytes(self._buf[: self._chunk]), final=False)
            del self._buf[: self._chunk]
        return len(data)

    def _emit(self, plain: bytes, *, final: bool) -> None:
        if self._counter >= 1 << 32:
            raise BackupCryptoError("the backup is too large for this format")
        self._fp.write(
            self._seal_chunk(
                _nonce(self._prefix, self._counter),
                plain,
                _aad(self._header, self._counter, final),
            )
        )
        self._counter += 1

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._emit(bytes(self._buf), final=True)
            self._buf.clear()
        finally:
            super().close()


class DecryptingReader(io.RawIOBase):
    """A file-like object for ``tarfile.open(fileobj=..., mode="r|gz")`` that yields verified plaintext.

    The first chunk is opened here, so a wrong passphrase or a hostile header fails before the caller reads
    anything. A tar reader stops at its own end marker, so call ``drain()`` afterwards: it checks every
    remaining chunk and that the final one is there."""

    def __init__(self, fp: BinaryIO, passphrase: str) -> None:
        if not passphrase:
            raise BackupCryptoError("a passphrase is needed")
        self._fp = fp
        fixed = _read_upto(fp, _FIXED.size)
        if len(fixed) < _FIXED.size or not is_protected(fixed):
            raise NotProtectedBackup("this is not a protected Anthill backup")
        _, version, n, r, p, salt_len = _FIXED.unpack(fixed)
        if version != VERSION:
            raise NotProtectedBackup("this backup was made by a newer version of Anthill")
        if not 16 <= salt_len <= 64:
            raise BackupCryptoError(
                "this backup asks for key-derivation settings that are not accepted"
            )
        rest = _read_upto(fp, salt_len + _TAIL.size)
        if len(rest) < salt_len + _TAIL.size:
            raise DamagedBackup("the backup ends inside its header")
        salt = rest[:salt_len]
        self._chunk, self._prefix = _TAIL.unpack(rest[salt_len:])
        if not _CHUNK_MIN <= self._chunk <= _CHUNK_MAX:
            raise BackupCryptoError("this backup asks for a chunk size that is not accepted")
        self._header = fixed + rest
        self._open_chunk = AESGCM(_key(passphrase, n, r, p, salt)).decrypt
        self._counter = 0
        self._carry = b""
        self._view = memoryview(b"")
        self._pos = 0
        self._eof = False
        self._advance()

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while self._pos >= len(self._view) and not self._eof:
            self._advance()
        n = min(len(b), len(self._view) - self._pos)
        b[:n] = self._view[self._pos : self._pos + n]
        self._pos += n
        return n

    def drain(self) -> None:
        while not self._eof:
            self._advance()
        self._view, self._pos = memoryview(b""), 0

    def _advance(self) -> None:
        sealed_max = self._chunk + _TAG
        blob = self._carry + _read_upto(self._fp, sealed_max + 1 - len(self._carry))
        final = len(blob) <= sealed_max
        if final:
            self._carry = b""
        else:
            self._carry, blob = blob[sealed_max:], blob[:sealed_max]
        if self._counter >= 1 << 32:
            raise DamagedBackup("the backup has more chunks than the format allows")
        try:
            plain = self._open_chunk(
                _nonce(self._prefix, self._counter),
                blob,
                _aad(self._header, self._counter, final),
            )
        except InvalidTag:
            if self._counter == 0:
                raise WrongPassphrase("wrong passphrase, or the file is damaged") from None
            raise DamagedBackup(
                "the backup is damaged, cut short or has been changed; nothing was restored"
            ) from None
        self._counter += 1
        self._view, self._pos, self._eof = memoryview(plain), 0, final
