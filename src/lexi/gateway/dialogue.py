"""DialogueState (C1 step 5): the mode, the pending probe and the counters of one call. The mode follows what the system just said
(`SpeechPlan.expects`), so a barge-in keeps it and the cell fixed at stage 0 travels in `pending_probe`."""

from dataclasses import dataclass

from lexi.common.types import Dialogue, Expects, ProbeSpec

MODE_OF = {"NEW_COMMAND": "NEW_COMMAND", "PROBE_ANSWER": "PROBE_ANSWER", "UNDO_WINDOW": "UNDO_WINDOW", "NONE": "CLOSING"}


@dataclass
class DialogueState:
    mode: str = "NEW_COMMAND"
    pending_probe: ProbeSpec | None = None
    probes_used: int = 0
    turns_used: int = 0
    reprompts_used: int = 0
    entries_in_session: int = 0

    def on_plan(self, expects: Expects) -> None:
        """A plan that expects a fresh command is a reprompt: C8 issues those only for REPROMPT, and the opening control message is not a plan."""
        self.mode, self.pending_probe = MODE_OF[expects.kind], expects.probe_spec
        self.probes_used += expects.kind == "PROBE_ANSWER"
        self.reprompts_used += expects.kind == "NEW_COMMAND"
        self.entries_in_session += expects.kind == "UNDO_WINDOW"

    def on_turn(self) -> Dialogue:
        """The slice copied into a TurnInput; `turns_used` counts the turn being sent."""
        self.turns_used += 1
        return Dialogue(mode=self.mode, pending_probe=self.pending_probe, probes_used=self.probes_used,  # type: ignore[arg-type]
                        turns_used=self.turns_used, reprompts_used=self.reprompts_used, entries_in_session=self.entries_in_session)

    def new_action(self) -> None:
        """The caller went on after a receipt (C8 step 3, CONTINUE): the next turn starts a new action, the session's entries stay counted."""
        self.mode, self.pending_probe = "NEW_COMMAND", None
        self.probes_used = self.turns_used = self.reprompts_used = 0
