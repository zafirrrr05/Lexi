"""C1 endpointing in audio time: the EOU rule, the numeric hold, the timeouts and the noisy-line fallback."""

from lexi.common.types import PrismaStream, Word
from lexi.gateway.endpointing import Endpointer, Utterance

CFG = {"frame_ms": 20, "eou_silence_ms": 350, "numeric_hold": True, "numeric_hold_ms": 600, "numeric_hold_max_ms": 1200,
       "no_speech_timeout_s": 8, "max_utterance_s": 20}
OPEN = {"hazaar", "aur"}


def words(*tokens: str, t0: int = 100) -> list[Word]:
    return [Word(w=t, t0=t0 + 300 * i, t1=t0 + 300 * i + 250, conf=0.9, lang="hi") for i, t in enumerate(tokens)]


def ev(kind: str, ws: list[Word], status: str = "OK") -> PrismaStream:
    return PrismaStream(session_id="s", kind=kind, words=ws, text=" ".join(w.w for w in ws), lang="hi",  # type: ignore[arg-type]
                        t0_ms=int(ws[0].t0) if ws else 0, t1_ms=int(ws[-1].t1) if ws else 0, status=status)


def run(ep: Endpointer, until_ms: int, *, speech_until: int, events: dict[int, PrismaStream], noisy: bool = False, resume: tuple[int, int] | None = None) -> tuple[int, Utterance] | None:
    """Drive frames; speech between 0 and `speech_until` (and the `resume` span); events are delivered when the audio clock reaches their key."""
    silence = 0
    for now in range(20, until_ms + 1, 20):
        if now in events:
            ep.on_event(events[now])
        talking = now <= speech_until or noisy or (resume is not None and resume[0] <= now <= resume[1])
        silence = 0 if talking else silence + 20
        if (utt := ep.on_frame(now, talking, silence)) is not None:
            return now, utt
    return None


def endpointer() -> Endpointer:
    return Endpointer(CFG, "hi", lambda ws, lang: ws[-1].w in OPEN)


def test_ends_after_350ms_of_silence_with_a_final() -> None:
    ws = words("sharma", "ji", "ka")
    got = run(endpointer(), 3000, speech_until=1000, events={1100: ev("final", ws)})
    assert got is not None
    now, utt = got
    assert utt.status == "OK" and [w.w for w in utt.words] == ["sharma", "ji", "ka"]
    assert now == 1000 + 360  # the first frame at which the line has been silent for 350 ms


def test_waits_for_the_final_even_when_the_line_is_silent() -> None:
    ws = words("sharma", "ji")
    got = run(endpointer(), 6000, speech_until=800, events={900: ev("partial", ws)})
    assert got is not None and got[1].status == "TIMEOUT"
    assert got[0] >= ws[-1].t1 + 2500


def test_numeric_tail_holds_the_end_of_utterance() -> None:
    first, second = words("sharma", "ji", "pachaas", "hazaar"), words("paanch", "sau", t0=2300)
    ep = endpointer()
    got = run(ep, 6000, speech_until=1300, resume=(2000, 2900),
              events={1500: ev("final", first), 2860: ev("partial", second), 3160: ev("final", second)})
    assert got is not None
    assert [w.w for w in got[1].words] == ["sharma", "ji", "pachaas", "hazaar", "paanch", "sau"]
    assert got[0] > 2900 + 350  # one turn: the pause after "hazaar" was shorter than 350 + 600 ms


def test_without_the_hold_the_number_is_cut() -> None:
    first = words("pachaas", "hazaar")
    ep = Endpointer({**CFG, "numeric_hold": False}, "hi", lambda ws, lang: True)
    got = run(ep, 6000, speech_until=900, resume=(1800, 2400), events={1000: ev("final", first)})
    assert got is not None and [w.w for w in got[1].words] == ["pachaas", "hazaar"]


def test_hold_is_capped_per_turn() -> None:
    ep = Endpointer(CFG, "hi", lambda ws, lang: True)  # every tail looks open
    got = run(ep, 9000, speech_until=900, events={1000: ev("final", words("aur"))})
    assert got is not None
    assert 900 + 350 + 600 <= got[0] <= 900 + 350 + 600 + 40  # one hold of 600 ms, then the plain rule applies again after the cap is reached


def test_silence_after_8_seconds() -> None:
    got = run(endpointer(), 9000, speech_until=0, events={})
    assert got is not None and got[1].status == "SILENCE" and got[0] == 8000


def test_recogniser_failure_travels_as_a_status() -> None:
    err = PrismaStream(session_id="s", kind="error", words=[], text="", lang="hi", t0_ms=0, t1_ms=0, status="UNAVAILABLE")
    got = run(endpointer(), 3000, speech_until=500, events={100: err})
    assert got is not None and got[1].status == "UNAVAILABLE" and got[1].words == []


def test_empty_final_is_an_empty_turn() -> None:
    empty = PrismaStream(session_id="s", kind="final", words=[], text="", lang="hi", t0_ms=900, t1_ms=900, status="EMPTY")
    got = run(endpointer(), 3000, speech_until=500, events={900: empty})
    assert got is not None and got[1].status == "EMPTY"


def test_noisy_line_ends_on_the_streams_own_quiet() -> None:
    ws = words("ramesh", "ka", "paanch", "sau")  # webrtcvad calls the whole line speech
    got = run(endpointer(), 6000, speech_until=0, noisy=True, events={1500: ev("final", ws)})
    assert got is not None and got[1].status == "OK"
    assert got[0] >= ws[-1].t1 + 350 + 650


def test_long_utterance_is_cut_at_20_seconds() -> None:
    talk = {t: ev("partial", [Word(w="haan", t0=t - 250, t1=t, conf=0.9, lang="hi")]) for t in range(500, 25000, 300)}  # the recogniser keeps hearing words
    got = run(endpointer(), 25000, speech_until=25000, events=talk)
    assert got is not None and got[1].long and got[0] == 20020


def test_words_of_the_previous_turn_are_dropped() -> None:
    ep = endpointer()
    ep.reset(2000)
    ep.on_event(ev("final", words("purana", t0=1000)))
    ep.on_event(ev("final", words("naya", t0=2500)))
    assert [w.w for w in ep.words()] == ["naya"]
