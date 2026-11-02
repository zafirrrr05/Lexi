"""Append-only JSONL segments on disk (C12 owns: segments, index, chain head)."""

import os
from collections.abc import Iterator
from pathlib import Path


def segment_name(first_seq: int) -> str:
    return f"seg-{first_seq:010d}.jsonl"


def segment_paths(directory: Path) -> list[Path]:
    return sorted(directory.glob("seg-*.jsonl"))


def scan(path: Path) -> Iterator[tuple[int, bytes]]:
    """(byte offset, line without newline) for every line of a segment."""
    offset = 0
    with open(path, "rb") as f:
        for line in f:
            yield offset, line.rstrip(b"\n")
            offset += len(line)


def repair_tail(directory: Path) -> int:
    """A crash mid-write leaves a last line without newline; its append was never acknowledged, so drop it.
    Returns the number of bytes removed."""
    paths = segment_paths(directory)
    if not paths:
        return 0
    data = paths[-1].read_bytes()
    keep = data.rfind(b"\n") + 1
    if keep == len(data):
        return 0
    with open(paths[-1], "r+b") as f:
        f.truncate(keep)
        f.flush()
        os.fsync(f.fileno())
    return len(data) - keep
