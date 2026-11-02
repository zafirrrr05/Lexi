"""Identifiers. Anything created inside the pure decision core is derived from content (det_id),
so replaying a recorded decision reproduces the same ids bit for bit (RC7)."""


from lexi.common.hashing import digest


def det_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{digest(list(parts))[:12]}"
