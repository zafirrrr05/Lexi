"""Structured JSON logs with trace_id, plus in-process counters and latency samples for /metrics (13)."""

import json
import logging
import sys
from collections import defaultdict, deque
from contextvars import ContextVar

trace_var: ContextVar[str] = ContextVar("trace_id", default="")

_counters: dict[str, int] = defaultdict(int)
_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=5000))


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name,
               "trace_id": trace_var.get(), "msg": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger("lexi")
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"lexi.{name}")


def inc(name: str, n: int = 1) -> None:
    _counters[name] += n


def observe(name: str, value: float) -> None:
    _samples[name].append(value)


def metrics_snapshot() -> dict[str, object]:
    def pct(xs: list[float], q: float) -> float:
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    lat = {}
    for k, v in _samples.items():
        xs = sorted(v)
        lat[k] = {"n": len(xs), "p50": pct(xs, 0.5), "p95": pct(xs, 0.95), "p99": pct(xs, 0.99)}
    return {"counters": dict(_counters), "latency_ms": lat}
