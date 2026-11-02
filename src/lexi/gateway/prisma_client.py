"""The call's speech-recognition stream (C1 steps 2, FAILS): frames in, Prisma events out. A dropped stream is reopened once per turn and the
turn's audio so far is sent again; a second failure reaches the endpointer as an error event and the turn ends UNAVAILABLE. A transcript is never made up."""

from lexi.adapters.prisma import PrismaClient, PrismaSession
from lexi.common.errors import ProviderError
from lexi.common.types import PrismaStream


class AsrStream:
    def __init__(self, client: PrismaClient, session_id: str, lang: str | None) -> None:
        self._client, self._id, self._lang = client, session_id, lang
        self._s: PrismaSession | None = None
        self._turn = bytearray()  # audio of the current turn, sent again after a reconnect
        self._turn_from = 0
        self._retried = False
        self._failures: list[PrismaStream] = []
        self._spent = 0.0

    async def start(self) -> None:
        self._s = await self._client.open(self._id, self._lang)

    @property
    def credits_used(self) -> float:
        return self._spent + (self._s.credits_used if self._s else 0.0)

    def announce(self, utt_id: int, clip_start_ms: int) -> None:
        """Sim only (docs/DECISIONS.md D-002): the Twin says which clip starts where."""
        assert self._s is not None
        self._s.announce(utt_id, clip_start_ms)

    async def feed(self, pcm: bytes, ts_ms: int) -> None:
        assert self._s is not None
        if not self._turn:
            self._turn_from = ts_ms
        self._turn += pcm
        try:
            await self._s.feed(pcm, ts_ms)
        except ProviderError:
            self._failures.append(self._error())

    async def poll(self) -> list[PrismaStream]:
        assert self._s is not None
        events, self._failures = [*self._s.poll(), *self._failures], []
        if not any(e.kind == "error" for e in events):
            return events
        if self._retried:
            return events
        self._retried = True
        self._spent += self._s.credits_used
        await self._s.close()
        self._s = await self._client.open(self._id, self._lang)
        try:
            await self._s.feed(bytes(self._turn), self._turn_from)
        except ProviderError:
            self._failures.append(self._error())
        return [e for e in events if e.kind != "error"] + self._s.poll()

    def turn_done(self) -> None:
        """The turn ended: its audio is no longer needed, and the stream may be reopened again in the next one."""
        self._turn.clear()
        self._retried = False

    async def close(self) -> None:
        if self._s is not None:
            await self._s.close()

    def _error(self) -> PrismaStream:
        return PrismaStream(session_id=self._id, kind="error", words=[], text="", lang=self._lang or "hi", t0_ms=self._turn_from, t1_ms=self._turn_from,
                            status="UNAVAILABLE")
