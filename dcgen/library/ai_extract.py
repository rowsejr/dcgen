"""Datasheet PDFs -> DRAFT records via the Claude Message Batches API (50% off standard prices).

This module only BUILDS requests and PARSES results (no network, no key). `scripts/batch_extract.py`
submits them. Extracted rows always arrive as drafts and still need an engineer's check against the
source PDF: the model reads tables well, but a transcription slip in a sound power level is exactly
what the validators and the checker exist to catch.
"""
from __future__ import annotations

import json

from .records import PROVENANCE, TYPES, spec

SYSTEM = (
    "You extract plant data from manufacturer datasheets and selection outputs into a strict JSON record. "
    "Return only values stated in the document. Convert to the stated units (metres, kW, degC, dB(A)). "
    "If a value is not in the document, use null. Never estimate or infer. Put the document title, revision "
    "and page of each key number in `source`. Record the selection conditions in `conditions`."
)


def tool_schema(rtype: str) -> dict:
    props = {}
    for f in spec(rtype):
        if f.name in ("status", "checked_by", "checked_date", "entered_by"):
            continue
        typ = {"float": "number", "int": "integer", "bool": "boolean", "json": "array"}.get(f.dtype, "string")
        if f.dtype == "list_float":
            props[f.name] = {"type": ["array", "null"], "items": {"type": "number"},
                             "description": f"{f.desc} ({f.unit})".strip()}
        else:
            props[f.name] = {"type": [typ, "null"], "description": f"{f.desc} {('[' + f.unit + ']') if f.unit else ''}".strip()}
    return {"name": "record_plant", "description": f"One {TYPES[rtype]['title']} record",
            "input_schema": {"type": "object", "properties": props,
                             "required": [f.name for f in spec(rtype) if f.required]}}


def batch_request(custom_id: str, document_text: str, rtype: str, model: str) -> dict:
    """One entry for POST /v1/messages/batches."""
    return {"custom_id": custom_id, "params": {
        "model": model, "max_tokens": 2000, "system": SYSTEM,
        "tools": [tool_schema(rtype)], "tool_choice": {"type": "tool", "name": "record_plant"},
        "messages": [{"role": "user", "content": f"Datasheet text:\n\n{document_text[:60000]}"}]}}


def parse_result(message: dict) -> dict:
    """Pull the tool input out of a batch result message; raises ValueError if absent."""
    for block in message.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "record_plant":
            return {k: v for k, v in block["input"].items() if v is not None}
    raise ValueError("no record_plant tool call in the response")
