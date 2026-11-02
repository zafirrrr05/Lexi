"""Split authority (C14 step 4): the split of a script is a pure function of its id (common/splits.py)."""

from lexi.common.errors import LexiError
from lexi.common.splits import split_of
from lexi.common.types import GoldLabel


def check(gold: GoldLabel) -> None:
    """A gold label that carries a split different from the hash of its script_id would leak data across splits."""
    if gold.split != split_of(gold.script_id):
        raise LexiError(f"gold {gold.call_id}: split {gold.split} disagrees with split_of({gold.script_id})")
