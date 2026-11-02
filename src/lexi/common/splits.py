"""script_id -> split (4.7, C14 step 4). Pure; C11 uses it to decide what to generate and C14 uses it
as the authority when storing, so variants of one script never straddle splits."""

from lexi.common.hashing import sha256_hex

# cumulative fractions of the FULL profile (Train 5k, Valid 1.5k, Calib 6k, Test 2.5k of 15k)
_EDGES = (("Train", 5 / 15), ("Valid", 6.5 / 15), ("Calib", 12.5 / 15), ("Test", 1.0))


def split_of(script_id: str) -> str:
    u = int(sha256_hex(script_id)[:12], 16) / 16**12
    return next(name for name, edge in _EDGES if u < edge)
