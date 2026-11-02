"""Hash chain (C12 step 1): hash = sha256(prev_hash || canonical_json(record without hash))."""

from typing import Any

from lexi.common.hashing import canonical_json, chain_hash
from lexi.common.types import AuditRecord

GENESIS = "0" * 64


def recompute(rec: AuditRecord) -> str:
    return chain_hash(rec.prev_hash, canonical_json(rec.model_dump(mode="json", exclude={"hash"})))


def seal(seq: int, prev_hash: str, type_: str, payload: dict[str, Any], trace_id: str, ts: float) -> AuditRecord:
    body = AuditRecord.model_construct(
        schema_version=1, trace_id=trace_id, ts=ts, seq=seq, prev_hash=prev_hash, hash="", type=type_, payload=payload)
    return AuditRecord(**{**body.model_dump(), "hash": recompute(body)})
