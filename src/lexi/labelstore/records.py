"""Label comparison and TrainingData record builders (CONTRACT section 3). Pure functions over decoded audit payloads."""

from collections.abc import Mapping
from typing import Any

from lexi.common.types import GoldLabel

SLOTS = ("party", "amount", "date")


def tops(sp: Mapping[str, Any]) -> dict[str, Any]:
    """The values this stage would commit: intent plus the top candidate of every slot."""
    slots = sp["slots"]
    intent = sp.get("intent") or (slots.get("intent") or {}).get("top") or sp["ap"].get("intent")
    return {"intent": intent, **{k: slots[k]["top"] for k in SLOTS if k in slots}}


def gold_values(gold: GoldLabel) -> dict[str, Any]:
    return {"intent": gold.gold_intent, **gold.gold_slots}


def is_wrong(committed: Mapping[str, Any], gold: GoldLabel) -> int:
    """10.4: y = 1 iff any critical slot of the committed values differs from gold."""
    return int(any(committed.get(k) != v for k, v in gold_values(gold).items()))


def confusion_records(call: Mapping[str, Any]) -> list[dict[str, Any]]:
    """call = {"gold": GoldLabel, "stage0": decoded stage-0 payload}. Token rows fit P(heard | spoken); repair rows fit 0.85/0.15."""
    gold, eb = call["gold"], call["stage0"]["sp"]["eb"]
    rows: list[dict[str, Any]] = [{
        "type": "token", "lang": eb["lang"], "cqi": eb["profile"]["cqi"], "bandwidth_class": eb["profile"]["bandwidth_class"],
        "spoken": gold.spoken, "heard": [{"w": w["w"], "conf": w["conf"]} for w in eb["words"]]}]
    want = {"AMOUNT": gold.gold_slots.get("amount"), "DATE": gold.gold_slots.get("date"), "PARTY": gold.gold_slots.get("party")}
    for ent in eb["lattice"]["entities"]:
        if ent["features"]["repaired"] and want.get(ent["type"]) is not None:
            rows.append({"type": "repair", "post_correct": want[ent["type"]] in [c["value"] for c in ent["candidates"] if c["parse_id"] == 0]})
    return rows


def probe_model_records(call: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per answered probe. call = {"gold", "stages": {stage_index: decoded payload}}."""
    gold, stages = call["gold"], call["stages"]
    out = []
    for j, payload in sorted(stages.items()):
        po = payload.get("probe_outcome")
        if not po:
            continue
        spec, kind, eb = po["spec"], po["kind"], payload["sp"]["eb"]
        truth = _truth(gold, spec["slot"])
        options = spec["options"]
        if spec["kind"] == "READBACK":
            base = tops(stages[j - 1]["sp"])
            has_gold = all(base.get(k) == v for k, v in gold_values(gold).items())
        else:
            has_gold = truth in options
        correct: bool | None
        if kind == "CHOICE":
            correct = po["chosen_index"] is not None and options[po["chosen_index"]] == truth
        elif kind in ("VALUE", "NO_WITH_VALUE"):
            correct = str(po.get("value")) == truth
        elif kind in ("YES", "NO"):
            correct = has_gold if kind == "YES" else not has_gold
        else:
            correct = None
        out.append({"probe_kind": spec["kind"], "slot": spec["slot"], "lang": eb["lang"], "cqi": eb["profile"]["cqi"],
                    "answer_kind": kind, "answer_correct": correct, "options_has_gold": has_gold})
    return out


def _truth(gold: GoldLabel, slot: str) -> str:
    """Gold value of a probed slot as the string a probe option carries. The duplicate probe's truth comes from the script tags."""
    if slot == "dup":
        return "same" if "dup_same" in gold.tags else "new"
    return str(gold.gold_intent if slot == "intent" else gold.gold_slots.get(slot))
