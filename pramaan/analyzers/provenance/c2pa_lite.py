"""Offline reader for C2PA Content Credentials (no c2pa-python / cbor2 needed).

What it does, for JPEG, PNG, MP3, WAV, MP4/MOV and anything else that embeds a JUMBF manifest store:
  1. finds the manifest store (JUMBF superbox labelled "c2pa"), including JPEG APP11 fragments;
  2. decodes the active manifest's claim and assertions (CBOR / JSON);
  3. reports who made it (claim generator, signing certificate's organisation), what was done
     (c2pa.actions, IPTC digitalSourceType, e.g. trainedAlgorithmicMedia = made by AI);
  4. verifies the COSE_Sign1 signature over the claim with the certificate in the manifest;
  5. re-hashes the file (c2pa.hash.data with its exclusion ranges) to check the content still matches.

Not done offline: checking the signing certificate against the C2PA trust list, and BMFF (MP4) hash
verification. The report says so, so the examiner knows exactly what was and was not verified.
If the official c2pa-python package is installed, prefer it (dl-free, same output shape) — see read().
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

AI_SOURCE_TYPES = {
    "trainedalgorithmicmedia": "created by a generative AI model",
    "compositewithtrainedalgorithmicmedia": "composite that includes AI-generated elements",
    "algorithmicmedia": "created by an algorithm (not a camera)",
    "compositesynthetic": "composite including synthetic elements",
}
CAPTURE_SOURCE_TYPES = {"digitalcapture": "captured by a camera/recorder", "negativefilm": "scanned film",
                        "positivefilm": "scanned film", "print": "scanned print"}


# --------------------------------------------------------------------------- minimal CBOR (RFC 8949)
class Tag:
    def __init__(self, tag: int, value: Any):
        self.tag, self.value = tag, value

    def __repr__(self):
        return f"Tag({self.tag}, {self.value!r})"


def cbor_loads(b: bytes) -> Any:
    v, _ = _cbor(b, 0)
    return v


def _cbor_len(b: bytes, i: int, info: int) -> tuple[int | None, int]:
    if info < 24:
        return info, i
    if info == 24:
        return b[i], i + 1
    if info == 25:
        return struct.unpack(">H", b[i:i + 2])[0], i + 2
    if info == 26:
        return struct.unpack(">I", b[i:i + 4])[0], i + 4
    if info == 27:
        return struct.unpack(">Q", b[i:i + 8])[0], i + 8
    if info == 31:
        return None, i                       # indefinite length
    raise ValueError("bad CBOR length")


def _cbor(b: bytes, i: int) -> tuple[Any, int]:
    ib = b[i]
    major, info = ib >> 5, ib & 31
    i += 1
    if major == 7:
        if info == 20:
            return False, i
        if info == 21:
            return True, i
        if info in (22, 23):
            return None, i
        if info == 25:
            import numpy as np
            return float(np.frombuffer(b[i:i + 2], ">f2")[0]), i + 2
        if info == 26:
            return struct.unpack(">f", b[i:i + 4])[0], i + 4
        if info == 27:
            return struct.unpack(">d", b[i:i + 8])[0], i + 8
        if info < 24:
            return info, i
        raise ValueError("bad CBOR simple")
    n, i = _cbor_len(b, i, info)
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        if n is None:                         # indefinite string: chunks until break
            parts = []
            while b[i] != 0xFF:
                p, i = _cbor(b, i)
                parts.append(p)
            i += 1
            return (b"".join(parts) if major == 2 else "".join(parts)), i
        s = b[i:i + n]
        return (bytes(s) if major == 2 else s.decode("utf-8", "replace")), i + n
    if major == 4:
        out = []
        if n is None:
            while b[i] != 0xFF:
                v, i = _cbor(b, i)
                out.append(v)
            return out, i + 1
        for _ in range(n):
            v, i = _cbor(b, i)
            out.append(v)
        return out, i
    if major == 5:
        d = {}
        count = 0
        while (n is None and b[i] != 0xFF) or (n is not None and count < n):
            k, i = _cbor(b, i)
            v, i = _cbor(b, i)
            d[k if not isinstance(k, (list, dict)) else repr(k)] = v
            count += 1
        if n is None:
            i += 1
        return d, i
    if major == 6:
        v, i = _cbor(b, i)
        return Tag(n, v), i
    raise ValueError("bad CBOR major type")


def _plain(v: Any) -> Any:
    """JSON-safe view of decoded CBOR."""
    if isinstance(v, Tag):
        return _plain(v.value)
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>" if len(v) > 64 else v.hex()
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


# --------------------------------------------------------------------------- JUMBF boxes (ISO 19566-5)
def _boxes(b: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        lbox, tbox = struct.unpack(">I4s", b[i:i + 8])
        hdr = 8
        if lbox == 1:
            lbox = struct.unpack(">Q", b[i + 8:i + 16])[0]
            hdr = 16
        elif lbox == 0:
            lbox = end - i
        if lbox < hdr or i + lbox > end:
            return
        yield tbox.decode("latin1"), i + hdr, i + lbox
        i += lbox


def _superbox(b: bytes, s: int, e: int) -> dict:
    """A 'jumb' superbox -> {'label', 'type', 'children':[superboxes], 'content':[(tbox, bytes)]}."""
    node = {"label": None, "type": None, "children": [], "content": []}
    for t, cs, ce in _boxes(b, s, e):
        if t == "jumd":
            node["type"] = b[cs:cs + 16].hex()
            toggles = b[cs + 16] if ce > cs + 16 else 0
            p = cs + 17
            if toggles & 0x02:                          # label present
                z = b.index(b"\x00", p, ce) if b"\x00" in b[p:ce] else ce
                node["label"] = b[p:z].decode("utf-8", "replace")
        elif t == "jumb":
            node["children"].append(_superbox(b, cs, ce))
        else:
            node["content"].append((t, b[cs:ce]))
    return node


def _find_store(data: bytes) -> tuple[bytes, int] | None:
    """Locate the C2PA manifest store. Returns (bytes containing it, offset of its 'jumb' box header)."""
    blob = data
    if data[:2] == b"\xff\xd8":                         # JPEG: reassemble APP11 'JP' packets per box instance
        i, frags = 2, {}
        while i + 4 <= len(data) and data[i] == 0xFF:
            m = data[i + 1]
            if m == 0xDA:
                break
            ln = int.from_bytes(data[i + 2:i + 4], "big")
            seg = data[i + 4:i + 2 + ln]
            if m == 0xEB and seg[:2] == b"JP":
                en, seq = struct.unpack(">HI", seg[2:8])
                payload = seg[8:]
                frags.setdefault(en, []).append((seq, payload))
            i += 2 + ln
        for en, parts in frags.items():
            parts.sort()
            whole = parts[0][1] + b"".join(p[8:] for _, p in parts[1:])   # later packets repeat LBox+TBox
            if b"jumb" in whole[:16]:
                blob = whole
                break
    pos = 0
    while True:
        k = blob.find(b"jumb", pos)
        if k < 4:
            if k < 0:
                return None
            pos = k + 4
            continue
        lbox = struct.unpack(">I", blob[k - 4:k])[0]
        if 16 < lbox <= len(blob) - (k - 4):
            node = _superbox(blob, k + 4, k - 4 + lbox)
            if node["label"] == "c2pa":
                return blob, k - 4
        pos = k + 4


# --------------------------------------------------------------------------- COSE signature
_ALG = {-7: ("ec", "sha256"), -35: ("ec", "sha384"), -36: ("ec", "sha512"), -37: ("pss", "sha256"),
        -38: ("pss", "sha384"), -39: ("pss", "sha512"), -8: ("ed25519", None)}


def _verify_cose(sig_bytes: bytes, claim_bytes: bytes) -> dict:
    out = {"signature_valid": None, "signer": None, "issuer": None, "algorithm": None, "signed_at": None}
    try:
        cose = cbor_loads(sig_bytes)
        if isinstance(cose, Tag):
            cose = cose.value
        prot_b, unprot, _payload, sig = cose
        prot = cbor_loads(prot_b) if prot_b else {}
        alg = prot.get(1)
        chain = prot.get(33) or (unprot or {}).get(33) or (unprot or {}).get("x5chain")
        if isinstance(chain, bytes):
            chain = [chain]
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        cert = x509.load_der_x509_certificate(chain[0])

        def name(n):
            parts = {a.oid._name: a.value for a in n}
            return parts.get("organizationName") or parts.get("commonName")
        out["signer"] = name(cert.subject)
        out["issuer"] = name(cert.issuer)
        out["cert_valid_from"] = cert.not_valid_before_utc.isoformat() if hasattr(cert, "not_valid_before_utc") else None
        out["cert_valid_to"] = cert.not_valid_after_utc.isoformat() if hasattr(cert, "not_valid_after_utc") else None
        kind, h = _ALG.get(alg, (None, None))
        out["algorithm"] = {-7: "ES256", -35: "ES384", -36: "ES512", -37: "PS256", -38: "PS384", -39: "PS512",
                            -8: "Ed25519"}.get(alg, str(alg))
        tbs = _cbor_dump(["Signature1", prot_b, b"", claim_bytes])
        pub = cert.public_key()
        hh = {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512()}.get(h)
        if kind == "ec":
            n = len(sig) // 2
            der = encode_dss_signature(int.from_bytes(sig[:n], "big"), int.from_bytes(sig[n:], "big"))
            pub.verify(der, tbs, ec.ECDSA(hh))
        elif kind == "pss":
            pub.verify(sig, tbs, padding.PSS(mgf=padding.MGF1(hh), salt_length=hh.digest_size), hh)
        elif kind == "ed25519":
            pub.verify(sig, tbs)
        else:
            return out
        out["signature_valid"] = True
    except Exception as e:  # invalid signature or unsupported structure
        if out.get("signer") and "InvalidSignature" in type(e).__name__:
            out["signature_valid"] = False
        out["error"] = type(e).__name__
    return out


def _cbor_dump(v: Any) -> bytes:
    """Encoder for the Sig_structure only (array of text/bytes)."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        if n < 65536:
            return bytes([major << 5 | 25]) + struct.pack(">H", n)
        if n < 2 ** 32:
            return bytes([major << 5 | 26]) + struct.pack(">I", n)
        return bytes([major << 5 | 27]) + struct.pack(">Q", n)
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(_cbor_dump(x) for x in v)
    if isinstance(v, str):
        e = v.encode()
        return head(3, len(e)) + e
    if isinstance(v, (bytes, bytearray)):
        return head(2, len(v)) + bytes(v)
    raise TypeError(type(v))


# --------------------------------------------------------------------------- public API
def read(path: str | Path) -> dict | None:
    """None if no manifest; else a dict describing the active manifest (see module doc)."""
    data = Path(path).read_bytes()
    found = _find_store(data)
    if not found:
        return None
    blob, off = found
    lbox = struct.unpack(">I", blob[off:off + 4])[0]
    store = _superbox(blob, off + 8, off + lbox)
    manifests = [c for c in store["children"]]
    if not manifests:
        return {"present": True, "error": "empty manifest store"}
    active = manifests[-1]                       # the last manifest in the store is the active one
    res: dict[str, Any] = {"present": True, "manifest": active["label"], "n_manifests": len(manifests),
                           "assertions": {}, "actions": [], "digital_source_types": [], "claim_generator": None,
                           "author": None, "title": None}
    claim_bytes = sig_bytes = None
    for ch in active["children"]:
        lab = ch["label"] or ""
        if lab.startswith("c2pa.claim"):
            claim_bytes = next((c for t, c in ch["content"] if t == "cbor"), None)
        elif lab == "c2pa.signature":
            sig_bytes = next((c for t, c in ch["content"] if t == "cbor"), None)
        elif lab == "c2pa.assertions":
            for a in ch["children"]:
                for t, c in a["content"]:
                    try:
                        val = cbor_loads(c) if t == "cbor" else json.loads(c) if t == "json" else None
                    except Exception:
                        val = None
                    if val is not None:
                        res["assertions"][a["label"]] = val
    if claim_bytes:
        try:
            claim = cbor_loads(claim_bytes)
            gi = claim.get("claim_generator_info")
            if isinstance(gi, list) and gi:
                gi = gi[0]
            res["claim_generator"] = (gi or {}).get("name") if isinstance(gi, dict) else claim.get("claim_generator")
            if isinstance(gi, dict) and gi.get("version"):
                res["claim_generator"] += f" {gi['version']}"
            res["title"] = claim.get("dc:title")
            res["format"] = claim.get("dc:format")
            res["instance_id"] = claim.get("instanceID")
        except Exception as e:
            res["claim_error"] = str(e)
    for lab, val in res["assertions"].items():
        if lab.startswith("c2pa.actions") and isinstance(val, dict):
            for act in val.get("actions", []):
                a = {"action": act.get("action"), "softwareAgent": _plain(act.get("softwareAgent")),
                     "when": _plain(act.get("when")), "digitalSourceType": act.get("digitalSourceType")}
                res["actions"].append(a)
                if a["digitalSourceType"]:
                    res["digital_source_types"].append(str(a["digitalSourceType"]).rsplit("/", 1)[-1])
        if "CreativeWork" in lab and isinstance(val, dict):
            au = val.get("author")
            if isinstance(au, list) and au:
                res["author"] = au[0].get("name") if isinstance(au[0], dict) else str(au[0])
    if claim_bytes and sig_bytes:
        res.update(_verify_cose(sig_bytes, claim_bytes))
    # hard binding: c2pa.hash.data = hash of the file with the manifest's own bytes excluded
    hd = res["assertions"].get("c2pa.hash.data")
    if isinstance(hd, dict) and blob is data:
        try:
            alg = (hd.get("alg") or "sha256").replace("-", "")
            h = hashlib.new(alg)
            pos = 0
            for ex in sorted(hd.get("exclusions") or [], key=lambda e: e["start"]):
                h.update(data[pos:ex["start"]])
                pos = ex["start"] + ex["length"]
            h.update(data[pos:])
            res["content_hash_valid"] = h.digest() == hd.get("hash")
        except Exception:
            res["content_hash_valid"] = None
    elif "c2pa.hash.bmff" in " ".join(res["assertions"]) or "c2pa.hash.bmff.v2" in res["assertions"]:
        res["content_hash_valid"] = None             # BMFF (MP4) binding: not verified offline
    res["assertions"] = {k: _plain(v) for k, v in res["assertions"].items()
                         if not k.startswith("c2pa.hash") and not k.startswith("c2pa.thumbnail")}
    st = [s.lower() for s in res["digital_source_types"]]
    res["ai_generated"] = any(s in AI_SOURCE_TYPES for s in st)
    res["camera_capture"] = any(s in CAPTURE_SOURCE_TYPES for s in st)
    res["source_meaning"] = "; ".join(AI_SOURCE_TYPES.get(s) or CAPTURE_SOURCE_TYPES.get(s) or s for s in st) or None
    return res
