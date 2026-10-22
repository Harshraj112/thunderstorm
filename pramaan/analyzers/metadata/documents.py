"""Documents, empty and unrecognised files: what can be said without pixels or samples."""
from __future__ import annotations

from ..base import INFO, Context, Finding
from .standard import office_props, pdf_info


def run(ctx: Context) -> list[Finding]:
    real = ctx.shared.get("real_type")
    if real in ("docx", "xlsx", "pptx"):
        p = office_props(ctx.path)
        txt = ", ".join(f"{k}: {p[k]}" for k in ("creator", "lastModifiedBy", "created", "modified", "Application")
                        if p.get(k))
        return [Finding(code="DOC_PROPS", module="document", family="metadata", title="Office document properties",
                        status=INFO, explanation=txt or "No author or date properties recorded.", data={"props": p})]
    if real == "pdf":
        p = pdf_info(ctx.path)
        return [Finding(code="DOC_PROPS", module="document", family="metadata", title="PDF document properties",
                        status=INFO, explanation=", ".join(f"{k}: {v}" for k, v in p.items()) or "No Info dictionary.",
                        data={"props": p})]
    return []
