"""Provenance checks that apply to every modality: Content Credentials (C2PA)."""
from __future__ import annotations

from ..base import FAIL, INFO, PASS, WARN, Context, Finding
from . import c2pa_lite

MOD = "provenance"


def c2pa(ctx: Context) -> list[Finding]:
    try:
        r = c2pa_lite.read(ctx.path)
    except Exception as e:
        return [Finding(code="PROV_C2PA", module=MOD, family="provenance", title="Content Credentials could not be read",
                        status=INFO, explanation=f"A manifest-like structure was found but could not be decoded ({e}).")]
    ctx.shared["c2pa"] = r
    if not r:
        return [Finding(code="PROV_C2PA", module=MOD, family="provenance", title="No Content Credentials (C2PA)",
                        status=INFO, reliability=0.0,
                        explanation="The file carries no C2PA manifest. Most cameras and apps do not add one, and "
                                    "messaging apps and editors strip them, so absence says nothing either way.")]
    who = r.get("signer") or r.get("claim_generator") or "unknown signer"
    gen = r.get("claim_generator") or "unknown tool"
    agents = [a.get("softwareAgent") for a in r.get("actions", []) if a.get("softwareAgent")]
    agent = agents[0] if agents else None
    agent_s = (f"{agent.get('name')} {agent.get('version') or ''}".strip() if isinstance(agent, dict) else str(agent)) \
        if agent else None
    sig = r.get("signature_valid")
    hashok = r.get("content_hash_valid")
    verified = ("signature verified with the signer's certificate" if sig else
                "signature DOES NOT verify" if sig is False else "signature not checked")
    verified += ("; file content matches the signed hash" if hashok else
                 "; file content does NOT match the signed hash" if hashok is False else
                 "; content hash not checked (MP4 binding)")
    data = {"c2pa": {k: r.get(k) for k in ("claim_generator", "signer", "issuer", "algorithm", "signature_valid",
                                          "content_hash_valid", "digital_source_types", "source_meaning", "author",
                                          "title", "format", "manifest", "n_manifests", "cert_valid_from",
                                          "cert_valid_to")},
            "actions": r.get("actions", [])[:12], "software_agent": agent_s}
    out: list[Finding] = []
    if r.get("ai_generated"):
        strong = sig is not False
        out.append(Finding(
            code="PROV_C2PA_AI", module=MOD, family="provenance",
            title=f"AI-generated, declared by Content Credentials ({who})",
            status=FAIL, llr=4.6 if (sig and hashok is not False) else 3.0 if strong else 2.0,
            reliability=0.97 if sig else 0.8,
            explanation=f"The file carries a C2PA manifest made by {gen}" + (f" (tool: {agent_s})" if agent_s else "") +
                        f", signed by {who}" + (f" (certificate issued by {r['issuer']})" if r.get("issuer") else "") +
                        f". Its action list says the content was {r.get('source_meaning')} "
                        f"(IPTC digitalSourceType: {', '.join(r.get('digital_source_types') or [])}). "
                        f"Verification: {verified}. The signer's certificate was not checked against the C2PA "
                        "trust list (offline).",
            innocent=["None for the generation itself: the generator signed this statement. A genuine recording "
                      "cannot acquire a valid generator signature."],
            data=data))
    elif r.get("camera_capture"):
        out.append(Finding(code="PROV_C2PA_CAPTURE", module=MOD, family="provenance",
                           title=f"Content Credentials: captured by a device ({who})",
                           status=PASS if sig else INFO, llr=-1.5 if (sig and hashok) else 0.0, reliability=0.9,
                           explanation=f"The C2PA manifest from {gen} declares a camera/recorder capture. {verified}.",
                           data=data))
    else:
        out.append(Finding(code="PROV_C2PA", module=MOD, family="provenance",
                           title=f"Content Credentials present ({who})", status=INFO, reliability=0.0,
                           explanation=f"A C2PA manifest from {gen} is present with actions "
                                       f"{', '.join(a.get('action') or '?' for a in r.get('actions', [])[:6])}. {verified}.",
                           data=data))
    if hashok is False:
        out.append(Finding(code="PROV_C2PA_ALTERED", module=MOD, family="provenance",
                           title="Content changed after the Content Credentials were signed",
                           status=FAIL, llr=3.2, reliability=0.95,
                           explanation="The manifest records a SHA-256 hash of the file's content. Re-hashing the file "
                                       "now gives a different value, so the pixels/samples were modified after signing "
                                       "while the old credentials were left in place.",
                           innocent=["A metadata-only tool rewrote bytes outside the excluded manifest region"],
                           data=data))
    if sig is False:
        out.append(Finding(code="PROV_C2PA_BADSIG", module=MOD, family="provenance",
                           title="Content Credentials signature does not verify", status=WARN, llr=2.0, reliability=0.9,
                           explanation="The claim was altered after signing, or the manifest was forged.", data=data))
    return out
