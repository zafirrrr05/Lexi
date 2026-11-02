"""C1 ingress: WebSocket /call (s16le 8 kHz mono, 20 ms frames in binary messages, JSON in text messages) and the session table.
The route is mounted by the composition root on the same FastAPI app as the console (6.2); the Twin and the browser softphone dial the same URL."""

from fastapi import APIRouter, WebSocket

from lexi.common.errors import LexiError
from lexi.common.logging import get_logger
from lexi.common.types import LedgerView, SpeechPlan, TurnInput
from lexi.gateway.session import Ports, Session

log = get_logger("gateway")


class Gateway:
    def __init__(self, ports: Ports) -> None:
        self.p = ports
        self.sessions: dict[str, Session] = {}

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.websocket("/call")
        async def call(ws: WebSocket, session_id: str, account: str, lang: str = "hi", client: str = "twin", collect: bool = False) -> None:
            await ws.accept()
            if len(self.sessions) >= self.p.cfg["max_sessions"] or session_id in self.sessions:
                await ws.close(1013)
                return
            s = Session(ws, session_id, account, lang, client == "softphone", self.p, collect=collect)
            self.sessions[session_id] = s
            try:
                await s.open()
            except LexiError as e:  # an unknown account, or a recogniser that cannot be opened: nothing was said, nothing was done
                log.warning("session %s refused: %s", session_id, e)
                await s.close()
            else:
                await s.run()
            finally:
                self.sessions.pop(session_id, None)

        return r

    async def speak(self, session_id: str, plan: SpeechPlan) -> None:
        """H21. A call that has already hung up has nobody to speak to; the ledger compensates what was never delivered (D9)."""
        if (s := self.sessions.get(session_id)) is not None:
            await s.speak(plan)

    def on_view(self, session_id: str, view: LedgerView) -> None:
        """H17."""
        if (s := self.sessions.get(session_id)) is not None:
            s.on_view(view)

    def renew(self, turn: TurnInput) -> TurnInput:
        """C8 step 3: the turn that was not an undo is a new command."""
        return self.sessions[turn.session_id].renew(turn)
