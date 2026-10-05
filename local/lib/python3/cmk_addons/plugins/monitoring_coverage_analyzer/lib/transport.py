#!/usr/bin/env python3
"""Encrypted transport file for support data ("mcactl support-data").

The document is encrypted for the maintainer's public key, so the file can be
attached to a public issue: only the holder of the private key can read it.

Format (binary): MAGIC | ephemeral X25519 public key (32) | nonce (12) |
AES-256-GCM ciphertext of the gzip-compressed JSON document. The AES key is
derived with HKDF-SHA256 from the X25519 shared secret; the header is
authenticated as associated data.

Uses the "cryptography" package shipped with Checkmk; imported lazily so
that the rest of the package does not depend on it.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from typing import Any

MAGIC = b"MCA-SUPPORT-1\n"
_INFO = b"mca support data v1"
_PUBLIC_PREFIX = "mca-pub-"
_PRIVATE_PREFIX = "mca-priv-"

# Public key of the maintainer (not secret). Empty: no transport file possible.
MAINTAINER_PUBLIC_KEY = "mca-pub-MxEnb26TnQeT5U0fqTZZh_Srw0bv1vslNJfAzItdKVU="


class TransportError(RuntimeError):
    pass


def _raw_key(text: str, prefix: str) -> bytes:
    text = text.strip()
    if not text.startswith(prefix):
        raise TransportError(f"not a key of this kind (expected '{prefix}...')")
    try:
        raw = base64.urlsafe_b64decode(text[len(prefix):])
    except ValueError as exc:
        raise TransportError(f"invalid key encoding: {exc}") from None
    if len(raw) != 32:
        raise TransportError("invalid key length")
    return raw


def _derive(shared: bytes, ephemeral_pub: bytes, recipient_pub: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=ephemeral_pub + recipient_pub, info=_INFO
    ).derive(shared)


def generate_keypair() -> tuple[str, str]:
    """(private, public) as text; the private key must stay with the maintainer."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = X25519PrivateKey.generate()
    raw_priv = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    raw_pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return (
        _PRIVATE_PREFIX + base64.urlsafe_b64encode(raw_priv).decode(),
        _PUBLIC_PREFIX + base64.urlsafe_b64encode(raw_pub).decode(),
    )


def public_key_configured(public_key: str = "") -> bool:
    return bool((public_key or MAINTAINER_PUBLIC_KEY).strip())


def encrypt(doc: Any, public_key: str = "") -> bytes:
    """Transport file content for doc (JSON-serializable)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    recipient_raw = _raw_key(public_key or MAINTAINER_PUBLIC_KEY, _PUBLIC_PREFIX)
    recipient = X25519PublicKey.from_public_bytes(recipient_raw)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_pub = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key = _derive(ephemeral.exchange(recipient), ephemeral_pub, recipient_raw)
    nonce = os.urandom(12)
    header = MAGIC + ephemeral_pub + nonce
    plain = gzip.compress(json.dumps(doc, sort_keys=True, default=str).encode("utf-8"))
    return header + AESGCM(key).encrypt(nonce, plain, header)


def decrypt(blob: bytes, private_key: str) -> Any:
    """Document from a transport file; raises TransportError on any problem."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 44 + 16:
        raise TransportError("not an MCA support data transport file")
    ephemeral_pub = blob[len(MAGIC):len(MAGIC) + 32]
    nonce = blob[len(MAGIC) + 32:len(MAGIC) + 44]
    header = blob[:len(MAGIC) + 44]
    own = X25519PrivateKey.from_private_bytes(_raw_key(private_key, _PRIVATE_PREFIX))
    own_pub = own.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = _derive(own.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub)), ephemeral_pub, own_pub)
    try:
        plain = AESGCM(key).decrypt(nonce, blob[len(header):], header)
    except InvalidTag:
        raise TransportError("decryption failed (wrong key or damaged file)") from None
    return json.loads(gzip.decompress(plain))
