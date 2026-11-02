"""Tier 2 (C4 step 2): the Evon prompt and the validation of its answer. Evon selects entity ids; it never emits a value."""

import json
import re
from pathlib import Path

from lexi.common.catalog import CONFIG_DIR, Catalog
from lexi.common.types import EvidenceBundle, Intent
from lexi.parser.grammar import SLOT_TYPE

PROMPT_FILE = CONFIG_DIR / "prompts" / "evon_parse.md"
TAGGED = ("AMOUNT", "PARTY", "DATE")


def load_template(path: Path = PROMPT_FILE) -> tuple[str, dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    block = {m.group(1): m.group(2).strip() for m in re.finditer(r"```(\w+)\n(.*?)```", text, re.S)}
    return block["prompt"], json.loads(block["schema"])


def annotated_text(eb: EvidenceBundle) -> str:
    """Words with entities inline as [E2:amount|do hazaar paanch sau]; cue words stay plain."""
    starts = {e.span[0]: e for e in eb.lattice.entities if e.type in TAGGED}
    out: list[str] = []
    i = 0
    while i < len(eb.words):
        e = starts.get(i)
        if e:
            out.append(f"[{e.id}:{e.type.lower()}|{e.surface}]")
            i = e.span[1]
        else:
            out.append(eb.words[i].w)
            i += 1
    return " ".join(out)


def build_prompt(template: str, eb: EvidenceBundle, catalog: Catalog) -> str:
    required = "; ".join(f"{k}={','.join(a.slots)}" for k, a in catalog.actions.items())
    return (template.replace("{ALLOWED_INTENTS}", ", ".join(catalog.actions)).replace("{REQUIRED_SLOTS}", required)
            .replace("{TEXT}", annotated_text(eb)))


def validate(text: str | None, eb: EvidenceBundle, catalog: Catalog) -> tuple[Intent, dict[str, str]] | None:
    """The intent must be in the catalog and every slot an existing entity id of the right type; anything else is INVALID."""
    try:
        data = json.loads(text or "")
        intent, slots = data["intent"], data["slots"]
        if intent not in catalog.actions or not isinstance(slots, dict) or set(slots) - set(SLOT_TYPE):
            return None
        for slot, eid in slots.items():
            ent = eb.lattice.get(eid) if isinstance(eid, str) else None
            if ent is None or ent.type != SLOT_TYPE[slot]:
                return None
    except (ValueError, KeyError, TypeError):
        return None
    return intent, {k: v for k, v in slots.items() if k in catalog.actions[intent].slots}
