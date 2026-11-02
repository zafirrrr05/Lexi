# Evon parse prompt (C4 step 2)

Tier 2 is called only when the deterministic grammar is AMBIGUOUS or FAIL. Evon never produces a value: it selects entity ids
(E1, E2, ...) from the tagged text and names the intent. The placeholders ALLOWED_INTENTS, REQUIRED_SLOTS and TEXT are filled
from the action catalog and the evidence bundle; the simulated Evon parses exactly this format.

```prompt
TASK: Select entity ids and the intent. Output JSON only: {"intent": "...", "slots": {"party": "E1", "amount": "E2"}}
ALLOWED_INTENTS: {ALLOWED_INTENTS}
REQUIRED_SLOTS: {REQUIRED_SLOTS}
TEXT: {TEXT}
JSON:
```

The response schema (versioned with this file):

```schema
{"type": "object", "required": ["intent", "slots"], "additionalProperties": false,
 "properties": {"intent": {"type": "string"},
                "slots": {"type": "object", "additionalProperties": false,
                          "properties": {"party": {"type": "string"}, "amount": {"type": "string"}, "date": {"type": "string"}}}}}
```
