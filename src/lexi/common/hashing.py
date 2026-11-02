"""Canonical JSON and SHA-256 helpers. Hash inputs must be byte-stable across runs (RC7, RC8)."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace, no NaN. Pydantic models are dumped in JSON mode first."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False).encode()


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def digest(obj: Any) -> str:
    """Short content digest of any JSON-able value or model."""
    return sha256_hex(canonical_json(obj))[:16]


def chain_hash(prev_hash: str, payload: bytes) -> str:
    """hash = sha256(prev_hash || canonical_json) as in C12 step 1."""
    return sha256_hex(prev_hash.encode() + payload)
