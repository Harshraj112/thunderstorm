#!/usr/bin/env python3
"""Offline verification of a PRAMAAN report – usable by court staff or defence without the platform.

    python scripts/verify_report.py report.pdf report.manifest.json lab_ed25519.pub.pem [--evidence original.mp4]

Checks: (1) the manifest signature with the lab public key, (2) optionally that the evidence file's
SHA-256 equals the one in the signed manifest.
"""
import argparse
import base64
import hashlib
import json
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("manifest")
    ap.add_argument("pubkey")
    ap.add_argument("--signature", help="base64 signature (printed in Annex B of the report)")
    ap.add_argument("--evidence")
    a = ap.parse_args()
    manifest_bytes = open(a.manifest, "rb").read()
    m = json.loads(manifest_bytes)
    sig = a.signature
    if not sig:
        sig_file = a.manifest.replace(".manifest.json", ".sig")
        try:
            sig = open(sig_file).read().strip()
        except OSError:
            print("Pass --signature (copy it from Annex B of the PDF) or keep <id>.sig next to the manifest.")
            return 2
    pub = serialization.load_pem_public_key(open(a.pubkey, "rb").read())
    try:
        pub.verify(base64.b64decode(sig), manifest_bytes)
        print("✓ Manifest signature valid (lab key)")
    except InvalidSignature:
        print("✗ Manifest signature INVALID")
        return 1
    print(f"  report {m['report_id']} v{m['version']} · {m['lab']} · {m['created_at']}")
    print(f"  verdict: {m['verdict']['category']} ({m['verdict']['confidence']})")
    print(f"  PDF SHA-256 (for your records): {hashlib.sha256(open(a.pdf, 'rb').read()).hexdigest()}")
    if a.evidence:
        h = hashlib.sha256(open(a.evidence, "rb").read()).hexdigest()
        ok = h == m["evidence"]["sha256"]
        print(("✓" if ok else "✗") + f" evidence SHA-256 {'matches' if ok else 'DOES NOT match'} the signed manifest")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
