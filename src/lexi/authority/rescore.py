"""Release pairing (C6 step 9): re-score stored feature vectors with the staged scorer so a certificate is about the scorer that will run."""

from collections.abc import Callable, Mapping, Sequence

from lexi.common.errors import RegistryError
from lexi.common.types import CalibrationRecord

Rescore = Callable[[Sequence[Mapping[str, float | None]]], list[float]]


def rescored(records: Sequence[CalibrationRecord], fn: Rescore, scorer_version: str) -> list[CalibrationRecord]:
    scores = iter(fn([v for r in records for v in r.feature_vectors]))
    return [r.model_copy(update={"scores": [next(scores) for _ in r.feature_vectors], "scorer_version": scorer_version}) for r in records]


def require_version(records: Sequence[CalibrationRecord], scorer_version: str) -> None:
    """Without a rescoring function the recorded scores must already come from the release scorer; anything else is a mismatched pair (F14)."""
    stale = {r.scorer_version for r in records} - {scorer_version}
    if stale:
        raise RegistryError(f"records scored by {sorted(stale)}, release is {scorer_version}")
