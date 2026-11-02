"""Detector checkpoints (C10 FAILS: restore after restart). One JSON file, replaced atomically so a crash never leaves half a state."""

import json
import os
from pathlib import Path
from typing import Any

FILE = "sentinel.json"


def save(directory: Path, state: dict[str, Any]) -> None:
    # ponytail: the whole state (references included) is rewritten each time; fine for thousands of scores per cell, switch to append-only deltas beyond that
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, directory / FILE)


def load(directory: Path) -> dict[str, Any] | None:
    """None for a missing or unreadable file: the sentinel then treats every cell as WATCH until recertified."""
    try:
        with open(directory / FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None
