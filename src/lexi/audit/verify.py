"""Recompute the whole chain from disk (C12 step 4, D16). Flip one byte anywhere and this fails."""

import json
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from lexi.audit.chain import GENESIS, recompute
from lexi.audit.segments import scan, segment_paths
from lexi.common.types import AuditRecord


class VerifyResult(NamedTuple):
    ok: bool
    n_records: int
    first_bad_seq: int | None


class IndexEntry(NamedTuple):
    path: Path
    offset: int
    hash: str


def verify_dir(directory: Path, index: list[IndexEntry] | None = None) -> VerifyResult:
    prev, n = GENESIS, 0
    for path in segment_paths(directory):
        for offset, line in scan(path):
            try:
                rec = AuditRecord(**json.loads(line))
            except (ValueError, ValidationError):
                return VerifyResult(False, n, n + 1)
            if rec.seq != n + 1 or rec.prev_hash != prev or rec.hash != recompute(rec):
                return VerifyResult(False, n, rec.seq)
            prev, n = rec.hash, n + 1
            if index is not None:
                index.append(IndexEntry(path, offset, rec.hash))
    return VerifyResult(True, n, None)
