"""C12 Decision Audit Log: tamper-evident, group-committed, acknowledged only after fsync."""

import asyncio
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from lexi.audit.chain import GENESIS, seal
from lexi.audit.segments import repair_tail, segment_name
from lexi.audit.verify import IndexEntry, VerifyResult, verify_dir
from lexi.common.clock import Clock
from lexi.common.errors import AuditError
from lexi.common.logging import get_logger, observe
from lexi.common.types import AuditRecord, HealthSignal

log = get_logger("audit")


class Ack(NamedTuple):
    seq: int
    hash: str


class _Pending(NamedTuple):
    type: str
    payload: dict[str, Any]
    trace_id: str
    ts: float
    fut: "asyncio.Future[Ack]"


class AuditLog:
    def __init__(self, directory: Path, clock: Clock, *, group_commit_ms: float = 2, group_commit_max: int = 16,
                 segment_records: int = 50000, on_health: Callable[[HealthSignal], None] | None = None) -> None:
        self.dir, self.clock = directory, clock
        self._gc_s, self._gc_max, self._seg_max = group_commit_ms / 1000, group_commit_max, segment_records
        self._on_health = on_health
        self._index: list[IndexEntry] = []
        self._head = Ack(0, GENESIS)
        self._subs: list[Callable[[AuditRecord], None]] = []
        self._q: asyncio.Queue[_Pending] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._file: Any = None
        self._seg_count = 0
        self._tail_path: Path | None = None
        self._healthy = True

    async def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        repair_tail(self.dir)
        self._index.clear()
        res = verify_dir(self.dir, self._index)
        if not res.ok:
            raise AuditError(f"audit chain broken at seq {res.first_bad_seq}")
        self._head = Ack(res.n_records, self._index[-1].hash if self._index else GENESIS)
        self._tail_path = self._index[-1].path if self._index else None
        self._seg_count = sum(1 for e in self._index if e.path == self._tail_path)
        self._task = asyncio.create_task(self._run())

    @property
    def head(self) -> Ack:
        return self._head

    def subscribe(self, cb: Callable[[AuditRecord], None]) -> None:
        self._subs.append(cb)

    async def append(self, type: str, payload: dict[str, Any], trace_id: str = "") -> Ack:
        fut: asyncio.Future[Ack] = asyncio.get_running_loop().create_future()
        await self._q.put(_Pending(type, payload, trace_id, self.clock.now(), fut))
        return await fut

    def has(self, seq: int, hash: str) -> bool:
        return 1 <= seq <= len(self._index) and self._index[seq - 1].hash == hash

    def range(self, start: int, stop: int | None = None) -> Iterator[AuditRecord]:
        for entry in self._index[max(start, 1) - 1: stop]:
            with open(entry.path, "rb") as f:
                f.seek(entry.offset)
                yield AuditRecord(**json.loads(f.readline()))

    def verify(self) -> VerifyResult:
        return verify_dir(self.dir)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._file:
            self._file.close()
            self._file = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch = [await self._q.get()]
            deadline = loop.time() + self._gc_s
            while len(batch) < self._gc_max and (left := deadline - loop.time()) > 0:
                try:
                    batch.append(await asyncio.wait_for(self._q.get(), left))
                except TimeoutError:
                    break
            await self._commit(batch)

    async def _commit(self, batch: list[_Pending]) -> None:
        t0 = time.perf_counter()
        recs, prev = [], self._head
        for p in batch:
            rec = seal(prev.seq + 1, prev.hash, p.type, p.payload, p.trace_id, p.ts)
            recs.append(rec)
            prev = Ack(rec.seq, rec.hash)
        lines = [json.dumps(r.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode() + b"\n" for r in recs]
        try:
            offsets = await asyncio.to_thread(self._write, recs, lines)
        except OSError as e:
            self._set_health(False, f"append failed: {e}")
            err = AuditError(f"audit append failed: {e}")
            for p in batch:
                if not p.fut.done():  # a caller that was cancelled while waiting no longer needs the answer
                    p.fut.set_exception(err)
            return
        self._set_health(True, "")
        self._index.extend(IndexEntry(*o, r.hash) for o, r in zip(offsets, recs, strict=True))
        self._head = prev
        observe("audit_append_ms", (time.perf_counter() - t0) * 1000)
        for p, r in zip(batch, recs, strict=True):
            if not p.fut.done():
                p.fut.set_result(Ack(r.seq, r.hash))
        for r in recs:
            for cb in self._subs:
                try:
                    cb(r)
                except Exception:
                    log.exception("audit subscriber failed")

    def _write(self, recs: list[AuditRecord], lines: list[bytes]) -> list[tuple[Path, int]]:
        """Runs in a worker thread: one write + one fsync per batch (group commit)."""
        offsets: list[tuple[Path, int]] = []
        count0, size0 = self._seg_count, None
        try:
            for rec, line in zip(recs, lines, strict=True):
                if self._file is None or self._seg_count >= self._seg_max:
                    self._open_segment(rec.seq)
                    count0, size0 = self._seg_count, self._file.tell()
                elif size0 is None:
                    size0 = self._file.tell()
                offsets.append((Path(self._file.name), self._file.tell()))
                self._file.write(line)
                self._seg_count += 1
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError:
            self._abandon(size0, count0)
            raise
        return offsets

    def _open_segment(self, first_seq: int) -> None:
        if self._file:
            self._file.close()
            self._file = None
        if self._tail_path and self._seg_count < self._seg_max:  # resume the last segment after a restart
            path = self._tail_path
        else:
            path, self._seg_count = self.dir / segment_name(first_seq), 0
        self._tail_path = None
        self._file = open(path, "ab")

    def _abandon(self, size: int | None, count: int) -> None:
        """Unacknowledged bytes must not stay on disk, or the next append would collide with their seq."""
        self._seg_count = count
        if self._file is not None:
            path = Path(self._file.name)
            try:
                self._file.close()
                if size is not None:
                    os.truncate(path, size)
            except OSError:
                log.exception("audit rollback failed")
            self._file = None
            self._tail_path = path

    def _set_health(self, healthy: bool, reason: str) -> None:
        if healthy != self._healthy and self._on_health:
            self._on_health(HealthSignal(component="C12", healthy=healthy, since_ts=self.clock.now(), reason=reason))
        self._healthy = healthy
