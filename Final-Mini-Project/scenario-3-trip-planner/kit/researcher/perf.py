"""Per-request performance trace: where a planning request spent its time. Measurement only, it changes nothing.

One Trace per request, held in a ContextVar, so concurrent requests never mix. Worker threads must be started with
contextvars.copy_context() to report to it (planning/evidence.py and planning/verification.py do). No prompt, argument
or result is kept: only names, durations and counts. summary() goes into ChatResponse.performance.trace and the log.
"""
from __future__ import annotations

import contextvars
import threading
import time
from collections import Counter
from contextlib import contextmanager
from typing import Iterator

_TRACE: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar("rihla_perf_trace", default=None)


class Trace:
    def __init__(self):
        self.started = time.perf_counter()
        self._lock = threading.Lock()
        self.llm: list[tuple[str, int, bool]] = []  # (stage, ms, ok)
        self.tools: list[tuple[str, str, int, bool]] = []  # (tool, stage, ms, ok)
        self.counts: Counter = Counter()  # retries, cache hits, timeouts

    def add_llm(self, stage: str | None, ms: int, ok: bool) -> None:
        with self._lock:
            self.llm.append((stage or "other", ms, ok))

    def add_tool(self, tool: str, stage: str | None, ms: int, ok: bool) -> None:
        with self._lock:
            self.tools.append((tool, stage or "other", ms, ok))

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counts[name] += n

    def summary(self) -> dict:
        with self._lock:
            llm, tools, counts = list(self.llm), list(self.tools), dict(self.counts)
        by_stage: dict[str, dict] = {}
        for stage, ms, ok in llm:
            row = by_stage.setdefault(stage, {"calls": 0, "ms": 0, "max_ms": 0, "failed": 0})
            row["calls"] += 1
            row["ms"] += ms
            row["max_ms"] = max(row["max_ms"], ms)
            row["failed"] += 0 if ok else 1
        by_tool: dict[str, dict] = {}
        for tool, _stage, ms, ok in tools:
            row = by_tool.setdefault(tool, {"calls": 0, "ms": 0, "max_ms": 0, "failed": 0})
            row["calls"] += 1
            row["ms"] += ms
            row["max_ms"] = max(row["max_ms"], ms)
            row["failed"] += 0 if ok else 1
        return {"total_ms": round((time.perf_counter() - self.started) * 1000),
                "llm": {"calls": len(llm), "ms": sum(x[1] for x in llm), "by_stage": by_stage},
                "mcp": {"calls": len(tools), "ms": sum(x[2] for x in tools), "by_tool": by_tool},
                "counts": counts}


@contextmanager
def tracing() -> Iterator[Trace]:
    trace = Trace()
    token = _TRACE.set(trace)
    try:
        yield trace
    finally:
        _TRACE.reset(token)


def current() -> Trace | None:
    return _TRACE.get()


def count(name: str, n: int = 1) -> None:
    trace = _TRACE.get()
    if trace is not None:
        trace.count(name, n)
