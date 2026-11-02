"""Evidence builder (C3): TurnInput -> EvidenceBundle. Pure and deterministic; the lexicons and confusion tables are
loaded once, `build` reads nothing else."""

from datetime import date
from pathlib import Path

from lexi.common.hashing import digest, sha256_hex
from lexi.common.ids import det_id
from lexi.common.types import (
    DialogueCtx,
    EbFlags,
    EvidenceBundle,
    Lattice,
    PartyContext,
    TurnInput,
    Word,
)
from lexi.evidence.confusion import Confusion
from lexi.evidence.dates import dates
from lexi.evidence.lattice import assemble, cues
from lexi.evidence.normalize import LEXICON_DIR, Lexicon, Seq, tokenize
from lexi.evidence.numerals import amounts, is_numeric
from lexi.evidence.parties import OOV, parties
from lexi.evidence.repair import resolve

TRUNCATION_SILENCE_MS = 250
LONG_UTTERANCE_TOKENS = 40
FAILED = ("EMPTY", "SILENCE", "UNAVAILABLE", "TIMEOUT")


def _open_end(seq: Seq) -> bool:
    last = seq.toks[-1]
    return is_numeric(last) or seq.has(last.i, "func", fkind="connector") is not None


class Evidence:
    def __init__(self, lexicons: dict[str, Lexicon], confusion: Confusion) -> None:
        self.lexicons = lexicons
        self.confusion = confusion
        self.version = "ev-" + sha256_hex("|".join(sorted(x.version for x in lexicons.values())) + confusion.version)[:10]

    @classmethod
    def load(cls, lexicon_dir: Path | None = None, confusion_path: Path | None = None) -> "Evidence":
        root = lexicon_dir or LEXICON_DIR
        langs = sorted(p.name for p in root.iterdir() if p.is_dir())
        return cls({lang: Lexicon(lang, root) for lang in langs}, Confusion.load(confusion_path))

    def open_tail(self, words: list[Word], lang: str) -> bool:
        """Does the transcript end on a numeral, scale word or connector? C1 holds the end of utterance then (C1 step 3)."""
        lex = self.lexicons[lang[:2] if lang[:2] in self.lexicons else "hi"]
        seq = tokenize(words, lex)
        return bool(seq.toks) and _open_end(seq)

    def build(self, turn: TurnInput) -> EvidenceBundle:
        lang = turn.asr.lang[:2] if turn.asr.lang[:2] in self.lexicons else "hi"
        lex, view, prof = self.lexicons[lang], turn.ledger_view, turn.profile
        words = turn.asr.words
        failed = turn.asr.asr_status in FAILED or not words
        flags = EbFlags(asr_failed=failed, conf_missing=any(w.conf is None for w in words),
                        long_utterance=turn.long_utterance or len(words) > LONG_UTTERANCE_TOKENS)
        lattice, context = Lattice(), {}
        seq = tokenize(words, lex)
        failed = failed or not seq.toks
        flags.asr_failed = failed
        if not failed:
            numeric_end = _open_end(seq)
            flags.maybe_truncated = numeric_end and prof.trailing_silence_ms < TRUNCATION_SILENCE_MS
            scripts = {t.script for t in seq.toks} - {"digit"}
            flags.mixed_script = len(scripts) > 1
            today = date.fromisoformat(view.today)
            bw, cqi = prof.bandwidth_class, prof.cqi
            found = dates(seq, lex, self.confusion, today, bw, cqi)
            claimed = {k for f in found for k in range(f.i, f.j)}
            found += amounts(seq, lex, self.confusion, bw, cqi, claimed)
            found += parties(seq, view.parties)
            found = resolve(seq, found, self.confusion.repair_post)
            flags.repair_present = any(f.repaired for f in found)
            found += cues(seq, turn.dialogue.mode)
            lattice = assemble(seq, found, flags.maybe_truncated)
            by_id = {p.id: p for p in view.parties}
            for e in lattice.of_type("PARTY"):
                for c in e.candidates:
                    if c.value != OOV and c.value not in context:
                        p = by_id[str(c.value)]
                        context[str(c.value)] = PartyContext(
                            party_id=p.id, display=p.display, balance=p.balance, amount_hist=p.amount_hist, last_entry_ts=p.last_entry_ts,
                            session_recent=[x for x in view.pending_entries if x.party_id == p.id])
        d = turn.dialogue
        eb = EvidenceBundle(
            trace_id=turn.trace_id, ts=turn.ts, eb_id="", session_id=turn.session_id, turn_id=turn.turn_id, mode=d.mode,
            asr_status=turn.asr.asr_status, lang=lang, words=words, profile=prof, lattice=lattice, flags=flags, prior_context=context,
            dialogue_ctx=DialogueCtx(mode=d.mode, probes_used=d.probes_used, turns_used=d.turns_used, reprompts_used=d.reprompts_used,
                                     entries_in_session=d.entries_in_session, cell=d.pending_probe.cell if d.pending_probe else None),
            lexicon_version=lex.version + "+" + self.confusion.version, digest="", today=view.today, clock_ts=turn.clock_ts)
        body = digest(eb)
        return eb.model_copy(update={"digest": body, "eb_id": det_id("EB", turn.session_id, turn.turn_id, body)})
