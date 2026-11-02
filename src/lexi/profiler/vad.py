"""Voice activity (C2): webrtcvad mode 3 on 20 ms frames of 8 kHz s16le."""

import webrtcvad

FRAME_BYTES = 320  # 20 ms at 8 kHz, 16 bit
FRAME_MS = 20


class Vad:
    def __init__(self) -> None:
        self._vad = webrtcvad.Vad(3)

    def flags(self, pcm: bytes) -> list[bool]:
        return [self._vad.is_speech(pcm[i:i + FRAME_BYTES], 8000) for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]
