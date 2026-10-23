"""Ed25519 lab signing key. Generated on first run and kept in data/keys/.

In production, keep the private key on an HSM / smart card; only the public key is
distributed so courts and defence can verify reports offline (scripts/verify_report.py).
"""
from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .. import config


@lru_cache(maxsize=1)
def _private_key() -> Ed25519PrivateKey:
    config.ensure_dirs()
    p = config.KEY_DIR / "lab_ed25519.pem"
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)  # type: ignore[return-value]
    key = Ed25519PrivateKey.generate()
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    p.chmod(0o600)
    (config.KEY_DIR / "lab_ed25519.pub.pem").write_bytes(public_pem(key.public_key()))
    return key


def public_pem(pub: Ed25519PublicKey | None = None) -> bytes:
    pub = pub or _private_key().public_key()
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def fingerprint() -> str:
    from .hashing import sha256_bytes
    return sha256_bytes(public_pem())[:16]


def sign(data: bytes) -> str:
    return base64.b64encode(_private_key().sign(data)).decode()


def verify(data: bytes, signature_b64: str, pub_pem: bytes | None = None) -> bool:
    pub = (serialization.load_pem_public_key(pub_pem) if pub_pem else _private_key().public_key())
    try:
        pub.verify(base64.b64decode(signature_b64), data)  # type: ignore[union-attr]
        return True
    except (InvalidSignature, ValueError):
        return False
