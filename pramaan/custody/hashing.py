"""Streaming multi-hash. BSA Section 63 certificates list the hash value and the algorithm used."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024


def hash_stream(f: BinaryIO) -> dict:
    sha256, sha1, md5 = hashlib.sha256(), hashlib.sha1(), hashlib.md5()
    size = 0
    while True:
        b = f.read(CHUNK)
        if not b:
            break
        size += len(b)
        sha256.update(b)
        sha1.update(b)
        md5.update(b)
    return {"sha256": sha256.hexdigest(), "sha1": sha1.hexdigest(), "md5": md5.hexdigest(), "size": size}


def hash_file(path: str | Path) -> dict:
    with open(path, "rb") as f:
        return hash_stream(f)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
